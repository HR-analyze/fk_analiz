#!/usr/bin/env python3
"""Генератор dashboard/kc_demo_data.json — ответа /api/dashboard/all бота кондитерки.

Данные выдуманные, по форме — как API HR-analyze/fk_conditerka (bot.py: OPERATION_COLUMNS,
PAUSE_COLUMNS + metrics()): время в UTC, открытые и отменённые операции, работа через
полночь, одна продукция проходит несколько операций с теми же штуками.

    python3 dashboard/tests/make_kc_demo.py
"""
import json
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
OUT = Path(__file__).resolve().parent.parent / "kc_demo_data.json"
FIRST, DAYS = date(2026, 9, 28), 14
NS = uuid.UUID("0b6f5a4e-3c1d-4e2f-8a9b-7c6d5e4f3a21")
# продукция → [(операция, минут на партию, людей, шт)]
ROUTES = {
    "Ромовая баба": [("Накалывание", 180, 1, 600), ("Купание", 120, 2, 600), ("Декорирование", 60, 3, 600)],
    "Эклер": [("Отсадка", 50, 2, 400), ("Начинка", 70, 2, 400), ("Глазировка", 45, 2, 400)],
    "Торт Медовик": [("Сборка", 90, 3, 40), ("Пропитка", 30, 1, 40), ("Декорирование", 60, 2, 40)],
    "Макарон": [("Отсадка", 60, 2, 900), ("Сборка", 80, 3, 450)],
    "Тарталетка ягодная": [("Заполнение", 40, 2, 300), ("Декорирование", 50, 2, 300)],
}
PEOPLE = ["Анна С.", "Мария К.", "Ольга Т.", "Дина Р.", "Ирина В.", "Фарида Н."]
REASONS = ["Поломка оборудования", "Нет сырья", "Нет персонала", "Техническая проблема", "Качество продукции", "Нет инвентаря"]


def uid(*parts):
    return str(uuid.uuid5(NS, "/".join(map(str, parts))))


def utc(dt):
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def hhmm(dt):
    return dt.astimezone(TZ).strftime("%H:%M") if dt else None


def metrics(start, end, people, pcs, status):
    # как bot.metrics(): у незавершённых — null
    if status != "closed" or not end:
        return {"duration_min": None, "person_hours": None, "pcs_per_hour": None, "pcs_per_person_hour": None}
    minutes = int((end - start).total_seconds() // 60)
    hours = minutes / 60
    ph = round(hours * people, 2)
    return {"duration_min": minutes, "person_hours": ph, "pcs_per_hour": round(pcs / hours, 1) if pcs and hours else None,
            "pcs_per_person_hour": round(pcs / ph, 1) if pcs and ph else None}


def main():
    rnd = random.Random(20261012)
    ops, pauses = [], []
    cut = datetime.combine(FIRST + timedelta(days=DAYS - 1), datetime.min.time(), TZ) + timedelta(hours=19)
    n = 0
    for day_no in range(DAYS):
        day = FIRST + timedelta(days=day_no)
        # дневная смена и часть ночной: партии продукции, операции идут друг за другом
        # плюс одна поздняя партия: её операции переходят через полночь в ночную смену
        starts = [rnd.choice(range(7 * 60, 19 * 60, 15)) for _ in range(rnd.randint(3, 5))] + [rnd.choice(range(22 * 60, 23 * 60, 15))]
        for minute in starts:
            product = rnd.choice(list(ROUTES))
            t = datetime.combine(day, datetime.min.time(), TZ) + timedelta(minutes=minute)
            for operation, minutes, people, pcs in ROUTES[product]:
                n += 1
                t += timedelta(minutes=rnd.randint(5, 40))
                start = t
                dur = max(10, int(minutes * rnd.uniform(0.75, 1.3)))
                ppl = max(1, people + rnd.choice([0, 0, 0, 1, -1]))
                end = start + timedelta(minutes=dur)
                qty = pcs if rnd.random() > 0.15 else int(pcs * rnd.uniform(0.6, 1.0))
                starter, closer = rnd.choice(PEOPLE), rnd.choice(PEOPLE)
                status = "closed"
                if end > cut:
                    status = "open"
                elif rnd.random() < 0.03:
                    status = "cancelled"
                closed_at = end if status == "closed" else (start + timedelta(minutes=7) if status == "cancelled" else None)
                row = {"id": uid("op", n), "product": product, "operation": operation, "people": ppl,
                       "quantity_pcs": qty if status == "closed" else None,
                       "start_time": hhmm(start), "end_time": hhmm(end) if status == "closed" else None,
                       "start_at": utc(start), "end_at": utc(end) if status == "closed" else None, "status": status,
                       "user_id": 1, "user_name": starter, "created_at": utc(start), "closed_at": utc(closed_at),
                       "closed_by_id": 2 if closed_at else None, "closed_by_name": closer if closed_at else None}
                row.update(metrics(start, end, ppl, row["quantity_pcs"], status))
                ops.append(row)
                t = end
        for k in range(rnd.choice([0, 0, 1, 1, 2])):
            s = datetime.combine(day, datetime.min.time(), TZ) + timedelta(minutes=rnd.randint(8 * 60, 22 * 60))
            e = s + timedelta(minutes=rnd.randint(10, 60))
            pauses.append({"id": uid("pause", day_no, k), "user_id": 1, "user_name": rnd.choice(PEOPLE),
                           "reason": rnd.choice(REASONS), "start_time": hhmm(s), "end_time": hhmm(e),
                           "start_at": utc(s), "end_at": utc(e), "created_at": utc(e)})
    ops.sort(key=lambda r: r["start_at"], reverse=True)
    pauses.sort(key=lambda r: r["start_at"], reverse=True)
    payload = {"ok": True, "operation_count": len(ops), "pause_count": len(pauses), "operations": ops, "pauses": pauses,
               "catalog": {"products": [{"name": "Ромовая баба", "operations": ["Накалывание", "Купание", "Декорирование"]}],
                           "reasons": REASONS, "max_pcs": 100000, "max_people": 50, "max_name": 60,
                           "timezone": "Europe/Moscow"}}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT.name}: operations={len(ops)} pauses={len(pauses)}")


if __name__ == "__main__":
    main()
