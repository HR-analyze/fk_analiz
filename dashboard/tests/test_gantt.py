#!/usr/bin/env python3
"""Раздел «Гант»: время операций всех цехов (полночь, окно смены), дорожки параллельных
операций, разобранный чат КЦ, общий тайминг по единому продукту, справочник соответствий,
API, выгрузки и то, что остальные разделы работают как раньше.

    python3 dashboard/tests/test_gantt.py
"""
import asyncio
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

_TMP = Path(tempfile.mkdtemp())
_TEST_ENV = {"SOURCE_MODE": "demo", "BHM_SOURCE_MODE": "demo", "PROOFING_SOURCE_MODE": "demo", "KC_SOURCE_MODE": "demo",
             "RELAXDEV_DEPLOY": "", "PLAN_DATABASE_URL": "", "TIMEZONE": "Europe/Moscow", "PLAN_UPLOAD_TOKEN": "tok",
             "PLAN_FILE": str(_TMP / "plans.json"), "EXCLUDE_DATES": ""}
_saved = {k: os.environ.get(k) for k in _TEST_ENV}
os.environ.update(_TEST_ENV)
try:
    import app as dashboard  # noqa: E402
    import gantt  # noqa: E402
    import gantt_exports  # noqa: E402
    import product_map  # noqa: E402
    from sources import TZ  # noqa: E402
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

import bhm_source  # noqa: E402
import kc_source  # noqa: E402
import plans  # noqa: E402
import proofing_source  # noqa: E402
import sources  # noqa: E402

sources.SOURCE_MODE = "demo"
bhm_source.BHM_SOURCE_MODE = "demo"
proofing_source.PROOFING_SOURCE_MODE = "demo"
kc_source.KC_SOURCE_MODE = "demo"
plans.PG_PLANS = False
plans.PLAN_FILE = _TMP / "plans.json"
plans.PLAN_UPLOAD_TOKEN = "tok"
dashboard.RELAXDEV_DEPLOY = False

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=TZ)


def at(day, hh, mm=0):
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ)


def fk_row(product, start, end, created, qty=100, status="closed", equipment="Rondo"):
    return {"id": product + start, "user_name": "Оператор", "equipment": equipment, "product": product,
            "start_time": start, "end_time": end, "qty": qty, "status": status, "created_at": created.isoformat(),
            "date": created.date().isoformat()}


def kc_op(product, operation, start, minutes=None, people=2, pcs=100, status="closed"):
    end = start + timedelta(minutes=minutes) if minutes else None
    return {"id": f"{product}-{operation}-{start:%H%M}", "product": product, "operation": operation, "people": people,
            "pcs": pcs if status == "closed" else None, "status": status, "start_at": start, "end_at": end,
            "closed_at": end, "duration_min": minutes, "valid": bool(minutes), "person_hours": None,
            "user_name": "Анна", "closed_by_name": "Мария" if end else ""}


# ── время и источники ─────────────────────────────────────────────────────────

def test_fk_clock_handles_midnight_and_record_time():
    # запись сделана в конце операции (00:15 следующих суток) — начало в прошлые сутки
    s, e = gantt._clock(at(8, 0, 15), "23:00", "00:15")
    assert s == at(7, 23) and e == at(8, 0, 15)
    # запись сделана в начале операции
    s, e = gantt._clock(at(7, 23, 0), "23:00", "00:15")
    assert s == at(7, 23) and e == at(8, 0, 15)
    assert gantt._clock(at(7, 9), "", "")[0] is None


def test_bars_from_every_section_people_only_in_kc():
    fk = {"production": [fk_row("Багет", "09:00", "10:30", at(7, 10, 30), qty=900)]}
    bhm = {"production": [{"id": "b1", "user_name": "Ольга", "equipment": "Diosna", "product": "Багет", "operation": "Замес теста",
                           "start_time": "07:00", "end_time": "07:20", "qty": 150.5, "status": "closed",
                           "created_at": at(7, 7).isoformat(), "date": "2026-10-07"}]}
    kc = {"operations": [kc_op("Эклер", "Начинка", at(7, 11), 60, people=3, pcs=600)]}
    bars = gantt.collect_bars({"fk": fk, "bhm": bhm, "kc": kc}, {"operations": [], "handoffs": []})
    f, b, k = bars["fk"][0], bars["bhm"][0], bars["kc"][0]
    assert (f["operation"], f["qty"], f["unit"], f["people"], f["equipment"]) == ("Формовка", 900, "шт", None, "Rondo")
    assert (b["operation"], b["unit"], b["equipment"]) == ("Замес теста", "кг", "Diosna")
    assert (k["people"], k["qty"]) == (3, 600)
    v = gantt.view(k, NOW)
    assert v["rate"] == 600.0 and v["rate_per_person"] == 200.0 and v["person_hours"] == 3.0
    assert gantt.view(f, NOW)["rate_per_person"] is None   # людей у ФК нет — на человека не считаем


