"""Read-only веб-дашборд: ФК, БХМ, расстойка, КЦ и общий Гант. В базы ботов только чтение, никаких записей."""
import asyncio
import base64
import binascii
import hmac
import io
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from aiohttp import web

import aggregate
import exports
import gantt
import gantt_exports
import product_map
import kc_aggregate
import kc_exports
import plans as plan_store
import proofing_aggregate
import proofing_exports
import proofing_source as proofing_mod
from bhm_source import BhmSource
from kc_source import KcSource
from proofing_source import PhotoNotFound, ProofingSource
from sources import TZ, DataSource

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fk.dashboard")

PORT = int(os.getenv("PORT", "8090"))
RELAXDEV_DEPLOY = os.getenv("RELAXDEV_DEPLOY", "") == "1"
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "").strip()
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
STATIC = Path(__file__).parent / "static"
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "60"))
# Даты, которые дашборд не показывает. База при этом не трогается — фильтр только на чтении.
EXCLUDE_DATES = tuple(d.strip() for d in os.getenv("EXCLUDE_DATES", "").split(",") if d.strip())
BHM_EXCLUDE_DATES = tuple(d.strip() for d in os.getenv("BHM_EXCLUDE_DATES", "").split(",") if d.strip())
PROOFING_EXCLUDE_DATES = tuple(d.strip() for d in os.getenv("PROOFING_EXCLUDE_DATES", "").split(",") if d.strip())
KC_EXCLUDE_DATES = tuple(d.strip() for d in os.getenv("KC_EXCLUDE_DATES", "").split(",") if d.strip())
source = DataSource()
bhm_source = BhmSource()
proofing_source = ProofingSource()
kc_source = KcSource()

# Источники дашборда. ФК — по умолчанию: старые ссылки без ?source= работают как раньше.
# У БХМ своя единица (кг) и нет загрузки плана — вкладки плана для него скрыты.
SOURCES = {
    "fk": {"title": "ФК", "data": source, "exclude": EXCLUDE_DATES, "plans": True,
           "units": {"qty": "шт", "rate": "шт/ч"}, "file": "fk"},
    "bhm": {"title": "БХМ", "data": bhm_source, "exclude": BHM_EXCLUDE_DATES, "plans": False,
            "units": {"qty": "кг", "rate": "кг/ч"}, "file": "bhm"},
    # Расстойка — партии из двух этапов (расстойка → отпекание): свои расчёты
    # (proofing_aggregate) и выгрузки (proofing_exports). Плана у неё нет, пока не
    # появится согласованный источник планов расстойки.
    "proofing": {"title": "Расстойка", "data": proofing_source, "exclude": PROOFING_EXCLUDE_DATES, "plans": False,
                 "units": {"qty": "шт", "rate": "шт/ч"}, "file": "proofing", "kind": "proofing"},
    # КЦ — самостоятельные операции без маршрута: свои расчёты (kc_aggregate) и выгрузки
    # (kc_exports). Штуки операций не складываются в готовый выпуск. Плана нет.
    "kc": {"title": "КЦ", "data": kc_source, "exclude": KC_EXCLUDE_DATES, "plans": False,
           "units": {"qty": "шт", "rate": "шт/чел·ч"}, "file": "kc", "kind": "kc"},
    # Гант — не отдельный бот, а сводка всех цехов на одной шкале времени + общий тайминг.
    "gantt": {"title": "Гант", "data": None, "exclude": (), "plans": False,
              "units": {"qty": "шт", "rate": "шт/ч"}, "file": "gantt", "kind": "gantt"},
}
# Разобранный вручную чат КЦ (до запуска бота) — только для Ганта, читается один раз.
KC_CHAT_HISTORY = gantt.load_history()


def pick_source(request):
    key = request.query.get("source", "fk").strip().lower() or "fk"
    if key not in SOURCES:
        raise web.HTTPBadRequest(content_type="application/json", text=json.dumps(
            {"ok": False, "error": "неизвестный источник: " + re.sub(r"[^a-z0-9_-]", "", key)[:16]}, ensure_ascii=False))
    if key == "bhm" and not bhm_source.enabled:
        raise web.HTTPServiceUnavailable(content_type="application/json", text=json.dumps(
            {"ok": False, "error": "источник БХМ не настроен: задай BHM_API_URL и BHM_API_KEY"}, ensure_ascii=False))
    if key == "proofing" and not proofing_source.enabled:
        raise web.HTTPServiceUnavailable(content_type="application/json", text=json.dumps(
            {"ok": False, "error": "раздел «Расстойка» не настроен: задай PROOFING_API_URL и PROOFING_API_KEY"},
            ensure_ascii=False))
    if key == "kc" and not kc_source.enabled:
        raise web.HTTPServiceUnavailable(content_type="application/json", text=json.dumps(
            {"ok": False, "error": "раздел «КЦ» не настроен: задай KC_API_URL и KC_API_KEY"}, ensure_ascii=False))
    return key, SOURCES[key]


