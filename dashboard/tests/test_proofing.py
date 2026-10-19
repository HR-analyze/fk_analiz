#!/usr/bin/env python3
"""Раздел «Расстойка и отпекание»: нормализация API, правила расчёта, фильтры, отказ API,
прокси фото, Excel/PDF и то, что ФК и БХМ работают как раньше.

Без базы и без сети наружу (поддельный бот поднимается локально):
    python3 dashboard/tests/test_proofing.py
"""
import asyncio
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

# app.py читает окружение при импорте. Ставим тестовое и сразу возвращаем прежнее,
# чтобы не задеть другие тесты в том же процессе (test_pg_plans читает env на лету).
_TEST_ENV = {"SOURCE_MODE": "demo", "BHM_SOURCE_MODE": "demo", "PROOFING_SOURCE_MODE": "api",
             "PROOFING_API_URL": "http://127.0.0.1:9", "PROOFING_API_KEY": "secret-proofing-key",
             "RELAXDEV_DEPLOY": "", "PLAN_DATABASE_URL": "", "TIMEZONE": "Europe/Moscow",
             "PLAN_FILE": str(Path(tempfile.mkdtemp()) / "plans.json"), "EXCLUDE_DATES": ""}
_saved = {k: os.environ.get(k) for k in _TEST_ENV}
os.environ.update(_TEST_ENV)
try:
    import aggregate  # noqa: E402
    import app as dashboard  # noqa: E402
    import exports  # noqa: E402
    import proofing_aggregate as pa  # noqa: E402
    import proofing_exports as pe  # noqa: E402
    import proofing_source as ps  # noqa: E402
    from sources import TZ  # noqa: E402
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

# Модули могли быть импортированы раньше другим тестом (pytest гоняет всё в одном процессе):
# режимы источников и хранилище планов задаём явно, не полагаясь на порядок импорта.
import bhm_source  # noqa: E402
import plans  # noqa: E402
import sources  # noqa: E402

sources.SOURCE_MODE = "demo"
bhm_source.BHM_SOURCE_MODE = "demo"
plans.PG_PLANS = False
plans.PLAN_FILE = Path(_TEST_ENV["PLAN_FILE"])
dashboard.RELAXDEV_DEPLOY = False

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

UTC = timezone.utc
KEY = "secret-proofing-key"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PHOTO_ID = "11111111-2222-3333-4444-555555555555"


# ── фабрика данных в формате API бота ─────────────────────────────────────────

def at(day, hh, mm=0):
    """Местное (МСК) время → ISO в UTC, как отдаёт бот."""
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ).astimezone(UTC).isoformat()


def op(batch, code, start, end=None, status="closed", oven=None, racks=10, racks_end=None, pieces=500,
       pieces_end=None, user="Света", closer="Нихад", op_id=None):
    unit = "pods" if oven == "hearth" else "racks"
    names = {"hearth": "Подовая", "rotary": "Ротационная", "thermal_oil": "Термомасляная"}
    return {"id": op_id or f"{batch}-{code}", "batch_id": batch, "step_no": 0 if code == "proofing" else 1,
            "user_id": 1, "user_name": user, "operation": code,
            "operation_name": "Расстойка" if code == "proofing" else "Отпекание",
            "equipment": "Расстоечная камера" if code == "proofing" else f"{names[oven]} печь",
            "oven": oven, "product_code": "F08", "product": "Багет французский",
            "start_time": "", "end_time": "", "start_at": start, "end_at": end if status == "closed" else None,
            "rack_unit": unit, "racks_start": racks, "racks_end": racks_end if racks_end is not None else racks,
            "pieces_start": pieces, "pieces_end": pieces_end if pieces_end is not None else pieces,
            "status": status, "created_at": start, "closed_at": end,
            "closed_by_id": 2 if status == "closed" else None, "closed_by_name": closer if status == "closed" else None}


def batch(bid, status, started, finished=None, pieces_out=None, product="Багет французский", oven=None):
    return {"id": bid, "product_code": "F08", "product": product, "route": ["proofing", "baking"], "step": 2,
            "phase": "start", "oven": oven, "rack_unit": "racks", "racks": 10, "pieces": pieces_out or 500,
            "pieces_out": pieces_out, "status": status, "started_at": started, "last_at": finished or started,
            "last_mark": "", "last_by_name": "", "finished_at": finished, "user_id": 1, "user_name": "Света",
            "finished_by_id": None, "finished_by_name": None, "created_at": started, "updated_at": started}


