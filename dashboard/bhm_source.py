"""Источник БХМ: только HTTP к API бота БХМ, никакой базы и никакого кода бота.

Бот БХМ живёт в своём репозитории (HR-analyze/FK_BHM) и отдаёт
GET {BHM_API_URL}/api/dashboard/all по заголовку X-API-Key. Здесь ответ
приводится к тому же снимку {"production": [...], "pauses": [...]}, что у ФК,
поэтому дальше работают те же aggregate.py и exports.py.

Ключ BHM_API_KEY уходит только в заголовке запроса к API БХМ: в ответы
дашборда, логи и describe() он не попадает.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp

from sources import CACHE_TTL, MAX_DAYS, SOURCE_MODE, TZ, _as_local, _text

log = logging.getLogger("fk.bhm")

BHM_API_URL = os.getenv("BHM_API_URL", "").strip().rstrip("/")
BHM_API_KEY = os.getenv("BHM_API_KEY", "").strip()
# api — ходим в API БХМ; demo — локальный файл в формате ответа API; off — БХМ выключен.
# По умолчанию: есть BHM_API_URL — api, дашборд в demo — demo, иначе off.
BHM_SOURCE_MODE = os.getenv(
    "BHM_SOURCE_MODE", "api" if BHM_API_URL else ("demo" if SOURCE_MODE == "demo" else "off")).strip().lower()
BHM_DEMO_FILE = os.getenv("BHM_DEMO_FILE", str(Path(__file__).parent / "bhm_demo_data.json"))
BHM_TIMEOUT = int(os.getenv("BHM_TIMEOUT", "30"))
# Вес партии вносят один раз (в конце сборки), а бот копирует его в каждый шаг партии:
# сборка, замес, отлежка, обминка, подача. Сложить quantity_kg всех шагов — значит
# посчитать одну партию 5–6 раз. Поэтому объём выпуска берём с одного шага —
# замеса, как экран «Замешано» в аналитике самого БХМ. Остальные шаги идут
# операциями с длительностью и qty = 0.
BHM_VOLUME_OPERATION = os.getenv("BHM_VOLUME_OPERATION", "dough_mixing").strip()
# Пропущенный шаг — не работа (у него start = end и пустые кг), отменённый —
# партия ушла в брак или заведена по ошибке. Ни то ни другое не выпуск.
DROP_STATUSES = {"skipped", "cancelled"}


def _kg(value):
    try:
        kg = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(kg, 2) if kg > 0 else 0.0


def _stamp(row):
    # Дата и смена операции — по её началу: так БХМ фильтрует и свои отчёты.
    return _as_local(row.get("start_at")) or _as_local(row.get("created_at"))


def normalize_operations(rows, volume_operation=None):
    volume_operation = volume_operation or BHM_VOLUME_OPERATION
    out = []
    for r in rows:
        status = _text(r.get("status")) or "closed"
        if status in DROP_STATUSES:
            continue
        started = _stamp(r)
        out.append({
            "id": _text(r.get("id")),
            "user_name": _text(r.get("user_name")) or "—",
            "equipment": _text(r.get("equipment")) or "Без оборудования",
            "product": _text(r.get("product")) or "Без продукта",
            "operation": _text(r.get("operation_name")) or _text(r.get("operation")),
            "start_time": _text(r.get("start_time")),
            "end_time": _text(r.get("end_time")),
            "qty": _kg(r.get("quantity_kg")) if _text(r.get("operation")) == volume_operation else 0,
            "status": status,
            "created_at": started.isoformat() if started else "",
            "date": started.strftime("%Y-%m-%d") if started else "",
        })
    return out


def normalize_pauses(rows):
    out = []
    for r in rows:
        started = _stamp(r)
        out.append({
            "id": _text(r.get("id")),
            "user_name": _text(r.get("user_name")) or "—",
            "equipment": _text(r.get("equipment")) or "Без оборудования",
            "reason": _text(r.get("reason")) or "Без причины",
            "start_time": _text(r.get("start_time")),
            "end_time": _text(r.get("end_time")),
            "created_at": started.isoformat() if started else "",
            "date": started.strftime("%Y-%m-%d") if started else "",
        })
    return out


def _fetch_from_demo():
    with open(BHM_DEMO_FILE, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload.get("operations", []), payload.get("pauses", [])


async def _fetch_from_api(since):
    if not BHM_API_URL:
        raise RuntimeError("BHM_API_URL is not set")
    if not BHM_API_KEY:
        raise RuntimeError("BHM_API_KEY is not set")
    url = f"{BHM_API_URL}/api/dashboard/all"
    timeout = aiohttp.ClientTimeout(total=BHM_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params={"date_from": since.strftime("%Y-%m-%d")},
                               headers={"X-API-Key": BHM_API_KEY}) as resp:
            if resp.status == 401:
                raise RuntimeError("BHM API rejected the key (401)")
            if resp.status >= 400:
                # Тело ошибки БХМ — {"error": "..."}; URL с параметрами не логируем целиком.
                try:
                    code = (await resp.json()).get("error", "")
                except (aiohttp.ContentTypeError, ValueError):
                    code = ""
                raise RuntimeError(f"BHM API error {resp.status}{': ' + code if code else ''}")
            payload = await resp.json()
    return payload.get("operations", []), payload.get("pauses", [])


class BhmSource:
    """Снимок данных БХМ в памяти, обновляется не чаще CACHE_TTL — как DataSource у ФК."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._snapshot = None
        self._fetched_at = 0.0
        self._error = ""

    @property
    def enabled(self):
        return BHM_SOURCE_MODE in ("api", "demo")

    def describe(self):
        return {
            "mode": f"bhm-{BHM_SOURCE_MODE}",
            "cache_ttl": CACHE_TTL,
            "fetched_at": datetime.fromtimestamp(self._fetched_at, TZ).isoformat() if self._fetched_at else "",
            "error": self._error,
        }

    async def get(self, force=False):
        async with self._lock:
            fresh = self._snapshot is not None and (time.time() - self._fetched_at) < CACHE_TTL
            if fresh and not force:
                return self._snapshot
            since = datetime.now(TZ) - timedelta(days=MAX_DAYS)
            try:
                if BHM_SOURCE_MODE == "demo":
                    raw_ops, raw_pauses = await asyncio.to_thread(_fetch_from_demo)
                elif BHM_SOURCE_MODE == "api":
                    raw_ops, raw_pauses = await _fetch_from_api(since)
                else:
                    raise RuntimeError("источник БХМ выключен: не задан BHM_API_URL")
                self._snapshot = {
                    "production": normalize_operations(raw_ops),
                    "pauses": normalize_pauses(raw_pauses),
                }
                self._fetched_at = time.time()
                self._error = ""
                log.info("bhm snapshot refreshed: operations=%s pauses=%s mode=%s",
                         len(self._snapshot["production"]), len(self._snapshot["pauses"]), BHM_SOURCE_MODE)
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                log.error("bhm snapshot refresh failed: %s", self._error)
                if self._snapshot is None:
                    raise
            return self._snapshot
