"""Read-only веб-дашборд по данным бота ФК. В базу только SELECT, никаких записей."""
import asyncio
import hmac
import io
import logging
import os
from pathlib import Path

from aiohttp import web

import aggregate
import plans as plan_store
from sources import TZ, DataSource

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fk.dashboard")

PORT = int(os.getenv("PORT", "8090"))
STATIC = Path(__file__).parent / "static"
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "60"))
# Даты, которые дашборд не показывает. База при этом не трогается — фильтр только на чтении.
EXCLUDE_DATES = tuple(d.strip() for d in os.getenv("EXCLUDE_DATES", "").split(",") if d.strip())
source = DataSource()


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
    return web.json_response({"ok": True, **source.describe()})


async def meta(request):
    snapshot = await source.get()
    return no_store(web.json_response({
        "ok": True,
        "timezone": str(TZ),
        "refresh_seconds": REFRESH_SECONDS,
        "source": source.describe(),
        "plan_upload_enabled": bool(plan_store.PLAN_UPLOAD_TOKEN),
        **aggregate.build_meta(snapshot, EXCLUDE_DATES),
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

    try:
        parsed = await asyncio.to_thread(plan_store.parse_workbook, buffer, plan_date)
    except plan_store.PlanError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    stored = await asyncio.to_thread(plan_store.save, parsed, field.filename or "")
    log.info("plan uploaded: file=%s dates=%s", field.filename, sorted(parsed))
    return no_store(web.json_response({
        "ok": True,
        "loaded_dates": sorted(parsed),
        "loaded_positions": sum(len(v) for v in parsed.values()),
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


async def plan_delete(request):
    if not plan_auth(request):
        return web.json_response({"ok": False, "error": "неверный ключ загрузки"}, status=401)
    dates = [d for d in request.query.get("dates", "").split(",") if d.strip()]
    if not dates:
        return web.json_response({"ok": False, "error": "не переданы даты"}, status=400)
    stored = await asyncio.to_thread(plan_store.delete_dates, dates)
    return no_store(web.json_response({"ok": True, "stored_dates": sorted(stored)}))


async def summary(request):
    snapshot = await source.get(force=request.query.get("refresh") == "1")
    production, pauses = aggregate.apply_filters(
        snapshot,
        date_from=request.query.get("date_from", "").strip(),
        date_to=request.query.get("date_to", "").strip(),
        equipment=request.query.get("equipment", "all").strip(),
        product=request.query.get("product", "all").strip(),
        shift=request.query.get("shift", "all").strip(),
        employee=request.query.get("employee", "all").strip(),
        hour=request.query.get("hour", "").strip(),
        exclude_dates=EXCLUDE_DATES,
    )
    plan_filters = {
        "date_from": request.query.get("date_from", "").strip(),
        "date_to": request.query.get("date_to", "").strip(),
        "equipment": request.query.get("equipment", "all").strip(),
        "product": request.query.get("product", "all").strip(),
        "exclude_dates": EXCLUDE_DATES,
    }
    stored_plans = await asyncio.to_thread(plan_store.load)
    payload = aggregate.build_summary(production, pauses, plans=stored_plans,
                                      plan_filters=plan_filters)
    payload["ok"] = True
    payload["source"] = source.describe()
    payload["excluded_dates"] = list(EXCLUDE_DATES)
    return no_store(web.json_response(payload))


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
    app.router.add_get("/api/plan", plan_state)
    app.router.add_post("/api/plan/upload", plan_upload)
    app.router.add_delete("/api/plan", plan_delete)
    app.router.add_static("/static", STATIC, show_index=False)
    return app


if __name__ == "__main__":
    log.info("FK dashboard starting on port %s (source=%s)", PORT, source.describe()["mode"])
    web.run_app(build_app(), host="0.0.0.0", port=PORT, access_log=None)