def full_batch(bid, day, h, proof_min=45, gap=5, bake_min=30, oven="rotary", pieces=500, out=480,
               product="Багет французский", starter="Света", closer="Нихад", baker="Олег", status="done"):
    s = datetime(2026, 10, day, h, 0, tzinfo=TZ)
    pe_ = s + timedelta(minutes=proof_min)
    bs = pe_ + timedelta(minutes=gap)
    be = bs + timedelta(minutes=bake_min)
    iso = lambda dt: dt.astimezone(UTC).isoformat()  # noqa: E731
    ops = [op(bid, "proofing", iso(s), iso(pe_), user=starter, closer=closer, pieces=pieces),
           op(bid, "baking", iso(bs), iso(be), oven=oven, user=closer, closer=baker, pieces=pieces, pieces_end=out)]
    for o in ops:
        o["product"] = product
    b = batch(bid, status, iso(s), iso(be), pieces_out=out, product=product, oven=oven)
    return ops, b


def snapshot(ops, batches, pauses=(), photos=()):
    return ps.normalize(list(ops), list(batches), list(pauses), list(photos), None)


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=TZ)


def summary(snap, **filters):
    filters.setdefault("date_from", "2026-10-01")
    filters.setdefault("date_to", "2026-10-15")
    return pa.build_summary(snap, filters, now=NOW)


# ── нормализация ──────────────────────────────────────────────────────────────

def test_normalize_stages_units_timezone_and_operators():
    ops, b = full_batch("b1", 5, 8, oven="hearth")
    snap = snapshot(ops, [b])
    x = snap["batches"][0]
    assert x["status"] == "done" and x["oven"] == "hearth" and x["oven_unit"] == "pods"
    assert x["proofing"]["rack_unit"] == "racks" and x["baking"]["rack_unit"] == "pods"
    # UTC из API → МСК
    assert x["proofing"]["start_at"].utcoffset() == timedelta(hours=3)
    assert x["proofing"]["start_at"].hour == 8
    assert x["proofing"]["duration_min"] == 45 and x["baking"]["duration_min"] == 30
    # разные операторы на разных отметках
    assert x["proofing"]["started_by"] == "Света" and x["proofing"]["closed_by"] == "Нихад"
    assert x["baking"]["started_by"] == "Нихад" and x["baking"]["closed_by"] == "Олег"
    assert x["employees"] == ["Нихад", "Олег", "Света"]
    assert x["pieces_in"] == 500 and x["pieces_out"] == 480


def test_proofing_over_midnight_duration():
    start = datetime(2026, 10, 5, 23, 40, tzinfo=TZ)
    end = start + timedelta(minutes=50)
    o = op("b1", "proofing", start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat())
    snap = snapshot([o], [batch("b1", "active", o["start_at"])])
    assert snap["batches"][0]["proofing"]["duration_min"] == 50


# ── выпуск без двойного счёта ─────────────────────────────────────────────────

def test_output_counted_once_per_batch_even_with_duplicate_rows():
    ops, b = full_batch("b1", 5, 8, out=480)
    dup_same_id = dict(ops[1])                    # повтор той же строки
    dup_step = dict(ops[1], id="b1-baking-copy")  # второй вариант того же шага
    snap = snapshot(ops + [dup_same_id, dup_step], [b, dict(b)])
    s = summary(snap)
    assert s["kpi"]["completed"] == 1 and s["kpi"]["output"] == 480
    assert snap["issues"]["duplicate_ops"] == 2
    assert len(snap["batches"]) == 1
    assert sum(d["output"] for d in s["daily"]) == 480
    assert sum(h["value"] for h in s["hourly"]) == 480
    assert sum(o["output"] for o in s["ovens"]) == 480
    assert sum(p["output"] for p in s["products"]) == 480


