"""Загрузка данных для дашборда. Только чтение: ни DDL, ни DML тут нет и быть не может."""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

log = logging.getLogger("fk.sources")

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
SOURCE_MODE = os.getenv("SOURCE_MODE", "db").strip().lower()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
BOT_API_URL = os.getenv("BOT_API_URL", "").strip().rstrip("/")
BOT_API_KEY = os.getenv("BOT_API_KEY", "").strip()
CACHE_TTL = int(os.getenv("CACHE_TTL", "60"))
MAX_DAYS = int(os.getenv("MAX_DAYS", "400"))
STATEMENT_TIMEOUT_MS = int(os.getenv("STATEMENT_TIMEOUT_MS", "15000"))
DEMO_FILE = os.getenv("DEMO_FILE", str(Path(__file__).parent / "demo_data.json"))

# Сессия открывается только в режиме чтения: любая попытка записи упадёт с ошибкой 25006.
READ_ONLY_OPTIONS = f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS}"

PRODUCTION_SQL = (
    "SELECT id,user_id,user_name,equipment,forming,product,start_time,end_time,quantity,status,created_at "
    "FROM production WHERE created_at >= %s ORDER BY created_at DESC"
)
PAUSES_SQL = (
    "SELECT id,user_id,user_name,equipment,reason,start_time,end_time,created_at "
    "FROM pauses WHERE created_at >= %s ORDER BY created_at DESC"
)


def _assert_read_only(sql):
    head = sql.lstrip().split(None, 1)[0].upper()
    if head not in ("SELECT", "WITH"):
        raise RuntimeError("dashboard is read-only: only SELECT/WITH statements are allowed")


def _as_local(value):
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    return None


def _num(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _text(value):
    return (str(value).strip() if value not in (None, "") else "")


def normalize_production(rows):
    out = []
    for r in rows:
        created = _as_local(r.get("created_at"))
        out.append({
            "id": _text(r.get("id")),
            "user_name": _text(r.get("user_name")) or "—",
            "equipment": _text(r.get("equipment")) or "Без оборудования",
            "product": _text(r.get("product")) or "Без продукта",
            "start_time": _text(r.get("start_time")),
            "end_time": _text(r.get("end_time")),
            "qty": _num(r.get("quantity")),
            "status": _text(r.get("status")) or "closed",
            "created_at": created.isoformat() if created else "",
            "date": created.strftime("%Y-%m-%d") if created else "",
        })
    return out


def normalize_pauses(rows):
    out = []
    for r in rows:
        created = _as_local(r.get("created_at"))
        out.append({
            "id": _text(r.get("id")),
            "user_name": _text(r.get("user_name")) or "—",
            "equipment": _text(r.get("equipment")) or "Без оборудования",
            "reason": _text(r.get("reason")) or "Без причины",
            "start_time": _text(r.get("start_time")),
            "end_time": _text(r.get("end_time")),
            "created_at": created.isoformat() if created else "",
            "date": created.strftime("%Y-%m-%d") if created else "",
        })
    return out


_options_supported = True


def _connect():
    """Коннект в режиме чтения. Если пулер (pgbouncer) не принимает стартовые options —
    ставим те же ограничения через SET уже внутри сессии."""
    global _options_supported
    import psycopg
    from psycopg.rows import dict_row

    if _options_supported:
        try:
            return psycopg.connect(DATABASE_URL, row_factory=dict_row, options=READ_ONLY_OPTIONS, autocommit=True)
        except psycopg.Error as exc:
            _options_supported = False
            log.warning("startup options rejected by the server (%s); falling back to SET", exc)
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
    # Не запись данных, а ограничение сессии: после этого любой INSERT/UPDATE упадёт.
    conn.execute(f"SET statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
    conn.execute("SET default_transaction_read_only = on")
    return conn


def _fetch_from_db(since):
    _assert_read_only(PRODUCTION_SQL)
    _assert_read_only(PAUSES_SQL)
    with _connect() as conn:
        production = conn.execute(PRODUCTION_SQL, (since,)).fetchall()
        pauses = conn.execute(PAUSES_SQL, (since,)).fetchall()
    return production, pauses


def _fetch_from_demo():
    """Локальные данные для проверки деплоя без единого обращения к боевой базе."""
    with open(DEMO_FILE, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload.get("production", []), payload.get("pauses", [])


async def _fetch_from_api():
    if not BOT_API_URL:
        raise RuntimeError("BOT_API_URL is not set")
    url = f"{BOT_API_URL}/api/dashboard/all"
    headers = {"X-API-Key": BOT_API_KEY} if BOT_API_KEY else {}
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 401:
                raise RuntimeError("bot API rejected the key (401)")
            resp.raise_for_status()
            payload = await resp.json()
    return payload.get("production", []), payload.get("pauses", [])


class DataSource:
    """Держит один снимок данных в памяти и обновляет его не чаще CACHE_TTL."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._snapshot = None
        self._fetched_at = 0.0
        self._error = ""

    def describe(self):
        return {
            "mode": SOURCE_MODE,
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
                if SOURCE_MODE == "demo":
                    raw_production, raw_pauses = await asyncio.to_thread(_fetch_from_demo)
                elif SOURCE_MODE == "api":
                    raw_production, raw_pauses = await _fetch_from_api()
                else:
                    if not DATABASE_URL:
                        raise RuntimeError("DATABASE_URL is not set")
                    raw_production, raw_pauses = await asyncio.to_thread(_fetch_from_db, since)
                self._snapshot = {
                    "production": normalize_production(raw_production),
                    "pauses": normalize_pauses(raw_pauses),
                }
                self._fetched_at = time.time()
                self._error = ""
                log.info("snapshot refreshed: production=%s pauses=%s mode=%s",
                         len(self._snapshot["production"]), len(self._snapshot["pauses"]), SOURCE_MODE)
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                log.exception("snapshot refresh failed")
                if self._snapshot is None:
                    raise
            return self._snapshot
