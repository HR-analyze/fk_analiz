"""Источник «КЦ» (кондитерский цех): только HTTP к API бота кондитерки.

Бот живёт в своём репозитории (HR-analyze/fk_conditerka) со своей PostgreSQL и отдаёт
GET {KC_API_URL}/api/dashboard/all по заголовку X-API-Key (= DASHBOARD_API_KEY бота).
К базе бота дашборд не подключается.

Каждая запись КЦ — самостоятельная операция (продукция + операция + люди + штуки),
маршрута изделия нет. Поэтому штуки разных операций одной продукции нельзя складывать
в «готовый выпуск»: одна и та же ромовая баба проходит накалывание, купание и
декорирование — это 600 изделий, а не 1800. Здесь только приводим ответ к снимку,
правила счёта — в kc_aggregate.py.

Бот фильтрует по start_at, а завершённую работу дашборд считает по end_at. Снимок
берётся за MAX_DAYS с запасом в сутки назад и фильтруется на стороне дашборда, поэтому
операция, начатая до периода и закрытая внутри него, не теряется.

KC_API_KEY уходит только в заголовке запроса к боту: в ответы дашборда, URL, логи и
describe() он не попадает. ANALYTICS_KEY бота (экран Mini App) здесь не используется.
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

log = logging.getLogger("fk.kc")

KC_API_URL = os.getenv("KC_API_URL", "").strip().rstrip("/")
KC_API_KEY = os.getenv("KC_API_KEY", "").strip()
# api — ходим в API бота; demo — локальный файл в формате ответа API; off — раздел выключен.
KC_SOURCE_MODE = os.getenv(
    "KC_SOURCE_MODE", "api" if KC_API_URL else ("demo" if SOURCE_MODE == "demo" else "off")).strip().lower()
KC_DEMO_FILE = os.getenv("KC_DEMO_FILE", str(Path(__file__).parent / "kc_demo_data.json"))
KC_TIMEOUT = int(os.getenv("KC_TIMEOUT", "30"))
# Операция длиннее — ошибка ввода времени (бот так же ограничивает MAX_OPERATION_HOURS=18).
# В средние и нормы такие записи не идут, их видно отдельно.
MAX_OPERATION_HOURS = float(os.getenv("KC_MAX_OPERATION_HOURS", "18"))
# Сколько суток назад от начала выборки дополнительно брать у бота (он фильтрует по началу).
FETCH_BACK_DAYS = 1
STATUSES = ("open", "closed", "cancelled")


def _int(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def normalize_operation(r, max_hours=None):
    max_hours = MAX_OPERATION_HOURS if max_hours is None else max_hours
    start = _as_local(r.get("start_at"))
    end = _as_local(r.get("end_at"))
    status = _text(r.get("status"))
    if status not in STATUSES:
        status = "closed" if end else "open"
    if status != "closed":
        end = None
    people = _int(r.get("people"))
    pcs = _int(r.get("quantity_pcs")) if status == "closed" else None
    duration = (end - start).total_seconds() / 60 if start and end else None
    valid = duration is not None and 0 < duration <= max_hours * 60
    person_hours = duration / 60 * people if valid and people else None
    return {
        "id": _text(r.get("id")),
        "product": _text(r.get("product")) or "Без продукции",
        "operation": _text(r.get("operation")) or "Без операции",
        "people": people,
        "pcs": pcs,
        "status": status,
        "start_at": start,
        "end_at": end,
        "closed_at": _as_local(r.get("closed_at")),
        "duration_min": round(duration, 2) if duration is not None else None,
        "valid": valid,
        "person_hours": round(person_hours, 4) if person_hours is not None else None,
        "user_name": _text(r.get("user_name")) or "—",
        "closed_by_name": _text(r.get("closed_by_name")),
    }


def normalize_pause(r):
    start, end = _as_local(r.get("start_at")), _as_local(r.get("end_at"))
    duration = (end - start).total_seconds() / 60 if start and end and end > start else 0.0
    return {
        "id": _text(r.get("id")),
        "reason": _text(r.get("reason")) or "Без причины",
        "user_name": _text(r.get("user_name")) or "—",
        "start_at": start,
        "end_at": end,
        "duration_min": round(duration, 2),
    }


def normalize(raw_ops, raw_pauses, raw_catalog=None, max_hours=None):
    seen, ops, duplicates = set(), [], 0
    for r in raw_ops or []:
        op_id = _text(r.get("id"))
        if op_id and op_id in seen:
            duplicates += 1
            continue
        seen.add(op_id)
        op = normalize_operation(r, max_hours)
        if op["start_at"]:
            ops.append(op)
    pauses = [p for p in (normalize_pause(r) for r in raw_pauses or []) if p["start_at"]]
    seen_pauses = set()
    unique_pauses = []
    for p in pauses:
        if p["id"] and p["id"] in seen_pauses:
            continue
        seen_pauses.add(p["id"])
        unique_pauses.append(p)
    ops.sort(key=lambda x: x["start_at"], reverse=True)
    unique_pauses.sort(key=lambda x: x["start_at"], reverse=True)
    reasons = [_text(x) for x in (raw_catalog or {}).get("reasons") or [] if _text(x)]
    return {
        "operations": ops,
        "pauses": unique_pauses,
        "reasons": reasons,
        "issues": {
            "duplicate_ops": duplicates,
            "invalid_duration": sum(1 for o in ops if o["status"] == "closed" and not o["valid"]),
            "closed_without_pcs": sum(1 for o in ops if o["status"] == "closed" and not o["pcs"]),
            "closed_without_people": sum(1 for o in ops if o["status"] == "closed" and not o["people"]),
        },
    }


def _fetch_from_demo():
    with open(KC_DEMO_FILE, encoding="utf-8") as fh:
        return json.load(fh)


async def _error_code(resp):
    try:
        return (await resp.json()).get("error", "")
    except (aiohttp.ContentTypeError, ValueError):
        return ""


async def _fetch_from_api(since):
    if not KC_API_URL:
        raise RuntimeError("KC_API_URL is not set")
    if not KC_API_KEY:
        raise RuntimeError("KC_API_KEY is not set")
    url = f"{KC_API_URL}/api/dashboard/all"
    timeout = aiohttp.ClientTimeout(total=KC_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params={"date_from": since.strftime("%Y-%m-%d")},
                               headers={"X-API-Key": KC_API_KEY}) as resp:
            if resp.status == 401:
                raise RuntimeError("KC API rejected the key (401)")
            if resp.status >= 400:
                code = await _error_code(resp)
                raise RuntimeError(f"KC API error {resp.status}{': ' + code if code else ''}")
            payload = await resp.json()
    if not isinstance(payload, dict) or "operations" not in payload:
        raise RuntimeError("KC API returned an unexpected payload")
    return payload


class KcSource:
    """Снимок КЦ в памяти, обновляется не чаще CACHE_TTL — как у ФК, БХМ и расстойки."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._snapshot = None
        self._fetched_at = 0.0
        self._error = ""

    @property
    def enabled(self):
        return KC_SOURCE_MODE in ("api", "demo")

    def describe(self):
        return {
            "mode": f"kc-{KC_SOURCE_MODE}",
            "cache_ttl": CACHE_TTL,
            "fetched_at": datetime.fromtimestamp(self._fetched_at, TZ).isoformat() if self._fetched_at else "",
            "error": self._error,
        }

    async def get(self, force=False):
        async with self._lock:
            fresh = self._snapshot is not None and (time.time() - self._fetched_at) < CACHE_TTL
            if fresh and not force:
                return self._snapshot
            since = datetime.now(TZ) - timedelta(days=MAX_DAYS + FETCH_BACK_DAYS)
            try:
                if KC_SOURCE_MODE == "demo":
                    payload = await asyncio.to_thread(_fetch_from_demo)
                elif KC_SOURCE_MODE == "api":
                    payload = await _fetch_from_api(since)
                else:
                    raise RuntimeError("раздел КЦ выключен: не задан KC_API_URL")
                self._snapshot = normalize(payload.get("operations", []), payload.get("pauses", []),
                                           payload.get("catalog"))
                self._fetched_at = time.time()
                self._error = ""
                log.info("kc snapshot refreshed: operations=%s pauses=%s mode=%s",
                         len(self._snapshot["operations"]), len(self._snapshot["pauses"]), KC_SOURCE_MODE)
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                log.error("kc snapshot refresh failed: %s", self._error)
                if self._snapshot is None:
                    raise
            return self._snapshot
