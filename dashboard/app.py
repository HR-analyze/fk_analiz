"""Read-only веб-дашборд по данным бота ФК. В базу только SELECT, никаких записей."""
import logging
import os
from pathlib import Path

from aiohttp import web

import aggregate
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
        **aggregate.build_meta(snapshot, EXCLUDE_DATES),
    }))


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
    payload = aggregate.build_summary(production, pauses)
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
    app.router.add_static("/static", STATIC, show_index=False)
    return app


if __name__ == "__main__":
    log.info("FK dashboard starting on port %s (source=%s)", PORT, source.describe()["mode"])
    web.run_app(build_app(), host="0.0.0.0", port=PORT, access_log=None)