def sources_state():
    return {"fk": True, "bhm": bhm_source.enabled, "proofing": proofing_source.enabled, "kc": kc_source.enabled,
            "gantt": True}


async def gantt_snapshots(force=False):
    """Снимки всех цехов для Ганта. Недоступный цех не роняет Гант — он в section_errors."""
    jobs = {"fk": source, "bhm": bhm_source, "proofing": proofing_source, "kc": kc_source}
    snaps, errors = {}, {}
    names = [k for k, src in jobs.items() if getattr(src, "enabled", True)]
    for k in jobs:
        if k not in names:
            errors[k] = "источник не настроен"
    results = await asyncio.gather(*(jobs[k].get(force=force) for k in names), return_exceptions=True)
    for k, res in zip(names, results):
        if isinstance(res, Exception):
            errors[k] = f"{type(res).__name__}: {res}"
        else:
            snaps[k] = res
            err = jobs[k].describe().get("error")
            if err:
                errors[k] = "данные из кеша: " + err
    return snaps, errors


async def gantt_data(force=False):
    snaps, errors = await gantt_snapshots(force)
    bars = gantt.collect_bars(snaps, KC_CHAT_HISTORY)
    stored = await asyncio.to_thread(product_map.load)
    return bars, stored, errors


async def kc_snapshot(force=False):
    """Снимок КЦ или понятная 503 — только для раздела КЦ, остальные разделы не задеты."""
    try:
        return await kc_source.get(force=force)
    except Exception as exc:
        raise web.HTTPServiceUnavailable(content_type="application/json", text=json.dumps(
            {"ok": False, "source_key": "kc",
             "error": f"API кондитерского цеха недоступно: {exc}. Проверь KC_API_URL, KC_API_KEY и работу бота КЦ."},
            ensure_ascii=False))


async def proofing_snapshot(force=False):
    """Снимок расстойки или понятная 503 — только для раздела расстойки, ФК и БХМ не задеты."""
    try:
        return await proofing_source.get(force=force)
    except Exception as exc:
        raise web.HTTPServiceUnavailable(content_type="application/json", text=json.dumps(
            {"ok": False, "source_key": "proofing",
             "error": f"API расстойки недоступно: {exc}. Проверь PROOFING_API_URL, PROOFING_API_KEY и работу бота расстойки."},
            ensure_ascii=False))


def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


async def index(request):
    return no_store(web.FileResponse(STATIC / "index.html"))


async def health(request):
    """200, пока сервис способен отдать данные (пусть и из кеша). 503 — если снимка нет вовсе."""
    try:
        await asyncio.to_thread(plan_store.health_storage)
        await source.get()
    except Exception as exc:
        return web.json_response({"ok": False, "error": f"{type(exc).__name__}: {exc}", **source.describe()}, status=503)
    # БХМ и расстойка — дополнительные источники: их недоступность не делает контейнер нездоровым.
    return web.json_response({"ok": True, **source.describe(), "bhm": bhm_source.describe(),
                              "proofing": proofing_source.describe(), "kc": kc_source.describe()})