def test_output_from_baking_end_and_mismatch_flagged():
    ops, b = full_batch("b1", 5, 8, out=480)
    b["pieces_out"] = 470  # у партии другое число — берём закрытое отпекание, расхождение видно
    snap = snapshot(ops, [b])
    assert summary(snap)["kpi"]["output"] == 480
    assert snap["issues"]["pieces_mismatch"] == 1


def test_proofing_pieces_are_not_output():
    ops, b = full_batch("b1", 5, 8)
    b.update(status="active", finished_at=None, pieces_out=None)
    snap = snapshot(ops[:1], [b])  # только расстойка
    s = summary(snap)
    assert s["kpi"]["output"] == 0 and s["kpi"]["completed"] == 0
    assert s["kpi"]["started"] == 1


# ── длительность и производительность ─────────────────────────────────────────

def test_rate_is_total_output_over_total_baking_hours():
    a_ops, a = full_batch("a", 5, 8, bake_min=30, out=600)   # 1200 шт/ч
    b_ops, b = full_batch("b", 5, 10, bake_min=60, out=600)  # 600 шт/ч
    s = summary(snapshot(a_ops + b_ops, [a, b]))
    assert s["kpi"]["rate"] == 800.0              # 1200 шт / 1,5 ч, а не (1200+600)/2
    assert s["kpi"]["avg_baking"] == 45.0
    assert s["kpi"]["avg_proofing"] == 45.0


def test_invalid_durations_excluded_from_averages_and_rate():
    a_ops, a = full_batch("a", 5, 8, bake_min=30, out=600)
    b_ops, b = full_batch("b", 5, 10, bake_min=30, out=600)
    b_ops[1]["end_at"] = b_ops[1]["start_at"]           # нулевое отпекание
    c_ops, c = full_batch("c", 5, 12, proof_min=20 * 60, out=300)  # расстойка 20 ч > лимита
    s = summary(snapshot(a_ops + b_ops + c_ops, [a, b, c]))
    assert s["kpi"]["completed"] == 3 and s["kpi"]["output"] == 1500   # выпуск — факт, он есть
    assert s["kpi"]["rate"] == round((600 + 300) / 1.0, 1)              # без нулевого отпекания
    assert s["kpi"]["avg_proofing"] == 45.0                             # 20-часовая не вошла
    assert s["kpi"]["invalid_stages"] == 2


def test_no_division_by_zero_and_no_data_is_none():
    s = summary(snapshot([], []))
    k = s["kpi"]
    assert k["rate"] is None and k["avg_proofing"] is None and k["avg_baking"] is None
    assert k["output"] == 0 and k["completed"] == 0
    texts = dict((t, v) for t, v, _ in pa.kpi_rows(k))
    assert texts["Выпуск, шт"] == "нет данных"
    assert texts["Производительность отпекания, шт/ч"] == "нет данных"
    assert texts["Средняя расстойка"] == "нет данных"


# ── отменённые и незавершённые ────────────────────────────────────────────────

def test_cancelled_batch_never_in_output_or_averages():
    ops, b = full_batch("x", 5, 8, out=480)
    ops[1]["status"] = "cancelled"
    ops[1]["end_at"] = None
    b.update(status="cancelled", pieces_out=None)
    ok_ops, ok = full_batch("y", 5, 9, out=300, proof_min=60)
    s = summary(snapshot(ops + ok_ops, [b, ok]))
    assert s["kpi"]["started"] == 2 and s["kpi"]["cancelled"] == 1
    assert s["kpi"]["output"] == 300 and s["kpi"]["completed"] == 1
    assert s["kpi"]["avg_proofing"] == 60.0   # закрытая расстойка отменённой партии не вошла
    assert all(r["stages"] == 1 for r in s["load"]["rows"] if r["key"] == "proofer")


def test_open_stages_in_work_and_listed():
    start = datetime(2026, 10, 15, 22, 0, tzinfo=TZ)
    o = op("open1", "proofing", start.astimezone(UTC).isoformat(), status="open")
    b = batch("open1", "active", o["start_at"])
    s = summary(snapshot([o], [b]))
    assert s["kpi"]["in_work"] == 1 and s["kpi"]["completed"] == 0
    assert s["kpi"]["avg_proofing"] is None    # незакрытая расстойка в среднее не идёт
    row = s["open_stages"][0]
    assert row["stage"] == "Расстойка" and row["stale"]  # с 15.10 22:00 до NOW — дольше 18 ч
    assert s["batches"][0]["next_mark"] == "Конец расстойки"


