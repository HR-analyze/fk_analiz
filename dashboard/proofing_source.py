"""Источник «Расстойка и отпекание»: только HTTP к API бота расстойки.

Бот живёт в своём репозитории (HR-analyze/rastoyka_fk) со своей PostgreSQL и отдаёт
GET {PROOFING_API_URL}/api/dashboard/all и /api/dashboard/photo/{id} по заголовку
X-API-Key (= DASHBOARD_API_KEY бота). Сюда не копируется ни строки кода бота,
к его базе дашборд не подключается.

Ответ приводится к снимку «партия → два этапа»: расстойка и отпекание. Плоский
снимок ФК/БХМ («операция = выпуск») для расстойки не годится: одна партия проходит
два шага, а выпуск у неё один — по концу отпекания.

PROOFING_API_KEY уходит только в заголовке запроса к боту: в ответы дашборда, URL,
логи и describe() он не попадает. ANALYTICS_KEY (экран аналитики Mini App) здесь
не используется намеренно — это другой ключ для людей.
"""
import asyncio
import hashlib
import json
import logging
import os
import struct
import time
import uuid
import zlib
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp

from sources import CACHE_TTL, MAX_DAYS, SOURCE_MODE, TZ, _as_local, _text

log = logging.getLogger("fk.proofing")

PROOFING_API_URL = os.getenv("PROOFING_API_URL", "").strip().rstrip("/")
PROOFING_API_KEY = os.getenv("PROOFING_API_KEY", "").strip()
# api — ходим в API бота; demo — локальный файл в формате ответа API; off — раздел выключен.
# По умолчанию: есть PROOFING_API_URL — api, дашборд в demo — demo, иначе off.
PROOFING_SOURCE_MODE = os.getenv(
    "PROOFING_SOURCE_MODE",
    "api" if PROOFING_API_URL else ("demo" if SOURCE_MODE == "demo" else "off")).strip().lower()
PROOFING_DEMO_FILE = os.getenv("PROOFING_DEMO_FILE", str(Path(__file__).parent / "proofing_demo_data.json"))
PROOFING_TIMEOUT = int(os.getenv("PROOFING_TIMEOUT", "30"))
PROOFING_PHOTO_TIMEOUT = int(os.getenv("PROOFING_PHOTO_TIMEOUT", "20"))
# Этап длиннее — почти наверняка ошибка ввода времени (так же ограничивает сам бот:
# MAX_OPERATION_HOURS=18). В средние такие этапы не попадают, их видно отдельно.
MAX_STAGE_HOURS = float(os.getenv("PROOFING_MAX_STAGE_HOURS", "18"))
# Фото по запросу человека, не пачкой: держим немного последних в памяти.
PHOTO_CACHE_ITEMS = int(os.getenv("PROOFING_PHOTO_CACHE_ITEMS", "64"))
PHOTO_CACHE_BYTES = int(os.getenv("PROOFING_PHOTO_CACHE_MB", "48")) * 1024 * 1024
MAX_PHOTO_BYTES = 8 * 1024 * 1024 + 1024  # бот принимает до 8 МБ
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}

# Справочник печей бота (catalog.py: OVENS). Ответ API тоже несёт catalog —
# он главнее; это запасной вариант, если в ответе его нет.
DEFAULT_OVENS = [
    {"code": "hearth", "name": "Подовая", "unit": "pods"},
    {"code": "rotary", "name": "Ротационная", "unit": "racks"},
    {"code": "thermal_oil", "name": "Термомасляная", "unit": "racks"},
]
PROOFER = "Расстоечная камера"
STAGES = ("proofing", "baking")
STAGE_NAMES = {"proofing": "Расстойка", "baking": "Отпекание"}
MARK_NAMES = {("proofing", "start"): "Начало расстойки", ("proofing", "end"): "Конец расстойки",
              ("baking", "start"): "Начало отпекания", ("baking", "end"): "Конец отпекания"}
BATCH_STATUSES = ("active", "done", "cancelled")