async def meta(request):
    key, src = pick_source(request)
    if src.get("kind") == "proofing":
        snapshot = await proofing_snapshot()
        return no_store(web.json_response({
            "ok": True, "kind": "proofing", "timezone": str(TZ), "refresh_seconds": REFRESH_SECONDS,
            "source": proofing_source.describe(), "source_key": key, "sources": sources_state(),
            "units": src["units"], "plan_enabled": False, "plan_upload_enabled": False,
            **proofing_aggregate.build_meta(snapshot, src["exclude"]),
        }))
    if src.get("kind") == "gantt":
        bars, stored, errors = await gantt_data()
        return no_store(web.json_response({
            "ok": True, "kind": "gantt", "timezone": str(TZ), "refresh_seconds": REFRESH_SECONDS,
            "source": {"mode": "gantt"}, "source_key": key, "sources": sources_state(), "units": src["units"],
            "plan_enabled": False, "plan_upload_enabled": bool(plan_store.PLAN_UPLOAD_TOKEN),
            "equipment": [], "employees": [],
            **gantt.meta(bars, KC_CHAT_HISTORY, stored["map"], errors),
        }))
    if src.get("kind") == "kc":
        snapshot = await kc_snapshot()
        return no_store(web.json_response({
            "ok": True, "kind": "kc", "timezone": str(TZ), "refresh_seconds": REFRESH_SECONDS,
            "source": kc_source.describe(), "source_key": key, "sources": sources_state(),
            "units": src["units"], "plan_enabled": False, "plan_upload_enabled": False,
            **kc_aggregate.build_meta(snapshot, src["exclude"]),
        }))
    snapshot = await src["data"].get()
    return no_store(web.json_response({
        "ok": True,
        "timezone": str(TZ),
        "refresh_seconds": REFRESH_SECONDS,
        "source": src["data"].describe(),
        "source_key": key,
        "sources": sources_state(),
        "units": src["units"],
        "plan_enabled": src["plans"],
        "plan_upload_enabled": bool(plan_store.PLAN_UPLOAD_TOKEN) and src["plans"],
        **aggregate.build_meta(snapshot, src["exclude"]),
    }))


def plan_auth(request):
    """Загрузка планов включается только заданным PLAN_UPLOAD_TOKEN — дашборд публичный."""
    if not plan_store.PLAN_UPLOAD_TOKEN:
        return False
    supplied = (request.headers.get("X-Plan-Token", "")
                or request.query.get("token", "")).strip()
    return bool(supplied) and hmac.compare_digest(supplied, plan_store.PLAN_UPLOAD_TOKEN)


async def plan_upload(request):
    if not plan_store.PLAN_UPLOAD_TOKEN:
        return web.json_response(
            {"ok": False, "error": "загрузка планов выключена: не задан PLAN_UPLOAD_TOKEN"}, status=403)
    if not plan_auth(request):
        return web.json_response({"ok": False, "error": "неверный ключ загрузки"}, status=401)

    plan_date = request.query.get("date", "").strip()
    reader = await request.multipart()
    field = await reader.next()
    while field is not None and field.name != "file":
        if field.name == "date":
            plan_date = (await field.text()).strip()
        field = await reader.next()
    if field is None:
        return web.json_response({"ok": False, "error": "файл не передан"}, status=400)

    buffer = io.BytesIO()
    size = 0
    while True:
        chunk = await field.read_chunk()
        if not chunk:
            break
        size += len(chunk)
        if size > plan_store.MAX_UPLOAD_BYTES:
            return web.json_response(
                {"ok": False, "error": f"файл больше {plan_store.MAX_UPLOAD_BYTES // 1024 // 1024} МБ"}, status=413)
        buffer.write(chunk)
    buffer.seek(0)

    filename = field.filename or ""
    try:
        parsed = await asyncio.to_thread(plan_store.parse_workbook, buffer, plan_date)
    except plan_store.PlanError as exc:
        # Неудачную попытку тоже пишем в журнал — иначе непонятно, почему плана нет.
        await asyncio.to_thread(plan_store.log_upload, {
            "action": "upload", "ok": False, "file": filename, "size": size,
            "dates": [], "positions": 0, "error": str(exc)})
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    stored, replaced = await asyncio.to_thread(plan_store.save, parsed, filename)
    positions = sum(len(v) for v in parsed.values())
    quantity = sum(sum(v.values()) for v in parsed.values())
    await asyncio.to_thread(plan_store.log_upload, {
        "action": "upload", "ok": True, "file": filename, "size": size,
        "dates": sorted(parsed), "positions": positions, "quantity": quantity,
        "replaced": sorted(replaced), "error": ""})
    log.info("plan uploaded: file=%s dates=%s positions=%s", filename, sorted(parsed), positions)
    return no_store(web.json_response({
        "ok": True,
        "loaded_dates": sorted(parsed),
        "loaded_positions": positions,
        "loaded_quantity": quantity,
        "replaced_dates": sorted(replaced),
        "stored_dates": sorted(stored),
    }))


async def plan_state(request):
    stored = await asyncio.to_thread(plan_store.load)
    return no_store(web.json_response({
        "ok": True,
        "upload_enabled": bool(plan_store.PLAN_UPLOAD_TOKEN),
        "dates": sorted(stored),
        "positions": sum(len(v) for v in stored.values()),
    }))


async def plan_history(request):
    entries = await asyncio.to_thread(plan_store.history)
    return no_store(web.json_response({"ok": True, "entries": entries}))


