#!/usr/bin/env python3
"""Адаптер БХМ: ответ /api/dashboard/all → снимок в формате дашборда ФК.

Запуск без зависимостей (pytest тоже подхватит):
    python3 dashboard/tests/test_bhm_source.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aggregate  # noqa: E402
import bhm_source  # noqa: E402


def step(op, no, status="closed", kg=150.5, start="08:20", end="08:30", at="2026-10-01T05:20:00+00:00",
         equipment=None, batch="b1"):
    return {"id": f"{batch}-{no}", "batch_id": batch, "step_no": no, "user_name": "Иванова А.",
            "operation": op, "operation_name": op, "equipment": equipment, "product": "Хлеб картофельный",
            "start_time": start, "end_time": end, "start_at": at, "quantity_kg": kg, "status": status}


def batch_rows():
    return [
        step("dough_assembly", 0, equipment="Daxner"),
        step("dough_mixing", 1, equipment="Diosna", start="08:35", end="08:55", at="2026-10-01T05:35:00+00:00"),
        step("dough_rest", 2, start="08:55", end="09:30", at="2026-10-01T05:55:00+00:00"),
        step("dough_punch", 3, status="skipped", kg=None, start="09:30", end="09:30"),
        step("dough_to_forming", 4, start="09:32", end="09:32", at="2026-10-01T06:32:00+00:00"),
    ]


def test_batch_weight_counted_once():
    # Бот копирует вес партии во все шаги — в выпуск он должен попасть один раз.
    rows = bhm_source.normalize_operations(batch_rows(), volume_operation="dough_mixing")
    assert sum(r["qty"] for r in rows) == 150.5
    assert [r["qty"] for r in rows if r["operation"] == "dough_mixing"] == [150.5]


def test_skipped_and_cancelled_dropped():
    rows = batch_rows() + [step("dough_mixing", 1, status="cancelled", batch="b2")]
    out = bhm_source.normalize_operations(rows, volume_operation="dough_mixing")
    assert {r["status"] for r in out} == {"closed"}
    assert len(out) == 4


def test_fields_and_timezone():
    out = bhm_source.normalize_operations(batch_rows(), volume_operation="dough_mixing")
    mix = next(r for r in out if r["operation"] == "dough_mixing")
    # start_at в UTC → дата и created_at по TIMEZONE дашборда (Москва, +03:00)
    assert mix["created_at"].startswith("2026-10-01T08:35:00")
    assert mix["date"] == "2026-10-01"
    assert (mix["equipment"], mix["start_time"], mix["end_time"]) == ("Diosna", "08:35", "08:55")
    rest = next(r for r in out if r["operation"] == "dough_rest")
    assert rest["equipment"] == "Без оборудования" and rest["qty"] == 0


def test_open_step_without_end():
    out = bhm_source.normalize_operations([step("dough_rest", 2, status="open", end=None)])
    assert out[0]["status"] == "open" and out[0]["end_time"] == ""


def test_pauses_shape():
    out = bhm_source.normalize_pauses([{"id": "p1", "user_name": "Петров С.", "equipment": "Diosna",
                                        "reason": "Нет сырья", "start_time": "10:00", "end_time": "10:20",
                                        "start_at": "2026-10-01T07:00:00+00:00"}])
    assert out[0]["date"] == "2026-10-01" and out[0]["reason"] == "Нет сырья"
    assert set(out[0]) == {"id", "user_name", "equipment", "reason", "start_time", "end_time", "created_at", "date"}


def test_summary_in_kg():
    snapshot = {"production": bhm_source.normalize_operations(batch_rows(), volume_operation="dough_mixing"),
                "pauses": []}
    production, pauses = aggregate.apply_filters(snapshot)
    summary = aggregate.build_summary(production, pauses)
    summary["units"] = {"qty": "кг", "rate": "кг/ч"}
    assert summary["kpi"]["quantity"] == 150.5
    # 150,5 кг за 20 минут замеса = 451,5 кг/ч
    assert summary["kpi"]["avg_rate"] == 451.5
    lines = dict(aggregate.build_analysis(summary))["Итоги периода"]
    assert "Выпуск: 150 кг." in lines or "Выпуск: 151 кг." in lines
    assert all("шт" not in line for line in lines)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ✅ {t.__name__}")
    print(f"ИТОГ: {len(tests)} тестов прошли")
