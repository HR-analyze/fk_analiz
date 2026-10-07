"""Read-only веб-дашборд по данным бота ФК. В базу только SELECT, никаких записей."""
import asyncio
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
import plans as plan_store
from bhm_source import BhmSource
from sources import TZ, DataSource

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fk.dashboard")

PORT = int(os.getenv("PORT", "8090"))
STATIC = Path(__file__).parent / "static"
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "60"))
# Даты, которые дашборд не показывает. База при этом не трогается — фильтр только на чтении.
EXCLUDE_DATES = tuple(d.strip() for d in os.getenv("EXCLUDE_DATES", "").split(",") if d.strip())
BHM_EXCLUDE_DATES = tuple(d.strip() for d in os.getenv("BHM_EXCLUDE_DATES", "").split(",") if d.strip())
source = DataSource()
bhm_source = BhmSource()

# Источники дашборда. ФК — по умолчанию: старые ссылки без ?source= работают как раньше.
# У БХМ своя единица (кг) и нет загрузки плана — вкладки плана для него скрыты.
SOURCES = {
    "fk": {"title": "ФК", "data": source, "exclude": EXCLUDE_DATES, "plans": True,
           "units": {"qty": "шт", "rate": "шт/ч"}, "file": "fk"},
    "bhm": {"title": "БХМ", "data": bhm_source, "exclude": BHM_EXCLUDE_DATES, "plans": False,
            "units": {"qty": "кг", "rate": "кг/ч"}, "file": "bhm"},
}


def pick_source(request):
    key = request.query.get("source", "fk").strip().lower() or "fk"
    if key not in SOURCES:
        raise web.HTTPBadRequest(content_type="application/json", text=json.dumps(
            {"ok": False, "error": "неизвестный источник: " + re.sub(r"[^a-z0-9_-]", "", key)[:16]}, ensure_ascii=False))
    if key == "bhm" and not bhm_source.enabled:
        raise web.HTTPServiceUnavailable(content_type="application/json", text=json.dumps(
            {"ok": False, "error": "источник БХМ не настроен: задай BHM_API_URL и BHM_API_KEY"}, ensure_ascii=False))
    return key, SOURCES[key]


def sources_state():
    return {"fk": True, "bhm": bhm_source.enabled}


def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


async def index(request):
    return no_store(web.FileResponse(STATIC / "index.html"))


async def health(request):
    """200, пока сервис способен отдать данные (пусть и из кеша). 503 — если снимка нет вовсе."""
    try:
        await source.get()
    except Exception as exc:
        return web.json_response({"ok": False, "error": f"{type(exc).__name__}: {exc}", **source.describe()}, status=503)
    # БХМ — второй источник: его недоступность не делает контейнер нездоровым.
    return web.json_response({"ok": True, **source.describe(), "bhm": bhm_source.describe()})


async def meta(request):
    key, src = pick_source(request)
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


async def collect(request):
    """Одна выборка и один расчёт для страницы, Excel и PDF — цифры всегда совпадают."""
    q = request.query
    key, src = pick_source(request)
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
    return "_".join(parts)


async def summary(request):
    payload, filters, _ = await collect(request)
    payload["ok"] = True
    payload["filters"] = filters
    payload["analysis"] = aggregate.build_analysis(payload, filters)
    return no_store(web.json_response(payload))


async def export_xlsx(request):
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
    app = web.Application(middlewares=[errors])
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/meta", meta)
    app.router.add_get("/api/summary", summary)
    app.router.add_get("/api/export/xlsx", export_xlsx)
    app.router.add_get("/api/export/pdf", export_pdf)
    app.router.add_get("/api/plan", plan_state)
    app.router.add_get("/api/plan/history", plan_history)
    app.router.add_post("/api/plan/upload", plan_upload)
    app.router.add_delete("/api/plan", plan_delete)
    app.router.add_static("/static", STATIC, show_index=False)
    return app


if __name__ == "__main__":
    log.info("FK dashboard starting on port %s (source=%s, bhm=%s)",
             PORT, source.describe()["mode"], bhm_source.describe()["mode"])
    web.run_app(build_app(), host="0.0.0.0", port=PORT, access_log=None)