class PhotoNotFound(Exception):
    pass


def _int(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n


def _iso(dt):
    return dt.isoformat() if dt else ""


def oven_catalog(raw_catalog):
    ovens = (raw_catalog or {}).get("ovens") or DEFAULT_OVENS
    out = []
    for o in ovens:
        code = _text(o.get("code"))
        if code:
            out.append({"code": code, "name": _text(o.get("name")) or code,
                        "unit": "pods" if _text(o.get("unit")) == "pods" else "racks"})
    return out or list(DEFAULT_OVENS)


def _stage_rank(row):
    # Дубликаты одного шага (повтор выдачи API, ручная правка базы): берём живой и
    # закрытый, при равенстве — более поздний. Отменённый — в последнюю очередь.
    status = _text(row.get("status"))
    return ({"closed": 2, "open": 1}.get(status, 0), _text(row.get("created_at")), _text(row.get("id")))


def normalize_stage(row, ovens_by_code):
    code = _text(row.get("operation"))
    start = _as_local(row.get("start_at"))
    end = _as_local(row.get("end_at"))
    status = _text(row.get("status")) or ("closed" if end else "open")
    duration = None
    valid = False
    if status == "closed" and start and end:
        duration = (end - start).total_seconds() / 60
        valid = 0 < duration <= MAX_STAGE_HOURS * 60
    oven = _text(row.get("oven")) if code == "baking" else ""
    unit = _text(row.get("rack_unit"))
    if unit not in ("racks", "pods"):
        unit = ovens_by_code.get(oven, {}).get("unit", "racks") if code == "baking" else "racks"
    equipment = _text(row.get("equipment"))
    if not equipment:
        equipment = f"{ovens_by_code[oven]['name']} печь" if oven in ovens_by_code else (PROOFER if code == "proofing" else "")
    return {
        "id": _text(row.get("id")),
        "operation": code,
        "name": _text(row.get("operation_name")) or STAGE_NAMES.get(code, code),
        "status": status,
        "start_at": start,
        "end_at": end if status == "closed" else None,
        "duration_min": round(duration, 2) if duration is not None else None,
        "valid": valid,
        "equipment": equipment,
        "oven": oven,
        "rack_unit": unit,
        "racks_start": _int(row.get("racks_start")),
        "racks_end": _int(row.get("racks_end")),
        "pieces_start": _int(row.get("pieces_start")),
        "pieces_end": _int(row.get("pieces_end")),
        "started_by": _text(row.get("user_name")) or "—",
        "closed_by": _text(row.get("closed_by_name")) if status == "closed" else "",
    }


def _derive_status(stages):
    if any(s["status"] == "cancelled" for s in stages.values() if s):
        return "cancelled"
    baking = stages.get("baking")
    if baking and baking["status"] == "closed":
        return "done"
    return "active"


def _next_mark(batch):
    if batch["status"] != "active":
        return ""
    for code in STAGES:
        stage = batch[code]
        if not stage:
            return MARK_NAMES[(code, "start")]
        if stage["status"] == "open":
            return MARK_NAMES[(code, "end")]
    return ""


def normalize(raw_ops, raw_batches, raw_pauses, raw_photos, raw_catalog=None):
    """Ответ /api/dashboard/all → снимок для proofing_aggregate.

    Выпуск партии берётся ровно один раз: pieces_end закрытого отпекания, если партия
    не отменена. batches.pieces_out бота служит только сверкой.
    """
    ovens = oven_catalog(raw_catalog)
    ovens_by_code = {o["code"]: o for o in ovens}
    issues = {"duplicate_ops": 0, "pieces_mismatch": 0, "done_without_baking": 0,
              "invalid_duration": 0, "orphan_ops": 0}

    by_batch = {}
    seen_ops = set()
    for r in raw_ops or []:
        op_id = _text(r.get("id"))
        if op_id and op_id in seen_ops:
            issues["duplicate_ops"] += 1
            continue
        seen_ops.add(op_id)
        code = _text(r.get("operation"))
        batch_id = _text(r.get("batch_id"))
        if code not in STAGES or not batch_id:
            continue
        slot = by_batch.setdefault(batch_id, {})
        if code in slot:
            issues["duplicate_ops"] += 1
            if _stage_rank(r) <= _stage_rank(slot[code]):
                continue
        slot[code] = r

    photos_by_batch = {}
    photo_index = {}
    for p in raw_photos or []:
        pid, batch_id = _text(p.get("id")), _text(p.get("batch_id"))
        if not pid or not batch_id or pid in photo_index:
            continue
        created = _as_local(p.get("created_at"))
        operation = _text(p.get("operation"))
        phase = _text(p.get("phase"))
        item = {"id": pid, "batch_id": batch_id, "step_no": _int(p.get("step_no")), "phase": phase,
                "operation": operation,
                "mark": _text(p.get("mark")) or MARK_NAMES.get((operation, phase), ""),
                "content_type": _text(p.get("content_type")), "size": _int(p.get("size")) or 0,
                "user_name": _text(p.get("user_name")) or "—", "created_at": _iso(created)}
        photo_index[pid] = item
        photos_by_batch.setdefault(batch_id, []).append(item)

    raw_by_id = {}
    for b in raw_batches or []:
        bid = _text(b.get("id"))
        if bid and bid not in raw_by_id:
            raw_by_id[bid] = b

    batches = []
    for bid in list(raw_by_id) + [k for k in by_batch if k not in raw_by_id]:
        b = raw_by_id.get(bid)
        rows = by_batch.get(bid, {})
        stages = {code: normalize_stage(rows[code], ovens_by_code) if code in rows else None for code in STAGES}
        if b is None:
            # Партия начата раньше глубины выборки, а её шаги попали — собираем по шагам.
            issues["orphan_ops"] += 1
            first = rows.get("proofing") or rows.get("baking")
            b = {"id": bid, "product": first.get("product"), "product_code": first.get("product_code"),
                 "status": None, "started_at": first.get("start_at"), "user_name": first.get("user_name")}
        status = _text(b.get("status"))
        if status not in BATCH_STATUSES:
            status = _derive_status(stages)
        baking = stages["baking"]
        pieces_out = None
        batch_out = _int(b.get("pieces_out"))
        if status != "cancelled" and baking and baking["status"] == "closed":
            pieces_out = baking["pieces_end"]
            if batch_out is not None and pieces_out is not None and batch_out != pieces_out:
                issues["pieces_mismatch"] += 1
        elif status == "done":
            issues["done_without_baking"] += 1
        for s in stages.values():
            if s and s["status"] == "closed" and not s["valid"]:
                issues["invalid_duration"] += 1
        oven = (baking or {}).get("oven") or _text(b.get("oven"))
        proofing = stages["proofing"]
        started = _as_local(b.get("started_at")) or (proofing or {}).get("start_at")
        finished = _as_local(b.get("finished_at"))
        if status == "done" and baking and baking["end_at"]:
            finished = baking["end_at"]
        marks = []
        for code in STAGES:
            s = stages[code]
            if not s:
                continue
            marks.append({"kind": f"{code}_start", "at": s["start_at"], "user": s["started_by"]})
            if s["status"] == "closed" and s["end_at"]:
                marks.append({"kind": f"{code}_end", "at": s["end_at"], "user": s["closed_by"] or s["started_by"]})
        batch = {
            "id": bid,
            "product": _text(b.get("product")) or "Без изделия",
            "product_code": _text(b.get("product_code")),
            "status": status,
            "started_at": started,
            "finished_at": finished,
            "user_name": _text(b.get("user_name")) or (proofing or {}).get("started_by") or "—",
            "finished_by_name": _text(b.get("finished_by_name")),
            "oven": oven,
            "oven_name": ovens_by_code.get(oven, {}).get("name", ""),
            "oven_unit": ovens_by_code.get(oven, {}).get("unit", ""),
            "proofing": proofing,
            "baking": baking,
            # Сколько штук заложили в расстойку. batches.pieces бота — «последние введённые шт»,
            # у отпечённой партии это уже выход, поэтому вход берём только с расстойки.
            "pieces_in": (proofing or {}).get("pieces_start"),
            "pieces_out": pieces_out,
            "batch_pieces_out": batch_out,
            "marks": marks,
            "employees": sorted({m["user"] for m in marks if m["user"] and m["user"] != "—"}),
            "equipment": [s["equipment"] for s in (proofing, baking) if s and s["equipment"]],
            "photos": sorted(photos_by_batch.get(bid, []), key=lambda p: (p["step_no"] or 0, p["phase"] != "start", p["created_at"])),
        }
        batch["next_mark"] = _next_mark(batch)
        batches.append(batch)
    batches.sort(key=lambda x: x["started_at"] or datetime.min.replace(tzinfo=TZ), reverse=True)

    pauses = []
    for r in raw_pauses or []:
        start, end = _as_local(r.get("start_at")), _as_local(r.get("end_at"))
        duration = (end - start).total_seconds() / 60 if start and end and end > start else 0.0
        pauses.append({
            "id": _text(r.get("id")),
            "user_name": _text(r.get("user_name")) or "—",
            "equipment": _text(r.get("equipment")) or "Без оборудования",
            "reason": _text(r.get("reason")) or "Без причины",
            "start_at": start,
            "end_at": end,
            "start_time": start.strftime("%H:%M") if start else _text(r.get("start_time")),
            "end_time": end.strftime("%H:%M") if end else _text(r.get("end_time")),
            "duration_min": round(duration, 2),
        })
    pauses.sort(key=lambda x: x["start_at"] or datetime.min.replace(tzinfo=TZ), reverse=True)

    equipment = [PROOFER] + [f"{o['name']} печь" for o in ovens]
    extra = sorted({e for b in batches for e in b["equipment"]} | {p["equipment"] for p in pauses})
    equipment += [e for e in extra if e not in equipment]
    return {
        "batches": batches,
        "pauses": pauses,
        "photos": photo_index,
        "ovens": ovens,
        "equipment": equipment,
        "issues": issues,
    }


# ── загрузка ──────────────────────────────────────────────────────────────────

def _fetch_from_demo():
    with open(PROOFING_DEMO_FILE, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload


async def _error_code(resp):
    try:
        return (await resp.json()).get("error", "")
    except (aiohttp.ContentTypeError, ValueError):
        return ""


async def _fetch_from_api(since):
    if not PROOFING_API_URL:
        raise RuntimeError("PROOFING_API_URL is not set")
    if not PROOFING_API_KEY:
        raise RuntimeError("PROOFING_API_KEY is not set")
    url = f"{PROOFING_API_URL}/api/dashboard/all"
    timeout = aiohttp.ClientTimeout(total=PROOFING_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params={"date_from": since.strftime("%Y-%m-%d")},
                               headers={"X-API-Key": PROOFING_API_KEY}) as resp:
            if resp.status == 401:
                raise RuntimeError("proofing API rejected the key (401)")
            if resp.status >= 400:
                code = await _error_code(resp)
                raise RuntimeError(f"proofing API error {resp.status}{': ' + code if code else ''}")
            payload = await resp.json()
    if not isinstance(payload, dict) or "operations" not in payload or "batches" not in payload:
        raise RuntimeError("proofing API returned an unexpected payload")
    return payload


def _placeholder_png(seed, width=320, height=240):
    """Картинка-заглушка для demo: фото в demo-файле есть только метаданными."""
    digest = hashlib.sha256(seed.encode()).digest()
    color = bytes((90 + digest[0] % 140, 90 + digest[1] % 140, 90 + digest[2] % 140))
    row = b"\x00" + color * width
    raw = zlib.compress(row * height, 9)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def valid_photo_id(photo_id):
    try:
        return str(uuid.UUID(str(photo_id))) == str(photo_id).lower()
    except (ValueError, TypeError, AttributeError):
        return False


class ProofingSource:
    """Снимок расстойки в памяти, обновляется не чаще CACHE_TTL — как у ФК и БХМ."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._snapshot = None
        self._fetched_at = 0.0
        self._error = ""
        self._photos = OrderedDict()
        self._photo_bytes = 0
        self._photo_gate = asyncio.Semaphore(4)

    @property
    def mode(self):
        return PROOFING_SOURCE_MODE

    @property
    def enabled(self):
        return PROOFING_SOURCE_MODE in ("api", "demo")

    def describe(self):
        return {
            "mode": f"proofing-{PROOFING_SOURCE_MODE}",
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
                if PROOFING_SOURCE_MODE == "demo":
                    payload = await asyncio.to_thread(_fetch_from_demo)
                elif PROOFING_SOURCE_MODE == "api":
                    payload = await _fetch_from_api(since)
                else:
                    raise RuntimeError("раздел расстойки выключен: не задан PROOFING_API_URL")
                self._snapshot = normalize(payload.get("operations", []), payload.get("batches", []),
                                           payload.get("pauses", []), payload.get("photos", []),
                                           payload.get("catalog"))
                self._fetched_at = time.time()
                self._error = ""
                log.info("proofing snapshot refreshed: batches=%s pauses=%s photos=%s mode=%s",
                         len(self._snapshot["batches"]), len(self._snapshot["pauses"]),
                         len(self._snapshot["photos"]), PROOFING_SOURCE_MODE)
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                log.error("proofing snapshot refresh failed: %s", self._error)
                if self._snapshot is None:
                    raise
            return self._snapshot

    # ── фото: по одному, по запросу человека ─────────────────────────────────

    def _remember(self, photo_id, item):
        self._photos[photo_id] = item
        self._photo_bytes += len(item[0])
        while self._photos and (len(self._photos) > PHOTO_CACHE_ITEMS or self._photo_bytes > PHOTO_CACHE_BYTES):
            _, (old, _) = self._photos.popitem(last=False)
            self._photo_bytes -= len(old)

    async def photo(self, photo_id):
        """→ (bytes, content_type). ValueError — кривой id, PhotoNotFound — нет такого фото."""
        if not valid_photo_id(photo_id):
            raise ValueError("bad photo id")
        if photo_id in self._photos:
            self._photos.move_to_end(photo_id)
            return self._photos[photo_id]
        snapshot = await self.get()
        # Отдаём только фото, известные по снимку: дашборд не служит открытым прокси к боту.
        if photo_id not in snapshot["photos"]:
            raise PhotoNotFound(photo_id)
        if PROOFING_SOURCE_MODE == "demo":
            item = (_placeholder_png(photo_id), "image/png")
        else:
            async with self._photo_gate:
                item = await self._fetch_photo(photo_id)
        self._remember(photo_id, item)
        return item

    async def _fetch_photo(self, photo_id):
        if not PROOFING_API_URL or not PROOFING_API_KEY:
            raise RuntimeError("proofing API is not configured")
        url = f"{PROOFING_API_URL}/api/dashboard/photo/{photo_id}"
        timeout = aiohttp.ClientTimeout(total=PROOFING_PHOTO_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={"X-API-Key": PROOFING_API_KEY}) as resp:
                if resp.status == 404:
                    raise PhotoNotFound(photo_id)
                if resp.status == 401:
                    raise RuntimeError("proofing API rejected the key (401)")
                if resp.status >= 400:
                    code = await _error_code(resp)
                    raise RuntimeError(f"proofing photo error {resp.status}{': ' + code if code else ''}")
                content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if content_type not in PHOTO_TYPES:
                    raise RuntimeError(f"proofing photo has unexpected type {content_type or '—'}")
                data = await resp.content.read(MAX_PHOTO_BYTES + 1)
                if len(data) > MAX_PHOTO_BYTES:
                    raise RuntimeError("proofing photo is too large")
        return data, content_type
