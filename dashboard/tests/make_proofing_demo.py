#!/usr/bin/env python3
"""Генератор dashboard/proofing_demo_data.json — ответа /api/dashboard/all бота расстойки.

Данные выдуманные, но по форме совпадают с API бота (HR-analyze/rastoyka_fk, bot.py):
те же поля, время в UTC, отменённые партии, незакрытые этапы, параллельные партии,
разные операторы на разных отметках, остановки и метаданные фото (без байт).

    python3 dashboard/tests/make_proofing_demo.py
"""
import json
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
OUT = Path(__file__).resolve().parent.parent / "proofing_demo_data.json"
FIRST, DAYS = date(2026, 9, 28), 14
NS = uuid.UUID("6f1d3c52-0d0e-4a4e-9a55-5e0f4c0b1a01")

OVENS = [{"code": "hearth", "name": "Подовая", "unit": "pods"},
         {"code": "rotary", "name": "Ротационная", "unit": "racks"},
         {"code": "thermal_oil", "name": "Термомасляная", "unit": "racks"}]
OVEN = {o["code"]: o for o in OVENS}
# изделие → (код, печь, расстойка мин, отпекание мин, шт на шпильку)
PRODUCTS = [
    ("Багет французский", "F08", "hearth", (40, 55), (22, 30), 40),
    ("Багет ремесленный", "F09", "hearth", (45, 60), (25, 34), 36),
    ("Хлеб тартин ржано-пшеничный", "F18", "hearth", (55, 75), (35, 45), 24),
    ("Хлеб пшеничный домашний", "F16", "thermal_oil", (50, 65), (30, 40), 30),
    ("Хлеб злаковый", "F20", "thermal_oil", (50, 70), (32, 42), 28),
    ("Батон классический", "F21", "thermal_oil", (40, 55), (24, 32), 36),
    ("Булочка для гамбургеров без кунжута", "F04", "rotary", (35, 50), (12, 18), 96),
    ("Булочка для хот-дога белая", "F06", "rotary", (35, 45), (12, 16), 110),
    ("Круассан французский", "F34", "rotary", (80, 110), (16, 20), 60),
    ("Слойка с малиной", "F57", "rotary", (45, 60), (16, 22), 54),
    ("Улитка с маком", "F48", "rotary", (45, 60), (15, 20), 60),
    ("Мини-чиабатта", "F39", "hearth", (40, 55), (16, 22), 70),
]
PEOPLE = ["Светлана К.", "Нихад А.", "Ольга М.", "Игорь П.", "Марина Л.", "Тимур Р."]
REASONS = ["Поломка оборудования", "Нет продукта", "Нет персонала", "Техническая проблема", "Качество продукции",
           "Нет шпилек / подов"]
MARKS = {("proofing", "start"): "Начало расстойки", ("proofing", "end"): "Конец расстойки",
         ("baking", "start"): "Начало отпекания", ("baking", "end"): "Конец отпекания"}
EQUIPMENT = ["Расстоечная камера"] + [f"{o['name']} печь" for o in OVENS]


def uid(*parts):
    return str(uuid.uuid5(NS, "/".join(map(str, parts))))


def utc(dt):
    return dt.astimezone(timezone.utc).isoformat()


def hhmm(dt):
    return dt.astimezone(TZ).strftime("%H:%M")