def test_in_work_at_end_of_past_period():
    # партия начата 05.10 в 23:30 и отпечена 06.10 — на конец 05.10 она «в работе»
    ops, b = full_batch("n", 5, 23, proof_min=60, bake_min=30)
    s = pa.build_summary(snapshot(ops, [b]), {"date_from": "2026-10-05", "date_to": "2026-10-05"}, now=NOW)
    assert s["kpi"]["in_work"] == 1 and s["kpi"]["started"] == 1 and s["kpi"]["completed"] == 0


# ── фильтры и часовой пояс ────────────────────────────────────────────────────

def test_event_dates_use_local_timezone():
    # 21:30 UTC 05.10 = 00:30 МСК 06.10: партия начата 06.10, а не 05.10
    s_utc = "2026-10-05T21:30:00+00:00"
    e_utc = "2026-10-05T22:10:00+00:00"
    o = op("t", "proofing", s_utc, e_utc)
    snap = snapshot([o], [batch("t", "active", s_utc)])
    assert pa.build_summary(snap, {"date_from": "2026-10-05", "date_to": "2026-10-05"}, now=NOW)["kpi"]["started"] == 0
    assert pa.build_summary(snap, {"date_from": "2026-10-06", "date_to": "2026-10-06"}, now=NOW)["kpi"]["started"] == 1


def test_output_hour_is_baking_end_hour_not_proofing_start():
    ops, b = full_batch("h", 5, 8, proof_min=50, gap=5, bake_min=30)  # старт 08:00, конец отпекания 09:25
    snap = snapshot(ops, [b])
    s = summary(snap)
    assert s["hourly"][9]["value"] == 480 and s["hourly"][8]["value"] == 0
    assert summary(snap, hour="9")["kpi"]["output"] == 480
    assert summary(snap, hour="8")["kpi"]["output"] == 0
    assert summary(snap, hour="8")["kpi"]["started"] == 1


def test_output_date_is_baking_end_date():
    ops, b = full_batch("d", 5, 23, proof_min=50, bake_min=30)  # отпекание кончилось 06.10
    snap = snapshot(ops, [b])
    s5 = pa.build_summary(snap, {"date_from": "2026-10-05", "date_to": "2026-10-05"}, now=NOW)
    s6 = pa.build_summary(snap, {"date_from": "2026-10-06", "date_to": "2026-10-06"}, now=NOW)
    assert (s5["kpi"]["started"], s5["kpi"]["output"]) == (1, 0)
    assert (s6["kpi"]["started"], s6["kpi"]["output"]) == (0, 480)


def test_shift_filter():
    day_ops, day = full_batch("day", 5, 10)
    night_ops, night = full_batch("night", 5, 21)
    snap = snapshot(day_ops + night_ops, [day, night])
    assert summary(snap, shift="day")["kpi"]["completed"] == 1
    assert summary(snap, shift="night")["kpi"]["output"] == 480
    assert summary(snap)["kpi"]["completed"] == 2


def test_employee_filter_matches_any_role():
    ops, b = full_batch("e", 5, 8, starter="Анна", closer="Борис", baker="Вера")
    other_ops, other = full_batch("o", 5, 9, starter="Глеб", closer="Глеб", baker="Глеб")
    snap = snapshot(ops + other_ops, [b, other])
    for name in ("Анна", "Вера"):
        k = summary(snap, employee=name)["kpi"]
        assert k["completed"] == 1 and k["output"] == 480, name
    emp = {e["employee"]: e for e in summary(snap)["employees"]}
    assert emp["Анна"]["start_proofing"] == 1 and emp["Анна"]["end_baking"] == 0
    assert emp["Вера"]["end_baking"] == 1 and emp["Вера"]["output_closed"] == 480