def test_proofing_stages_become_two_operations():
    p = json.loads((HERE.parent / "proofing_demo_data.json").read_text(encoding="utf-8"))
    snap = proofing_source.normalize(p["operations"], p["batches"], p["pauses"], p["photos"], p["catalog"])
    bars = gantt.proofing_bars(snap)
    done = next(b for b in snap["batches"] if b["status"] == "done")
    mine = [x for x in bars if x["id"].startswith(f"pr-{done['id']}")]
    assert {x["stage"] for x in mine} == {"proofing", "baking"}
    baking = next(x for x in mine if x["stage"] == "baking")
    assert baking["qty"] == done["pieces_out"] and baking["equipment"].endswith("печь")
    assert not any(x["id"].startswith(f"pr-{b['id']}") for b in snap["batches"] if b["status"] == "cancelled" for x in bars)


# ── чат КЦ ────────────────────────────────────────────────────────────────────

def test_chat_history_is_consistent():
    h = gantt.load_history()
    ops = h["operations"]
    assert len(ops) >= 170 and len({o["id"] for o in ops}) == len(ops)
    for o in ops:
        assert o["flag"] in ("ok", "check", "invalid") and o["msgs"], o
        if o["start_at"] and o["end_at"]:
            assert o["end_at"] >= o["start_at"], o
        assert (o["status"] == "closed") == bool(o["end_at"]), o
        assert o["people"] is None or 1 <= o["people"] <= 50, o
        assert o["unit"] in ("", "шт", "кг", "пласт", "лист", "замес", "форма", "кольцо"), o
    assert {h_["product"] for h_ in h["handoffs"]} >= {"Чизкейк Сан-Себастьян", "Трубочка с заварным кремом"}
    # тексты сообщений и имена в файл не попадают
    raw = (HERE.parent / "kc_chat_history.json").read_text(encoding="utf-8")
    assert "Константин" not in raw and "text" not in json.loads(raw)["operations"][0]


def test_chat_bars_invalid_and_approx():
    h = gantt.load_history()
    bars = gantt.kc_bars(None, h)
    invalid = [b for b in bars if b["flag"] == "invalid"]
    assert invalid and all(gantt.view(b, NOW)["rate"] is None for b in invalid)
    approx = [b for b in bars if b["approx"]]
    assert approx and all(b["flag"] == "check" for b in approx)
    no_end = [b for b in bars if not b["end"]]
    assert no_end and all(not b["running"] and gantt.bar_end(b, NOW) is None for b in no_end)


# ── окно, дорожки ─────────────────────────────────────────────────────────────

def test_window_full_day_night_and_open_ops():
    kc = {"operations": [kc_op("Эклер", "Начинка", at(7, 23), 120),          # 23:00–01:00
                         kc_op("Эклер", "Глазировка", at(7, 10), 60),
                         kc_op("Эклер", "Декор", at(7, 15), status="open")]}  # бот: идёт до «сейчас»
    bars = gantt.collect_bars({"kc": kc}, {"operations": [], "handoffs": []})

    def ops(mode):
        s = gantt.build(bars, {}, {}, "2026-10-07", mode, ["kc"], now=NOW)
        return sorted(b["operation"] for r in s["rows"] for b in r["bars"])
    assert ops("full") == ["Глазировка", "Декор", "Начинка"]
    assert ops("day") == ["Глазировка", "Декор"]
    assert ops("night") == ["Декор", "Начинка"]
    s = gantt.build(bars, {}, {}, "2026-10-07", "full", ["kc"], now=NOW)
    running = next(b for r in s["rows"] for b in r["bars"] if b["operation"] == "Декор")
    assert running["running"] and running["draw_end"] == NOW.isoformat()


def test_parallel_operations_get_separate_lanes():
    kc = {"operations": [kc_op("Ром баба", "Накалывание", at(7, 9), 120), kc_op("Ром баба", "Накалывание", at(7, 10), 60),
                         kc_op("Ром баба", "Накалывание", at(7, 12), 30)]}
    s = gantt.build(gantt.collect_bars({"kc": kc}, {}), {}, {}, "2026-10-07", "full", ["kc"], now=NOW)
    row = s["rows"][0]
    assert row["lanes"] == 2 and [b["lane"] for b in row["bars"]] == [0, 1, 0]