def main():
    rnd = random.Random(20261012)
    ops, batches, pauses, photos = [], [], [], []
    end_of_data = datetime.combine(FIRST + timedelta(days=DAYS - 1), datetime.min.time(), TZ) + timedelta(hours=18, minutes=40)
    n = 0
    for day_no in range(DAYS):
        day = FIRST + timedelta(days=day_no)
        # день плотнее, ночь реже; партии идут параллельно — расстоечная камера одна
        starts = sorted(rnd.sample(range(6 * 60, 23 * 60 + 30, 10), rnd.randint(10, 15))
                        + rnd.sample(range(0, 5 * 60, 15), rnd.randint(1, 3)))
        for minute in starts:
            n += 1
            name, code, oven, proof_rng, bake_rng, per_rack = rnd.choice(PRODUCTS)
            bid = uid("batch", n)
            started = datetime.combine(day, datetime.min.time(), TZ) + timedelta(minutes=minute)
            racks = rnd.randint(6, 14)
            pieces = racks * per_rack - rnd.randint(0, per_rack // 2)
            proof_min = rnd.randint(*proof_rng)
            gap = rnd.randint(2, 12)
            bake_min = rnd.randint(*bake_rng)
            unit = OVEN[oven]["unit"]
            load = max(2, racks // 2) if unit == "pods" else racks
            proof_end = started + timedelta(minutes=proof_min)
            bake_start = proof_end + timedelta(minutes=gap)
            bake_end = bake_start + timedelta(minutes=bake_min)
            out = pieces - rnd.randint(0, max(1, pieces // 40))
            who = rnd.sample(PEOPLE, 3)
            cancelled = rnd.random() < 0.04
            status = "done"
            stage_count = 2
            if cancelled:
                status, stage_count = "cancelled", rnd.choice([1, 2])
            if bake_end > end_of_data:
                status = "active"
                stage_count = 1 if proof_end > end_of_data else 2
            base = {"product_code": code, "product": name}
            proof = {"id": bid, "batch_id": bid, "step_no": 0, "user_id": 1, "user_name": who[0],
                     "operation": "proofing", "operation_name": "Расстойка", "equipment": "Расстоечная камера",
                     "oven": None, **base, "start_time": hhmm(started), "end_time": hhmm(proof_end),
                     "start_at": utc(started), "end_at": utc(proof_end), "rack_unit": "racks",
                     "racks_start": racks, "racks_end": racks, "pieces_start": pieces, "pieces_end": pieces,
                     "status": "closed", "created_at": utc(started), "closed_at": utc(proof_end),
                     "closed_by_id": 2, "closed_by_name": who[1]}
            bake = {"id": uid("bake", n), "batch_id": bid, "step_no": 1, "user_id": 2, "user_name": who[1],
                    "operation": "baking", "operation_name": "Отпекание", "equipment": f"{OVEN[oven]['name']} печь",
                    "oven": oven, **base, "start_time": hhmm(bake_start), "end_time": hhmm(bake_end),
                    "start_at": utc(bake_start), "end_at": utc(bake_end), "rack_unit": unit,
                    "racks_start": load, "racks_end": load, "pieces_start": pieces, "pieces_end": out,
                    "status": "closed", "created_at": utc(bake_start), "closed_at": utc(bake_end),
                    "closed_by_id": 3, "closed_by_name": who[2]}
            finished = None
            if status == "active":
                if stage_count == 1:
                    proof.update(end_time=None, end_at=None, racks_end=None, status="open", closed_at=None,
                                 closed_by_id=None, closed_by_name=None)
                else:
                    bake.update(end_time=None, end_at=None, racks_end=None, pieces_end=None, status="open",
                                closed_at=None, closed_by_id=None, closed_by_name=None)
            elif status == "cancelled":
                finished = (proof_end if stage_count == 1 else bake_start) + timedelta(minutes=5)
                target = proof if stage_count == 1 else bake
                target.update(end_time=None, end_at=None, racks_end=None, pieces_end=None if target is bake else pieces,
                              status="cancelled", closed_at=utc(finished), closed_by_id=1, closed_by_name=who[0])
            else:
                finished = bake_end
            ops.append(proof)
            if stage_count == 2:
                ops.append(bake)
            last = bake if stage_count == 2 else proof
            batches.append({
                "id": bid, **base, "route": ["proofing", "baking"],
                "step": 2 if status == "done" else stage_count - 1,
                "phase": "start" if status == "done" else "end",
                "oven": oven if stage_count == 2 else None, "rack_unit": last["rack_unit"],
                "racks": last["racks_start"], "pieces": out if status == "done" else pieces,
                "pieces_out": out if status == "done" else None, "status": status,
                "started_at": utc(started), "last_at": utc(bake_end if status == "done" else (bake_start if stage_count == 2 else started)),
                "last_mark": "Конец отпекания" if status == "done" else "", "last_by_name": who[2],
                "finished_at": utc(finished) if finished else None, "user_id": 1, "user_name": who[0],
                "finished_by_id": 3 if finished else None, "finished_by_name": (who[2] if status == "done" else who[0]) if finished else None,
                "created_at": utc(started), "updated_at": utc(finished or started)})
            for step, op in enumerate([proof, bake][:stage_count]):
                for phase in ("start", "end"):
                    at = op["start_at"] if phase == "start" else op["end_at"]
                    if not at or rnd.random() > 0.3:
                        continue
                    for k in range(rnd.choice([1, 1, 2])):
                        photos.append({"id": uid("photo", n, step, phase, k), "batch_id": bid, "step_no": step,
                                       "phase": phase, "operation": op["operation"],
                                       "mark": MARKS[(op["operation"], phase)],
                                       "content_type": "image/jpeg", "size": rnd.randint(250_000, 900_000),
                                       "user_id": 1, "user_name": op["closed_by_name"] if phase == "end" else op["user_name"],
                                       "created_at": at})
        for k in range(rnd.choice([0, 1, 1, 2])):
            start = datetime.combine(day, datetime.min.time(), TZ) + timedelta(minutes=rnd.randint(7 * 60, 22 * 60))
            end = start + timedelta(minutes=rnd.randint(8, 70))
            pauses.append({"id": uid("pause", day_no, k), "user_id": 1, "user_name": rnd.choice(PEOPLE),
                           "equipment": rnd.choice(EQUIPMENT), "reason": rnd.choice(REASONS),
                           "start_time": hhmm(start), "end_time": hhmm(end), "start_at": utc(start),
                           "end_at": utc(end), "created_at": utc(end)})
    payload = {"ok": True, "operation_count": len(ops), "pause_count": len(pauses), "batch_count": len(batches),
               "photo_count": len(photos), "operations": ops, "pauses": pauses, "batches": batches, "photos": photos,
               "catalog": {"route": ["proofing", "baking"], "ovens": OVENS,
                           "units": {"racks": {"name": "Шпильки", "forms": ["шпилька", "шпильки", "шпилек"]},
                                     "pods": {"name": "Поды", "forms": ["под", "пода", "подов"]}},
                           "equipment": EQUIPMENT, "reasons": REASONS, "timezone": "Europe/Moscow"}}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT.name}: batches={len(batches)} operations={len(ops)} pauses={len(pauses)} photos={len(photos)}")


if __name__ == "__main__":
    main()