async def plan_delete(request):
    if not plan_auth(request):
        return web.json_response({"ok": False, "error": "неверный ключ загрузки"}, status=401)
    dates = [d for d in request.query.get("dates", "").split(",") if d.strip()]
    if not dates:
        return web.json_response({"ok": False, "error": "не переданы даты"}, status=400)
    stored = await asyncio.to_thread(plan_store.delete_dates, dates)
    return no_store(web.json_response({"ok": True, "stored_dates": sorted(stored)}))


SHIFT_LABELS = {"all": "все смены", "day": "дневная (08:00–20:00)", "night": "ночная (20:00–08:00)"}


PROOFING_STATUS_LABELS = {"all": "все", **proofing_aggregate.STATUS_NAMES}


async def collect_proofing(request, key, src):
    """Расстойка: одна выборка и один расчёт для страницы, Excel и PDF."""
    q = request.query
    args = {name: q.get(name, default).strip() for name, default in (
        ("date_from", ""), ("date_to", ""), ("product", "all"), ("equipment", "all"), ("oven", "all"),
        ("employee", "all"), ("shift", "all"), ("hour", ""), ("status", "all"))}
    snapshot = await proofing_snapshot(force=q.get("refresh") == "1")
    payload = proofing_aggregate.build_summary(snapshot, args, exclude_dates=src["exclude"], now=datetime.now(TZ),
                                               max_stage_hours=proofing_mod.MAX_STAGE_HOURS)
    payload.update(source=proofing_source.describe(), source_key=key, source_title=src["title"], units=src["units"],
                   plan_enabled=False, excluded_dates=list(src["exclude"]))
    f = payload["filters_applied"]
    ovens = {o["code"]: o["name"] for o in snapshot["ovens"]}

    def named(value):
        return "все" if value in ("all", "") else value

    filters = [
        ("Источник", src["title"]),
        ("Период", f"{f['date_from'] or 'начало'} – {f['date_to'] or 'сегодня'}"),
        ("Изделие", named(f["product"])),
        ("Оборудование", named(f["equipment"])),
        ("Тип печи", "все" if f["oven"] == "all" else ovens.get(f["oven"], f["oven"])),
        ("Сотрудник", named(f["employee"])),
        ("Смена", SHIFT_LABELS.get(f["shift"], f["shift"])),
        ("Час", f"{f['hour']:02d}:00" if f["hour"] is not None else "все"),
        ("Статус партии", PROOFING_STATUS_LABELS.get(f["status"], f["status"])),
        ("Скрытые даты", ", ".join(src["exclude"]) or "нет"),
    ]
    stamp = {**args, "hour": "" if f["hour"] is None else str(f["hour"]), "shift": f["shift"], "source": key}
    return payload, filters, stamp


KC_STATUS_LABELS = {"all": "все", **kc_aggregate.STATUS_NAMES}


async def collect_kc(request, key, src):
    """КЦ: одна выборка и один расчёт для страницы, Excel и PDF."""
    q = request.query
    args = {name: q.get(name, default).strip() for name, default in (
        ("date_from", ""), ("date_to", ""), ("product", "all"), ("operation", "all"), ("employee", "all"),
        ("shift", "all"), ("hour", ""), ("status", "all"))}
    snapshot = await kc_snapshot(force=q.get("refresh") == "1")
    payload = kc_aggregate.build_summary(snapshot, args, exclude_dates=src["exclude"], now=datetime.now(TZ))
    payload.update(source=kc_source.describe(), source_key=key, source_title=src["title"], units=src["units"],
                   plan_enabled=False, excluded_dates=list(src["exclude"]))
    f = payload["filters_applied"]

    def named(value):
        return "все" if value in ("all", "") else value

    filters = [
        ("Источник", "КЦ — кондитерский цех"),
        ("Период", f"{f['date_from'] or 'начало'} – {f['date_to'] or 'сегодня'}"),
        ("Продукция", named(f["product"])),
        ("Операция", named(f["operation"])),
        ("Сотрудник", named(f["employee"])),
        ("Смена", SHIFT_LABELS.get(f["shift"], f["shift"])),
        ("Час", f"{f['hour']:02d}:00" if f["hour"] is not None else "все"),
        ("Статус операции", KC_STATUS_LABELS.get(f["status"], f["status"])),
        ("Скрытые даты", ", ".join(src["exclude"]) or "нет"),
    ]
    stamp = {**args, "equipment": "all", "hour": "" if f["hour"] is None else str(f["hour"]), "shift": f["shift"],
             "source": key}
    return payload, filters, stamp