# ── общий тайминг ─────────────────────────────────────────────────────────────

def _chain():
    bhm = {"production": [{"id": "b1", "user_name": "О", "equipment": "Diosna", "product": "Батон / ромашка",
                           "operation": "Замес теста", "start_time": "08:00", "end_time": "08:30", "qty": 200.0,
                           "status": "closed", "created_at": at(7, 8).isoformat(), "date": "2026-10-07"}]}
    fk = {"production": [fk_row("Батон классический", "09:00", "10:00", at(7, 10), qty=1000)]}
    return gantt.collect_bars({"bhm": bhm, "fk": fk}, {"operations": [], "handoffs": []})


def test_timing_links_sections_only_through_mapping():
    bars = _chain()
    loose = gantt.build(bars, {}, {}, "2026-10-07", "full", now=NOW)["timing"]
    assert {t["product"] for t in loose} == {"Батон / ромашка", "Батон классический"}
    assert all(t["stages_count"] == 1 for t in loose)
    mapping = {"bhm": {"Батон / ромашка": "Батон классический"}}
    linked = gantt.build(bars, {}, mapping, "2026-10-07", "full", now=NOW)["timing"]
    assert len(linked) == 1
    t = linked[0]
    st = {s["key"]: s for s in t["stages"]}
    assert t["stages_count"] == 2 and t["lead_min"] == 120             # 08:00 → 10:00
    assert st["fk"]["gap_min"] == 30                                   # ожидание между тестом и формовкой
    assert st["bhm"]["qty"] == "200 кг" and st["fk"]["qty"] == "1 000 шт"   # единицы не складываются
    assert st["pack"]["present"] is False and st["pack"]["source"] == "нет источника"


def test_packing_handoffs_from_chat():
    h = {"operations": [], "handoffs": [{"product": "Трубочка", "at": at(11, 14, 54).isoformat(), "qty": 930, "unit": "шт",
                                         "exact": False}]}
    kc = {"operations": [kc_op("Трубочка", "Начинка", at(11, 10, 39), 240, people=1, pcs=930)]}
    s = gantt.build(gantt.collect_bars({"kc": kc}, h), h, {}, "2026-10-11", "full", now=NOW)
    t = s["timing"][0]
    pack = next(x for x in t["stages"] if x["key"] == "pack")
    assert pack["handoffs"][0]["qty"] == 930 and s["handoffs"][0]["product"] == "Трубочка"


# ── справочник ────────────────────────────────────────────────────────────────

def test_product_map_template_roundtrip_and_errors():
    import openpyxl
    blob = product_map.template_xlsx({"bhm": ["Батон / ромашка"], "fk": ["Батон классический"]}, {})
    wb = openpyxl.load_workbook(io.BytesIO(blob))
    ws = wb.active
    assert [c.value for c in ws[1]] == ["Цех", "Продукт в цехе", "Единый продукт"]
    ws["C2"] = "Батон классический"
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    mapping, count = product_map.parse_xlsx(out)
    assert mapping == {"bhm": {"Батон / ромашка": "Батон классический"}} and count == 1
    bad = openpyxl.Workbook()
    bad.active.append(["Цех", "Продукт в цехе", "Единый продукт"])
    bad.active.append(["Склад", "Х", "Y"])
    buf = io.BytesIO()
    bad.save(buf)
    buf.seek(0)
    try:
        product_map.parse_xlsx(buf)
        raise AssertionError("ожидалась ошибка цеха")
    except product_map.MapError as exc:
        assert "Склад".lower() in str(exc)


def test_product_map_file_storage_does_not_touch_plans():
    plans_file = Path(plans.PLAN_FILE)
    before = plans_file.read_text(encoding="utf-8") if plans_file.exists() else None
    product_map.save({"fk": {"А": "Б"}}, "x.xlsx")
    assert product_map.load()["map"] == {"fk": {"А": "Б"}}
    after = plans_file.read_text(encoding="utf-8") if plans_file.exists() else None
    assert before == after
    product_map.save({}, "")


# ── API и выгрузки ────────────────────────────────────────────────────────────