def test_oven_product_equipment_status_filters():
    a_ops, a = full_batch("a", 5, 8, oven="hearth", product="Чиабатта", out=200)
    b_ops, b = full_batch("b", 5, 9, oven="rotary", product="Багет", out=300)
    c_ops, c = full_batch("c", 5, 10, oven="rotary", product="Багет", out=100)
    c_ops = c_ops[:1]
    c.update(status="active", finished_at=None, pieces_out=None)
    pause = {"id": "p", "user_name": "Света", "equipment": "Подовая печь", "reason": "Нет продукта",
             "start_time": "", "end_time": "", "start_at": at(5, 12), "end_at": at(5, 12, 30)}
    snap = snapshot(a_ops + b_ops + c_ops, [a, b, c], [pause])
    assert summary(snap, oven="hearth")["kpi"]["output"] == 200
    assert summary(snap, oven="hearth")["kpi"]["pauses"] == 1
    assert summary(snap, oven="rotary")["kpi"]["pauses"] == 0
    assert summary(snap, product="Багет")["kpi"]["started"] == 2
    assert summary(snap, equipment="Ротационная печь")["kpi"]["output"] == 300
    assert summary(snap, status="active")["kpi"]["started"] == 1
    assert summary(snap, status="active")["kpi"]["output"] == 0
    assert summary(snap, status="done")["kpi"]["output"] == 500


def test_pods_and_racks_never_summed():
    a_ops, a = full_batch("a", 5, 8, oven="hearth")
    a_ops[1]["racks_start"] = 6
    b_ops, b = full_batch("b", 5, 9, oven="rotary")
    b_ops[1]["racks_start"] = 12
    s = summary(snapshot(a_ops + b_ops, [a, b]))
    ovens = {o["code"]: o for o in s["ovens"]}
    assert ovens["hearth"]["loaded_text"] == "6 подов" and ovens["rotary"]["loaded_text"] == "12 шпилек"
    assert not any("18" in line for _, lines in pa.build_analysis(s) for line in lines if "Загружено" in line)


def test_load_does_not_double_count_parallel_batches():
    # две партии в ротационной одновременно 08:00–09:00 → печь занята 1 ч, а не 2
    a_ops, a = full_batch("a", 5, 6, proof_min=110, gap=10, bake_min=60)
    b_ops, b = full_batch("b", 5, 6, proof_min=115, gap=5, bake_min=60)
    s = pa.build_summary(snapshot(a_ops + b_ops, [a, b]), {"date_from": "2026-10-05", "date_to": "2026-10-05"}, now=NOW)
    rotary = next(r for r in s["load"]["rows"] if r["key"] == "rotary")
    assert rotary["busy_hours"] == 1.0 and rotary["sum_hours"] == 2.0 and rotary["max_parallel"] == 2
    assert rotary["hours"][8]["pct"] == 100.0 and rotary["hours"][8]["parallel"] == 2.0
    proofer = next(r for r in s["load"]["rows"] if r["key"] == "proofer")
    assert proofer["busy_hours"] == round(115 / 60, 1)


def test_lfl_previous_period_same_filters():
    cur_ops, cur = full_batch("c", 12, 9, out=600)
    prev_ops, prev = full_batch("p", 5, 9, out=400)
    s = pa.build_summary(snapshot(cur_ops + prev_ops, [cur, prev]),
                         {"date_from": "2026-10-09", "date_to": "2026-10-15"}, now=NOW)
    assert s["lfl"]["available"] and s["lfl"]["prev_label"] == "02.10–08.10"
    assert s["lfl"]["kpi"]["output"] == {"prev": 400, "change": 50.0}
    week = {d["date"]: d for d in s["daily"]}["2026-10-12"]
    assert week["lfl_date"] == "2026-10-05" and week["lfl_output"] == 400


# ── API: недоступность, ключ, фото ────────────────────────────────────────────

def demo_payload():
    return json.loads((HERE.parent / "proofing_demo_data.json").read_text(encoding="utf-8"))