GANTT_SECTION_TITLES = dict(product_map.SECTIONS)


async def collect_gantt(request, key, src):
    """Гант: одна выборка и один расчёт для страницы, Excel и PDF."""
    q = request.query
    bars, stored, errors = await gantt_data(force=q.get("refresh") == "1")
    days = gantt.available_days(bars)
    day = (q.get("date") or "").strip()
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        day = days[-1] if days else datetime.now(TZ).date().isoformat()
    mode = (q.get("window") or "full").strip()
    mode = mode if mode in gantt.WINDOWS else "full"
    sections = [x for x in (q.get("sections") or "").split(",") if x in product_map.SECTIONS] or list(product_map.SECTIONS)
    product = (q.get("product") or "all").strip() or "all"
    equipment = (q.get("equipment") or "all").strip() or "all"
    payload = gantt.build(bars, KC_CHAT_HISTORY, stored["map"], day, mode, sections, product, equipment,
                          now=datetime.now(TZ), section_errors=errors)
    payload.update(source={"mode": "gantt"}, source_key=key, source_title=src["title"], units=src["units"],
                   plan_enabled=False, excluded_dates=[], map_updated=stored.get("updated_at", ""),
                   map_size=sum(len(v) for v in stored["map"].values()))
    filters = [
        ("Раздел", "Гант — все цеха"),
        ("Дата (рабочие сутки)", day),
        ("Окно", gantt.WINDOWS[mode][0]),
        ("Цеха", ", ".join(GANTT_SECTION_TITLES[s] for s in sections)),
        ("Продукт (единый)", "все" if product == "all" else product),
        ("Оборудование", "все" if equipment == "all" else equipment),
    ]
    stamp = {"date_from": day, "date_to": mode, "equipment": equipment, "product": product, "employee": "all",
             "shift": "all", "hour": "", "source": key}
    return payload, filters, stamp


async def collect(request):
    """Одна выборка и один расчёт для страницы, Excel и PDF — цифры всегда совпадают."""
    q = request.query
    key, src = pick_source(request)
    if src.get("kind") == "gantt":
        return await collect_gantt(request, key, src)
    if src.get("kind") == "proofing":
        return await collect_proofing(request, key, src)
    if src.get("kind") == "kc":
        return await collect_kc(request, key, src)
    exclude_dates = src["exclude"]
    args = {
        "date_from": q.get("date_from", "").strip(),
        "date_to": q.get("date_to", "").strip(),
        "equipment": q.get("equipment", "all").strip(),
        "product": q.get("product", "all").strip(),
        "employee": q.get("employee", "all").strip(),
        "shift": q.get("shift", "all").strip(),
        "hour": q.get("hour", "").strip(),
    }
    snapshot = await src["data"].get(force=q.get("refresh") == "1")
    production, pauses = aggregate.apply_filters(snapshot, exclude_dates=exclude_dates, **args)
    plan_filters = {
        "date_from": args["date_from"], "date_to": args["date_to"],
        "equipment": args["equipment"], "product": args["product"],
        "exclude_dates": exclude_dates,
    }
    # Планы загружаются только для ФК: продукты БХМ к ним отношения не имеют.
    stored_plans = await asyncio.to_thread(plan_store.load) if src["plans"] else {}
    payload = aggregate.build_summary(production, pauses, plans=stored_plans, plan_filters=plan_filters)
    payload["lfl"] = aggregate.build_lfl(
        snapshot, payload, production, pauses, args["date_from"], args["date_to"],
        filters={k: args[k] for k in ("equipment", "product", "employee", "shift", "hour")},
        exclude_dates=exclude_dates, now=datetime.now(TZ))
    # В файле плана есть только дата, оборудование и продукт. Фильтры по сотруднику,
    # смене и часу сужают факт, но не план, поэтому сравнение перестаёт быть
    # «выполнением» — об этом надо сказать прямо, а не показывать цифру молча.
    payload["plan"]["unsupported_filters"] = [
        name for name, value in (("сотруднику", args["employee"] not in ("all", "")),
                                 ("смене", args["shift"] not in ("all", "")),
                                 ("часу", bool(args["hour"]))) if value]
    payload["source"] = src["data"].describe()
    payload["source_key"] = key
    payload["source_title"] = src["title"]
    payload["units"] = src["units"]
    payload["plan_enabled"] = src["plans"]
    payload["excluded_dates"] = list(exclude_dates)

    def named(value, default="все"):
        return default if value in ("all", "") else value

    filters = [
        ("Период", f"{args['date_from'] or 'начало'} – {args['date_to'] or 'сегодня'}"),
        ("Оборудование", named(args["equipment"])),
        ("Продукт", named(args["product"])),
        ("Сотрудник", named(args["employee"])),
        ("Смена", SHIFT_LABELS.get(args["shift"], args["shift"])),
        ("Час", f"{int(args['hour']):02d}:00" if args["hour"].isdigit() else "все"),
        ("Скрытые даты", ", ".join(exclude_dates) or "нет"),
    ]
    if key != "fk":
        # У ФК список фильтров не меняем: выгрузки ФК остаются ровно такими, как были.
        filters.insert(0, ("Источник", src["title"]))
    args["source"] = key
    return payload, filters, args