async def _client():
    app = dashboard.build_app()
    app.on_startup.clear()
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class patched:
    def __init__(self, module, **values):
        self.module, self.values, self.old = module, values, {}

    def __enter__(self):
        for k, v in self.values.items():
            self.old[k] = getattr(self.module, k)
            setattr(self.module, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(self.module, k, v)


def test_api_meta_summary_and_one_section_down():
    async def run():
        dashboard.kc_source = kc_source.KcSource()
        client = await _client()
        try:
            m = await (await client.get("/api/meta?source=gantt")).json()
            assert m["ok"] and m["kind"] == "gantt" and m["days"] and m["history_ops"] >= 170
            d = await (await client.get("/api/summary?source=gantt&date=2026-10-07&window=full")).json()
            assert d["ok"] and d["totals"]["ops"] > 0 and d["totals"]["from_chat"] > 0
            assert set(d["totals"]["by_section"]) == {"БХМ", "ФК", "Расстойка", "КЦ"}
            only = await (await client.get("/api/summary?source=gantt&date=2026-10-07&sections=kc")).json()
            assert {r["section"] for r in only["rows"]} == {"kc"}
            # КЦ упал — Гант работает по остальным цехам и честно пишет, чего нет
            with patched(kc_source, KC_SOURCE_MODE="api", KC_API_URL="http://127.0.0.1:9", KC_API_KEY="k", KC_TIMEOUT=3):
                dashboard.kc_source = kc_source.KcSource()
                down = await (await client.get("/api/summary?source=gantt&date=2026-10-07")).json()
                assert down["ok"] and "kc" in down["section_errors"]
                assert {r["section"] for r in down["rows"]} >= {"proofing"}
                assert all(b["source"] == "chat" for r in down["rows"] if r["section"] == "kc" for b in r["bars"])
        finally:
            dashboard.kc_source = kc_source.KcSource()
            await client.close()
    asyncio.run(run())


def test_api_map_upload_needs_token_and_changes_timing():
    async def run():
        import openpyxl
        client = await _client()
        try:
            r = await client.get("/api/gantt/map/template")
            wb = openpyxl.load_workbook(io.BytesIO(await r.read()))
            ws = wb.active
            for row in ws.iter_rows(min_row=2):
                if row[0].value == "БХМ" and row[1].value == "Круассан классический":
                    row[2].value = "Круассан французский"
            buf = io.BytesIO()
            wb.save(buf)
            data = buf.getvalue()

            async def upload(token):
                from aiohttp import FormData
                form = FormData()
                form.add_field("file", data, filename="map.xlsx")
                return await client.post("/api/gantt/map/upload", data=form, headers={"X-Plan-Token": token})
            assert (await upload("wrong")).status == 401
            r = await upload("tok")
            assert r.status == 200 and (await r.json())["pairs"] == 1
            state = await (await client.get("/api/gantt/map")).json()
            assert state["map"] == {"bhm": {"Круассан классический": "Круассан французский"}}
            meta = await (await client.get("/api/meta?source=gantt")).json()
            assert "Круассан классический" not in meta["products"]
        finally:
            product_map.save({}, "")
            await client.close()
    asyncio.run(run())


def test_exports_xlsx_and_pdf():
    async def run():
        import openpyxl
        client = await _client()
        try:
            r = await client.get("/api/export/xlsx?source=gantt&date=2026-10-07&window=full")
            assert r.status == 200 and "gantt_2026-10-07_full" in r.headers["Content-Disposition"]
            wb = openpyxl.load_workbook(io.BytesIO(await r.read()))
            assert wb.sheetnames == ["Операции", "Общий тайминг", "Передано на фасовку", "Справочник продуктов", "Фильтры"]
            page = await (await client.get("/api/summary?source=gantt&date=2026-10-07&window=full")).json()
            assert wb["Операции"].max_row - 1 == page["totals"]["ops"]
            assert wb["Общий тайминг"].max_row - 1 == len(page["timing"])
            r = await client.get("/api/export/pdf?source=gantt&date=2026-10-07")
            assert r.status == 200 and (await r.read())[:4] == b"%PDF"
        finally:
            await client.close()
    asyncio.run(run())


def test_other_sections_unchanged():
    async def run():
        client = await _client()
        try:
            for src in ("fk", "bhm", "proofing", "kc"):
                r = await client.get(f"/api/summary?source={src}")
                assert r.status == 200 and (await r.json())["ok"], src
            assert (await (await client.get("/api/summary")).json())["source_key"] == "fk"
            meta = await (await client.get("/api/meta?source=fk")).json()
            assert meta["sources"]["gantt"] is True
        finally:
            await client.close()
    asyncio.run(run())


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ✅ {t.__name__}")
    print(f"ИТОГ: {len(tests)} тестов прошли")