def fake_bot(state):
    async def all_(request):
        state["calls"].append({"path": request.path, "query": dict(request.query), "key": request.headers.get("X-API-Key")})
        if request.headers.get("X-API-Key") != KEY:
            return web.json_response({"error": "unauthorized"}, status=401)
        if state.get("fail"):
            return web.json_response({"error": "server_error"}, status=500)
        return web.json_response(state["payload"])

    async def photo(request):
        state["calls"].append({"path": request.path, "key": request.headers.get("X-API-Key")})
        if request.headers.get("X-API-Key") != KEY:
            return web.json_response({"error": "unauthorized"}, status=401)
        if request.match_info["id"] != PHOTO_ID:
            return web.json_response({"error": "not_found"}, status=404)
        return web.Response(body=JPEG, content_type="image/jpeg")

    app = web.Application()
    app.router.add_get("/api/dashboard/all", all_)
    app.router.add_get("/api/dashboard/photo/{id}", photo)
    return app


class patched:
    """Подмена настроек модулей на время теста (модули читают их при каждом вызове)."""

    def __init__(self, module, **values):
        self.module, self.values, self.old = module, values, {}

    def __enter__(self):
        for k, v in self.values.items():
            self.old[k] = getattr(self.module, k)
            setattr(self.module, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(self.module, k, v)


def payload_with_photo():
    ops, b = full_batch("b1", 5, 8)
    return {"ok": True, "operations": ops, "batches": [b], "pauses": [],
            "photos": [{"id": PHOTO_ID, "batch_id": "b1", "step_no": 0, "phase": "start", "operation": "proofing",
                        "mark": "Начало расстойки", "content_type": "image/jpeg", "size": 68, "user_name": "Света",
                        "created_at": ops[0]["start_at"]}],
            "catalog": {"ovens": ps.DEFAULT_OVENS}}


def test_source_api_errors_and_cache():
    async def run():
        state = {"calls": [], "payload": payload_with_photo()}
        server = TestServer(fake_bot(state))
        await server.start_server()
        url = str(server.make_url("")).rstrip("/")
        try:
            with patched(ps, PROOFING_API_URL=url, PROOFING_API_KEY=KEY, PROOFING_SOURCE_MODE="api"):
                src = ps.ProofingSource()
                snap = await src.get()
                assert len(snap["batches"]) == 1 and src.describe()["error"] == ""
                call = state["calls"][0]
                assert call["key"] == KEY and "api_key" not in call["query"] and "date_from" in call["query"]
                assert KEY not in json.dumps(src.describe())
                # бот упал — остаётся последний снимок, ошибка видна в describe()
                state["fail"] = True
                again = await src.get(force=True)
                assert again is snap and "500" in src.describe()["error"]
            with patched(ps, PROOFING_API_URL=url, PROOFING_API_KEY="wrong", PROOFING_SOURCE_MODE="api"):
                try:
                    await ps.ProofingSource().get()
                    raise AssertionError("должна быть ошибка ключа")
                except RuntimeError as exc:
                    assert "401" in str(exc)
            with patched(ps, PROOFING_API_URL="http://127.0.0.1:9", PROOFING_API_KEY=KEY,
                         PROOFING_SOURCE_MODE="api", PROOFING_TIMEOUT=3):
                try:
                    await ps.ProofingSource().get()
                    raise AssertionError("должна быть ошибка соединения")
                except Exception as exc:  # aiohttp.ClientConnectorError
                    assert "secret" not in str(exc)
        finally:
            await server.close()
    asyncio.run(run())


async def _client():
    app = dashboard.build_app()
    app.on_startup.clear()  # хранилище планов здесь не нужно
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def test_app_proofing_down_does_not_break_fk_and_bhm():
    async def run():
        dashboard.proofing_source = ps.ProofingSource()
        dashboard.SOURCES["proofing"]["data"] = dashboard.proofing_source
        client = await _client()
        try:
            with patched(ps, PROOFING_API_URL="http://127.0.0.1:9", PROOFING_SOURCE_MODE="api", PROOFING_TIMEOUT=3):
                r = await client.get("/api/summary?source=proofing")
                body = await r.json()
                assert r.status == 503 and body["ok"] is False and "API печного цеха (расстойка) недоступно" in body["error"]
                assert KEY not in json.dumps(body)
                r = await client.get("/api/meta?source=proofing")
                assert r.status == 503
                r = await client.get("/api/export/pdf?source=proofing")
                assert r.status == 503
                for src in ("fk", "bhm"):
                    r = await client.get(f"/api/summary?source={src}")
                    assert r.status == 200, src
                    assert (await r.json())["ok"] is True
                r = await client.get("/health")
                h = await r.json()
                assert r.status == 200 and h["ok"] and "proofing" in h
                assert KEY not in json.dumps(h)
                r = await client.get("/api/meta?source=fk")
                sources_state = (await r.json())["sources"]
                assert {k: sources_state[k] for k in ("fk", "bhm", "proofing")} == {"fk": True, "bhm": True, "proofing": True}
            with patched(ps, PROOFING_SOURCE_MODE="off"):
                r = await client.get("/api/summary?source=proofing")
                assert r.status == 503 and "не настроен" in (await r.json())["error"]
        finally:
            await client.close()
    asyncio.run(run())


def test_app_photo_proxy_hides_key():
    async def run():
        state = {"calls": [], "payload": payload_with_photo()}
        bot = TestServer(fake_bot(state))
        await bot.start_server()
        url = str(bot.make_url("")).rstrip("/")
        dashboard.proofing_source = ps.ProofingSource()
        dashboard.SOURCES["proofing"]["data"] = dashboard.proofing_source
        client = await _client()
        try:
            with patched(ps, PROOFING_API_URL=url, PROOFING_API_KEY=KEY, PROOFING_SOURCE_MODE="api"):
                r = await client.get("/api/summary?source=proofing&date_from=2026-10-01&date_to=2026-10-10")
                text = await r.text()
                assert r.status == 200 and KEY not in text and url not in text
                data = json.loads(text)
                assert data["batches"][0]["photos"][0]["id"] == PHOTO_ID
                calls_before = len(state["calls"])
                r = await client.get(f"/api/proofing/photo/{PHOTO_ID}")
                assert r.status == 200 and r.content_type == "image/jpeg" and await r.read() == JPEG
                assert KEY not in json.dumps(dict(r.headers))
                photo_calls = [c for c in state["calls"][calls_before:] if "photo" in c["path"]]
                assert len(photo_calls) == 1 and photo_calls[0]["key"] == KEY
                r = await client.get(f"/api/proofing/photo/{PHOTO_ID}")  # из кеша — в бот не ходим
                assert r.status == 200
                assert len([c for c in state["calls"] if "photo" in c["path"]]) == 1
                r = await client.get("/api/proofing/photo/not-a-uuid")
                assert r.status == 400
                r = await client.get("/api/proofing/photo/99999999-2222-3333-4444-555555555555")
                assert r.status == 404   # не из снимка — к боту даже не обращаемся
                assert len([c for c in state["calls"] if "photo" in c["path"]]) == 1
                # авторизация дашборда защищает и раздел расстойки, и фото
                with patched(dashboard, RELAXDEV_DEPLOY=True, DASHBOARD_AUTH=True, DASHBOARD_USER="u", DASHBOARD_PASSWORD="p"):
                    for path in (f"/api/proofing/photo/{PHOTO_ID}", "/api/summary?source=proofing"):
                        assert (await client.get(path)).status == 401
                    assert (await client.get("/health")).status == 200
        finally:
            await client.close()
            await bot.close()
    asyncio.run(run())


# ── Excel и PDF ───────────────────────────────────────────────────────────────

def demo_summary():
    d = demo_payload()
    snap = ps.normalize(d["operations"], d["batches"], d["pauses"], d["photos"], d["catalog"])
    s = pa.build_summary(snap, {"date_from": "2026-10-05", "date_to": "2026-10-11"}, now=NOW)
    return s, [("Источник", "Расстойка"), ("Период", "2026-10-05 – 2026-10-11")]


def test_excel_has_every_widget_and_same_numbers():
    import openpyxl
    s, filters = demo_summary()
    wb = openpyxl.load_workbook(io.BytesIO(pe.build_xlsx(s, "all", filters)))
    titles = [pe.widget_rows(s, w)[0][:31] for w in pe.ALL_WIDGETS]
    assert wb.sheetnames == titles + ["Фильтры"]
    daily = wb["Выпуск по дням"]
    assert sum(r[4] for r in daily.iter_rows(min_row=2, values_only=True)) == s["kpi"]["output"]
    hourly = wb["Выпуск по часам"]
    assert sum(r[1] for r in hourly.iter_rows(min_row=2, values_only=True)) == s["kpi"]["output"]
    batches = wb["Партии"]
    assert batches.max_row - 1 == len(s["batches"])
    one = openpyxl.load_workbook(io.BytesIO(pe.build_xlsx(s, "ovens", filters)))
    assert one.sheetnames == ["По печам", "Фильтры"]


def test_pdf_builds():
    s, filters = demo_summary()
    blob = pe.build_pdf(s, pa.build_analysis(s, filters), filters)
    assert blob[:4] == b"%PDF" and len(blob) > 10_000


def test_app_exports_use_same_filters():
    async def run():
        with patched(ps, PROOFING_SOURCE_MODE="demo"):
            dashboard.proofing_source = ps.ProofingSource()
            dashboard.SOURCES["proofing"]["data"] = dashboard.proofing_source
            client = await _client()
            try:
                import openpyxl
                q = "source=proofing&date_from=2026-10-05&date_to=2026-10-11&oven=rotary&shift=day"
                page = await (await client.get("/api/summary?" + q)).json()
                r = await client.get("/api/export/xlsx?widget=kpi&" + q)
                assert r.status == 200 and "proofing_kpi_2026-10-05_2026-10-11" in r.headers["Content-Disposition"]
                wb = openpyxl.load_workbook(io.BytesIO(await r.read()))
                rows = {row[0]: row[1] for row in wb["KPI"].iter_rows(min_row=2, values_only=True)}
                assert rows["Выпуск, шт"] == pa.fmt_int(page["kpi"]["output"])
                filt = {row[0]: row[1] for row in wb["Фильтры"].iter_rows(min_row=2, values_only=True)}
                assert filt["Тип печи"] == "Ротационная" and filt["Смена"].startswith("дневная")
                r = await client.get("/api/export/pdf?" + q)
                assert r.status == 200 and (await r.read())[:4] == b"%PDF"
                r = await client.get("/api/export/xlsx?widget=plan&" + q)
                assert r.status == 400  # плана у расстойки нет
            finally:
                await client.close()
    asyncio.run(run())


# ── ФК и БХМ не изменились ───────────────────────────────────────────────────

FK_SUMMARY_KEYS = {"kpi", "daily", "hourly", "hourly_target", "hourly_below", "plan", "by_equipment",
                   "top_products", "pause_reasons", "work_vs_stop", "top_employees", "product_timings",
                   "equipment_timings", "employee_timings", "detail", "lfl", "source", "source_key",
                   "source_title", "units", "plan_enabled", "excluded_dates", "ok", "filters", "analysis"}


def test_fk_and_bhm_unchanged():
    async def run():
        client = await _client()
        try:
            for src, unit in (("fk", "шт"), ("bhm", "кг")):
                r = await client.get(f"/api/summary?source={src}")
                data = await r.json()
                assert set(data) == FK_SUMMARY_KEYS, set(data) ^ FK_SUMMARY_KEYS
                assert data["units"]["qty"] == unit and "kind" not in data
            r = await client.get("/api/summary")       # старые ссылки без source= — это ФК
            assert (await r.json())["source_key"] == "fk"
            r = await client.get("/api/export/xlsx?source=fk")
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(await r.read()))
            fk = await (await client.get("/api/summary?source=fk")).json()
            assert wb.sheetnames == [exports.widget_rows(fk, w)[0][:31] for w in exports.ALL_WIDGETS] + ["Фильтры"]
            r = await client.get("/api/export/pdf?source=bhm")
            assert r.status == 200 and (await r.read())[:4] == b"%PDF"
            # FK-расчёт тот же, что и напрямую через aggregate
            snap = await dashboard.source.get()
            prod, pauses = aggregate.apply_filters(snap)
            direct = aggregate.build_summary(prod, pauses)["kpi"]
            page = await (await client.get("/api/summary?source=fk")).json()
            assert page["kpi"] == direct
        finally:
            await client.close()
    asyncio.run(run())


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ✅ {t.__name__}")
    print(f"ИТОГ: {len(tests)} тестов прошли")