def _stamp(args):
    parts = [args["date_from"] or "start", args["date_to"] or "today"]
    for key in ("equipment", "product", "employee"):
        if args[key] not in ("all", ""):
            parts.append(re.sub(r"[^\w-]+", "-", args[key], flags=re.UNICODE)[:24])
    if args["shift"] != "all":
        parts.append(args["shift"])
    if args["hour"]:
        parts.append(f"h{args['hour']}")
    for key in ("oven", "operation", "status"):
        if args.get(key, "all") not in ("all", ""):
            parts.append(re.sub(r"[^\w-]+", "-", args[key], flags=re.UNICODE)[:24])
    return "_".join(parts)


async def summary(request):
    payload, filters, _ = await collect(request)
    payload["ok"] = True
    payload["filters"] = filters
    if payload.get("kind") == "proofing":
        payload["analysis"] = proofing_aggregate.build_analysis(payload, filters)
    elif payload.get("kind") == "kc":
        payload["analysis"] = kc_aggregate.build_analysis(payload, filters)
    elif payload.get("kind") == "gantt":
        payload["analysis"] = gantt_exports.build_analysis(payload, filters)
    else:
        payload["analysis"] = aggregate.build_analysis(payload, filters)
    return no_store(web.json_response(payload))


def _attachment(blob, name, content_type):
    return web.Response(body=blob, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                            "Cache-Control": "no-store"}, content_type=content_type)


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def export_proofing(request, kind):
    widget = request.query.get("widget", "all").strip() or "all"
    if kind == "xlsx" and widget != "all" and widget not in proofing_exports.ALL_WIDGETS:
        return web.json_response({"ok": False, "error": f"неизвестный виджет: {widget}"}, status=400)
    payload, filters, args = await collect(request)
    if kind == "xlsx":
        blob = await asyncio.to_thread(proofing_exports.build_xlsx, payload, widget, filters)
        return _attachment(blob, f"proofing_{widget}_{_stamp(args)}.xlsx", XLSX_TYPE)
    analysis = proofing_aggregate.build_analysis(payload, filters)
    blob = await asyncio.to_thread(proofing_exports.build_pdf, payload, analysis, filters)
    return _attachment(blob, f"proofing_svodka_{_stamp(args)}.pdf", "application/pdf")


async def export_kc(request, kind):
    widget = request.query.get("widget", "all").strip() or "all"
    if kind == "xlsx" and widget != "all" and widget not in kc_exports.ALL_WIDGETS:
        return web.json_response({"ok": False, "error": f"неизвестный виджет: {widget}"}, status=400)
    payload, filters, args = await collect(request)
    if kind == "xlsx":
        blob = await asyncio.to_thread(kc_exports.build_xlsx, payload, widget, filters)
        return _attachment(blob, f"kc_{widget}_{_stamp(args)}.xlsx", XLSX_TYPE)
    analysis = kc_aggregate.build_analysis(payload, filters)
    blob = await asyncio.to_thread(kc_exports.build_pdf, payload, analysis, filters)
    return _attachment(blob, f"kc_svodka_{_stamp(args)}.pdf", "application/pdf")


async def export_gantt(request, kind):
    payload, filters, args = await collect(request)
    name = f"gantt_{args['date_from']}_{args['date_to']}"
    if kind == "xlsx":
        stored = await asyncio.to_thread(product_map.load)
        blob = await asyncio.to_thread(gantt_exports.build_xlsx, payload, filters, stored["map"])
        return _attachment(blob, f"{name}.xlsx", XLSX_TYPE)
    blob = await asyncio.to_thread(gantt_exports.build_pdf, payload, gantt_exports.build_analysis(payload, filters), filters)
    return _attachment(blob, f"{name}.pdf", "application/pdf")


async def gantt_map_state(request):
    bars, stored, _ = await gantt_data()
    return no_store(web.json_response({
        "ok": True, "upload_enabled": bool(plan_store.PLAN_UPLOAD_TOKEN), "map": stored["map"],
        "updated_at": stored.get("updated_at", ""), "file": stored.get("file", ""),
        "products": gantt.known_products(bars), "sections": product_map.SECTIONS}))


async def gantt_map_template(request):
    bars, stored, _ = await gantt_data()
    blob = await asyncio.to_thread(product_map.template_xlsx, gantt.known_products(bars), stored["map"])
    return _attachment(blob, "spravochnik_produktov.xlsx", XLSX_TYPE)


async def gantt_map_upload(request):
    """Справочник соответствий — тем же ключом, что загрузка плана (PLAN_UPLOAD_TOKEN)."""
    if not plan_store.PLAN_UPLOAD_TOKEN:
        return web.json_response({"ok": False, "error": "загрузка выключена: не задан PLAN_UPLOAD_TOKEN"}, status=403)
    if not plan_auth(request):
        return web.json_response({"ok": False, "error": "неверный ключ загрузки"}, status=401)
    reader = await request.multipart()
    field = await reader.next()
    while field is not None and field.name != "file":
        field = await reader.next()
    if field is None:
        return web.json_response({"ok": False, "error": "файл не передан"}, status=400)
    buffer, size = io.BytesIO(), 0
    while True:
        chunk = await field.read_chunk()
        if not chunk:
            break
        size += len(chunk)
        if size > plan_store.MAX_UPLOAD_BYTES:
            return web.json_response({"ok": False, "error": "файл слишком большой"}, status=413)
        buffer.write(chunk)
    buffer.seek(0)
    try:
        mapping, count = await asyncio.to_thread(product_map.parse_xlsx, buffer)
    except product_map.MapError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    stored = await asyncio.to_thread(product_map.save, mapping, field.filename or "")
    log.info("product map uploaded: file=%s pairs=%s", field.filename, count)
    return no_store(web.json_response({"ok": True, "pairs": count, "updated_at": stored["updated_at"]}))


async def proofing_photo(request):
    """Фото партии по запросу: браузер видит только этот адрес дашборда, ключ бота — нет."""
    if not proofing_source.enabled:
        return web.json_response({"ok": False, "error": "раздел «Расстойка» не настроен"}, status=503)
    try:
        data, content_type = await proofing_source.photo(request.match_info["id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "неверный id фото"}, status=400)
    except PhotoNotFound:
        return web.json_response({"ok": False, "error": "фото не найдено"}, status=404)
    except Exception as exc:
        log.warning("proofing photo failed: %s", exc)
        return web.json_response({"ok": False, "error": f"фото недоступно: {exc}"}, status=502)
    return web.Response(body=data, content_type=content_type, headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


async def export_xlsx(request):
    if request.query.get("source", "").strip().lower() == "proofing":
        return await export_proofing(request, "xlsx")
    if request.query.get("source", "").strip().lower() == "kc":
        return await export_kc(request, "xlsx")
    if request.query.get("source", "").strip().lower() == "gantt":
        return await export_gantt(request, "xlsx")
    widget = request.query.get("widget", "all").strip() or "all"
    if widget != "all" and widget not in exports.ALL_WIDGETS:
        return web.json_response({"ok": False, "error": f"неизвестный виджет: {widget}"}, status=400)
    payload, filters, args = await collect(request)
    if widget == "plan" and not payload["plan_enabled"]:
        return web.json_response({"ok": False, "error": "у этого источника нет плана"}, status=400)
    try:
        blob = await asyncio.to_thread(exports.build_xlsx, payload, widget, filters)
    except KeyError:
        return web.json_response({"ok": False, "error": f"неизвестный виджет: {widget}"}, status=400)
    name = f"{SOURCES[args['source']]['file']}_{widget}_{_stamp(args)}.xlsx"
    return web.Response(
        body=blob,
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "Cache-Control": "no-store"},
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


async def export_pdf(request):
    if request.query.get("source", "").strip().lower() == "proofing":
        return await export_proofing(request, "pdf")
    if request.query.get("source", "").strip().lower() == "kc":
        return await export_kc(request, "pdf")
    if request.query.get("source", "").strip().lower() == "gantt":
        return await export_gantt(request, "pdf")
    payload, filters, args = await collect(request)
    analysis = aggregate.build_analysis(payload, filters)
    title = "Сводка по производству" if args["source"] == "fk" else f"Сводка по производству {payload['source_title']}"
    blob = await asyncio.to_thread(exports.build_pdf, payload, analysis, filters, title)
    name = f"{SOURCES[args['source']]['file']}_svodka_{_stamp(args)}.pdf"
    return web.Response(
        body=blob,
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "Cache-Control": "no-store"},
        content_type="application/pdf")


def validate_relax():
    if not RELAXDEV_DEPLOY:
        return
    required = {
        "SOURCE_MODE=api": os.getenv("SOURCE_MODE") == "api",
        "BOT_API_URL": bool(os.getenv("BOT_API_URL", "").strip()),
        "BOT_API_KEY": bool(os.getenv("BOT_API_KEY", "").strip()),
        "PLAN_DATABASE_URL": plan_store.PG_PLANS,
        "PLAN_UPLOAD_TOKEN": bool(plan_store.PLAN_UPLOAD_TOKEN),
        "DASHBOARD_USER": bool(DASHBOARD_USER),
        "DASHBOARD_PASSWORD": bool(DASHBOARD_PASSWORD),
    }
    missing = [key for key, ready in required.items() if not ready]
    if missing:
        raise RuntimeError("RelaxDev dashboard: missing " + ", ".join(missing))
    if os.getenv("BHM_API_URL", "").strip() and not os.getenv("BHM_API_KEY", "").strip():
        raise RuntimeError("RelaxDev dashboard: BHM_API_KEY required for BHM_API_URL")
    if os.getenv("PROOFING_API_URL", "").strip() and not os.getenv("PROOFING_API_KEY", "").strip():
        raise RuntimeError("RelaxDev dashboard: PROOFING_API_KEY required for PROOFING_API_URL")
    if os.getenv("PROOFING_SOURCE_MODE", "").strip().lower() == "api" and not os.getenv("PROOFING_API_URL", "").strip():
        raise RuntimeError("RelaxDev dashboard: PROOFING_API_URL required for PROOFING_SOURCE_MODE=api")
    if os.getenv("KC_API_URL", "").strip() and not os.getenv("KC_API_KEY", "").strip():
        raise RuntimeError("RelaxDev dashboard: KC_API_KEY required for KC_API_URL")
    if os.getenv("KC_SOURCE_MODE", "").strip().lower() == "api" and not os.getenv("KC_API_URL", "").strip():
        raise RuntimeError("RelaxDev dashboard: KC_API_URL required for KC_SOURCE_MODE=api")


async def start_storage(app):
    validate_relax()
    await asyncio.to_thread(plan_store.init_storage)
    await asyncio.to_thread(product_map.init_storage)


@web.middleware
async def basic_auth(request, handler):
    if not RELAXDEV_DEPLOY or request.path == "/health":
        return await handler(request)
    header = request.headers.get("Authorization", "")
    valid = False
    if header.startswith("Basic "):
        try:
            user, password = base64.b64decode(header[6:], validate=True).decode("utf-8").split(":", 1)
            valid = (hmac.compare_digest(user, DASHBOARD_USER)
                     and hmac.compare_digest(password, DASHBOARD_PASSWORD))
        except (ValueError, binascii.Error, UnicodeError):
            pass
    if not valid:
        raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="FK Dashboard", charset="UTF-8"'})
    return await handler(request)


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        log.exception("request failed: %s", request.path)
        return web.json_response({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)


def build_app():
    app = web.Application(middlewares=[basic_auth, errors])
    app.on_startup.append(start_storage)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/meta", meta)
    app.router.add_get("/api/summary", summary)
    app.router.add_get("/api/export/xlsx", export_xlsx)
    app.router.add_get("/api/export/pdf", export_pdf)
    app.router.add_get("/api/proofing/photo/{id}", proofing_photo)
    app.router.add_get("/api/gantt/map", gantt_map_state)
    app.router.add_get("/api/gantt/map/template", gantt_map_template)
    app.router.add_post("/api/gantt/map/upload", gantt_map_upload)
    app.router.add_get("/api/plan", plan_state)
    app.router.add_get("/api/plan/history", plan_history)
    app.router.add_post("/api/plan/upload", plan_upload)
    app.router.add_delete("/api/plan", plan_delete)
    app.router.add_static("/static", STATIC, show_index=False)
    return app


if __name__ == "__main__":
    log.info("FK dashboard starting on port %s (source=%s, bhm=%s, proofing=%s, kc=%s)", PORT,
             source.describe()["mode"], bhm_source.describe()["mode"], proofing_source.describe()["mode"],
             kc_source.describe()["mode"])
    web.run_app(build_app(), host="0.0.0.0", port=PORT, access_log=None)
