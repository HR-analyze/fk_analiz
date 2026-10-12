"""Расчёт показателей «Расстойка и отпекание». Чистые функции над снимком proofing_source.

Правила (они же в README):
- каждое событие партии считается по своему времени: «начато» — по началу расстойки,
  «расстойка» — по её концу, «завершено», выпуск, отпекание и шт/ч — по концу отпекания;
  фильтры периода, смены и часа применяются к времени события;
- выпуск — pieces_end закрытого отпекания, ровно один раз на партию; отменённые партии
  в выпуск, длительности и загрузку не попадают (только в «начато» и «отменено»);
- длительность этапа — end_at − start_at (aware-время, часовой пояс и переход через
  полночь учтены); в средние идут только корректно закрытые этапы (0 < t ≤ лимита);
- шт/ч = Σ выход / Σ часов отпекания по партиям с корректным отпеканием и выходом > 0;
- загрузка оборудования — объединение интервалов этапов: две партии в печи
  одновременно занимают её 1 час, а не 2;
- поды и шпильки не складываются никогда: у каждой печи своя единица.
"""
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta

from sources import TZ

DAY_SHIFT = (8 * 60, 20 * 60)  # 08:00–19:59 — день, остальное — ночь (как у ФК и бота)
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
STATUS_NAMES = {"active": "в работе", "done": "отпечена", "cancelled": "отменена"}
UNIT_FORMS = {"racks": ("шпилька", "шпильки", "шпилек"), "pods": ("под", "пода", "подов")}
UNIT_SHORT = {"racks": "шп.", "pods": "под."}
PROOFER_KEY = "proofer"
MAX_DETAIL = 500
LFL_METRICS = ("started", "completed", "output", "avg_proofing", "avg_baking", "rate", "pauses", "stop_minutes")


# ── мелочи ────────────────────────────────────────────────────────────────────

def plural(n, forms):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def units_text(n, unit):
    return f"{fmt_int(n)} {plural(n, UNIT_FORMS.get(unit, UNIT_FORMS['racks']))}"


def fmt_int(n):
    return f"{int(round(n or 0)):,}".replace(",", " ")


def _num_text(value):
    if float(value).is_integer():
        return fmt_int(value)
    return f"{value:,.1f}".replace(",", " ").replace(".", ",")


def fmt_min(minutes):
    if minutes is None:
        return "нет данных"
    m = int(round(minutes))
    return f"{m} мин" if m < 60 else f"{m // 60} ч {m % 60:02d} мин"


def _local(dt):
    return dt.astimezone(TZ) if dt else None


def _hm(dt):
    return _local(dt).strftime("%H:%M") if dt else ""


def _day(dt):
    return _local(dt).date().isoformat() if dt else ""


def _iso(dt):
    return _local(dt).isoformat() if dt else ""


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def _r(value, digits=1):
    return None if value is None else round(value, digits)


def shift_of(dt):
    local = _local(dt)
    minute = local.hour * 60 + local.minute
    return "day" if DAY_SHIFT[0] <= minute < DAY_SHIFT[1] else "night"


def _change(cur, prev):
    if cur is None or not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


# ── фильтры ───────────────────────────────────────────────────────────────────

def parse_filters(date_from="", date_to="", product="all", equipment="all", oven="all", employee="all",
                  shift="all", hour="", status="all"):
    def clean(value):
        value = (value or "").strip()
        return value or "all"

    try:
        hour_num = int(hour) if str(hour or "").strip() not in ("", "all") else None
    except (TypeError, ValueError):
        hour_num = None
    if hour_num is not None and not 0 <= hour_num <= 23:
        hour_num = None
    shift = clean(shift)
    status = clean(status)
    return {
        "date_from": _valid_day(date_from),
        "date_to": _valid_day(date_to),
        "product": clean(product),
        "equipment": clean(equipment),
        "oven": clean(oven),
        "employee": clean(employee),
        "shift": shift if shift in ("day", "night") else "all",
        "hour": hour_num,
        "status": status if status in STATUS_NAMES else "all",
    }


def _valid_day(value):
    value = (value or "").strip()
    try:
        return date.fromisoformat(value).isoformat() if value else ""
    except ValueError:
        return ""


class Window:
    """Период + смена + час. hit(dt) — попадает ли событие в окно (по местному времени)."""

    def __init__(self, date_from="", date_to="", exclude=(), shift="all", hour=None, cut_day="", cutoff=None):
        self.date_from, self.date_to = date_from or "", date_to or ""
        self.exclude = set(exclude or ())
        self.shift, self.hour = shift or "all", hour
        self.cut_day, self.cutoff = cut_day, cutoff

    def day_ok(self, day):
        if not day or day in self.exclude:
            return False
        if self.date_from and day < self.date_from:
            return False
        if self.date_to and day > self.date_to:
            return False
        return True

    def hit(self, dt):
        if dt is None:
            return False
        local = _local(dt)
        day = local.date().isoformat()
        if not self.day_ok(day):
            return False
        if self.shift != "all" and shift_of(local) != self.shift:
            return False
        if self.hour is not None and local.hour != self.hour:
            return False
        if self.cutoff is not None and day == self.cut_day and local.hour * 60 + local.minute > self.cutoff:
            return False
        return True

    def hit_interval(self, start, end):
        """Остановка: день и смена — по началу, час — любой минутой интервала."""
        if start is None:
            return False
        if self.hour is None:
            return self.hit(start)
        plain = Window(self.date_from, self.date_to, self.exclude, self.shift, None, self.cut_day, self.cutoff)
        if not plain.hit(start):
            return False
        t, stop = _local(start), _local(end) if end and end > start else _local(start) + timedelta(minutes=1)
        while t < stop:
            if t.hour == self.hour:
                return True
            t = (t + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return False


def batch_matches(b, f):
    return ((f["product"] == "all" or b["product"] == f["product"])
            and (f["equipment"] == "all" or f["equipment"] in b["equipment"])
            and (f["oven"] == "all" or b["oven"] == f["oven"])
            and (f["employee"] == "all" or f["employee"] in b["employees"])
            and (f["status"] == "all" or b["status"] == f["status"]))


def pause_matches(p, f, oven_equipment):
    # У остановки нет изделия и партии: фильтры по изделию и статусу её не сужают.
    if f["equipment"] != "all" and p["equipment"] != f["equipment"]:
        return False
    if f["oven"] != "all" and p["equipment"] != oven_equipment.get(f["oven"]):
        return False
    return f["employee"] == "all" or p["user_name"] == f["employee"]


def output_at(b):
    """Время выпуска партии: конец закрытого отпекания. У отменённой выпуска нет."""
    baking = b["baking"]
    if b["status"] == "cancelled" or not baking or baking["status"] != "closed":
        return None
    return baking["end_at"]


def proofing_end(b):
    s = b["proofing"]
    if b["status"] == "cancelled" or not s or s["status"] != "closed":
        return None
    return s["end_at"]


# ── KPI ───────────────────────────────────────────────────────────────────────

def in_work_at(batches, at):
    """Партии в работе на момент at: начаты до него и не завершены/не отменены к нему."""
    return [b for b in batches
            if b["started_at"] and b["started_at"] <= at
            and (b["finished_at"] is None or b["finished_at"] > at)]


def kpi_block(batches, pauses, win, at=None):
    started = [b for b in batches if win.hit(b["started_at"])]
    completed = [b for b in batches if win.hit(output_at(b))]
    proofed = [b["proofing"] for b in batches if win.hit(proofing_end(b))]
    proof_valid = [s["duration_min"] for s in proofed if s["valid"]]
    bake_valid = [b for b in completed if b["baking"]["valid"]]
    rated = [b for b in bake_valid if b["pieces_out"]]
    rated_hours = sum(b["baking"]["duration_min"] for b in rated) / 60
    output = sum(b["pieces_out"] or 0 for b in completed)
    working = in_work_at(batches, at) if at else []
    stop = sum(p["duration_min"] for p in pauses)
    return {
        "started": len(started),
        "cancelled": sum(1 for b in started if b["status"] == "cancelled"),
        "completed": len(completed),
        "in_work": len(working),
        "output": output,
        "completed_without_output": sum(1 for b in completed if not b["pieces_out"]),
        "avg_proofing": _mean(proof_valid),
        "avg_baking": _mean([b["baking"]["duration_min"] for b in bake_valid]),
        "rate": round(sum(b["pieces_out"] for b in rated) / rated_hours, 1) if rated_hours > 0 else None,
        "proofing_count": len(proof_valid),
        "baking_count": len(bake_valid),
        "invalid_stages": (sum(1 for s in proofed if not s["valid"])
                           + sum(1 for b in completed if not b["baking"]["valid"])),
        "pauses": len(pauses),
        "stop_minutes": round(stop, 1),
    }


# ── разрезы ───────────────────────────────────────────────────────────────────

def daily_block(batches, pauses, win, lfl_win=None):
    acc = {}

    def slot(day):
        return acc.setdefault(day, {"date": day, "started": 0, "completed": 0, "output": 0, "proofing": [],
                                    "baking": [], "rate_pieces": 0, "rate_min": 0.0, "pauses": 0, "stop": 0.0})

    for b in batches:
        if win.hit(b["started_at"]):
            slot(_day(b["started_at"]))["started"] += 1
        end = proofing_end(b)
        if win.hit(end) and b["proofing"]["valid"]:
            slot(_day(end))["proofing"].append(b["proofing"]["duration_min"])
        out = output_at(b)
        if win.hit(out):
            d = slot(_day(out))
            d["completed"] += 1
            d["output"] += b["pieces_out"] or 0
            if b["baking"]["valid"]:
                d["baking"].append(b["baking"]["duration_min"])
                if b["pieces_out"]:
                    d["rate_pieces"] += b["pieces_out"]
                    d["rate_min"] += b["baking"]["duration_min"]
    for p in pauses:
        d = slot(_day(p["start_at"]))
        d["pauses"] += 1
        d["stop"] += p["duration_min"]

    week_ago = {}
    if lfl_win is not None:
        for b in batches:
            out = output_at(b)
            if lfl_win.hit(out):
                week_ago[_day(out)] = week_ago.get(_day(out), 0) + (b["pieces_out"] or 0)

    rows = []
    for d in sorted(acc.values(), key=lambda x: x["date"]):
        ref = (date.fromisoformat(d["date"]) - timedelta(days=7)).isoformat()
        row = {
            "date": d["date"],
            "weekday": WEEKDAYS[date.fromisoformat(d["date"]).weekday()],
            "started": d["started"], "completed": d["completed"], "output": d["output"],
            "avg_proofing": _mean(d["proofing"]), "avg_baking": _mean(d["baking"]),
            "rate": round(d["rate_pieces"] / (d["rate_min"] / 60), 1) if d["rate_min"] > 0 else None,
            "pauses": d["pauses"], "stop": round(d["stop"], 1),
        }
        if lfl_win is not None:
            row["lfl_date"] = ref
            row["lfl_output"] = week_ago.get(ref, 0)
            row["lfl_change"] = _change(row["output"], row["lfl_output"])
        rows.append(row)
    return rows


def hourly_block(batches, win):
    """Выпуск по часу КОНЦА отпекания: шт и число партий за весь период."""
    pieces, count, days = [0] * 24, [0] * 24, [set() for _ in range(24)]
    for b in batches:
        out = output_at(b)
        if win.hit(out):
            h = _local(out).hour
            pieces[h] += b["pieces_out"] or 0
            count[h] += 1
            days[h].add(_day(out))
    return [{"label": f"{h:02d}:00", "hour": h, "value": pieces[h], "batches": count[h],
             "per_day": round(pieces[h] / len(days[h]), 1) if days[h] else 0} for h in range(24)]


def oven_block(batches, pauses, win, ovens, load_rows):
    busy = {r["key"]: r for r in load_rows}
    out = []
    for o in ovens:
        done = [b for b in batches if b["oven"] == o["code"] and win.hit(output_at(b))]
        valid = [b for b in done if b["baking"]["valid"]]
        rated = [b for b in valid if b["pieces_out"]]
        hours = sum(b["baking"]["duration_min"] for b in rated) / 60
        loaded = sum((b["baking"]["racks_start"] or 0) for b in done)
        name = f"{o['name']} печь"
        stops = [p for p in pauses if p["equipment"] == name]
        row = {
            "code": o["code"], "name": o["name"], "equipment": name, "unit": o["unit"],
            "unit_name": "поды" if o["unit"] == "pods" else "шпильки",
            "batches": len(done),
            "output": sum(b["pieces_out"] or 0 for b in done),
            "loaded": loaded,
            "loaded_text": units_text(loaded, o["unit"]) if done else "—",
            "avg_baking": _mean([b["baking"]["duration_min"] for b in valid]),
            "rate": round(sum(b["pieces_out"] for b in rated) / hours, 1) if hours > 0 else None,
            "pauses": len(stops),
            "stop": round(sum(p["duration_min"] for p in stops), 1),
            "busy_hours": busy.get(o["code"], {}).get("busy_hours", 0),
            "utilization": busy.get(o["code"], {}).get("utilization"),
            "max_parallel": busy.get(o["code"], {}).get("max_parallel", 0),
        }
        out.append(row)
    return out


def product_block(batches, win):
    acc = {}
    for b in batches:
        p = acc.setdefault(b["product"], {"product": b["product"], "started": 0, "completed": 0, "output": 0,
                                          "proofing": [], "baking": [], "cycle": [], "rp": 0, "rm": 0.0})
        if win.hit(b["started_at"]):
            p["started"] += 1
        if win.hit(proofing_end(b)) and b["proofing"]["valid"]:
            p["proofing"].append(b["proofing"]["duration_min"])
        if win.hit(output_at(b)):
            p["completed"] += 1
            p["output"] += b["pieces_out"] or 0
            if b["baking"]["valid"]:
                p["baking"].append(b["baking"]["duration_min"])
                if b["pieces_out"]:
                    p["rp"] += b["pieces_out"]
                    p["rm"] += b["baking"]["duration_min"]
                if b["proofing"] and b["proofing"]["valid"]:
                    p["cycle"].append((b["baking"]["end_at"] - b["proofing"]["start_at"]).total_seconds() / 60)
    rows = []
    for p in acc.values():
        if not (p["started"] or p["completed"] or p["proofing"]):
            continue
        rows.append({
            "product": p["product"], "started": p["started"], "completed": p["completed"], "output": p["output"],
            "avg_proofing": _mean(p["proofing"]), "avg_baking": _mean(p["baking"]), "avg_cycle": _mean(p["cycle"]),
            "rate": round(p["rp"] / (p["rm"] / 60), 1) if p["rm"] > 0 else None,
        })
    rows.sort(key=lambda r: (-r["output"], -r["completed"], -r["started"], r["product"]))
    return rows


MARK_ROLES = {"proofing_start": "start_proofing", "proofing_end": "end_proofing",
              "baking_start": "start_baking", "baking_end": "end_baking"}


def employee_block(batches, pauses, win):
    acc = {}

    def slot(name):
        return acc.setdefault(name, {"employee": name, "batches": set(), "marks": 0, "start_proofing": 0,
                                     "end_proofing": 0, "start_baking": 0, "end_baking": 0,
                                     "output_closed": 0, "pauses": 0, "stop": 0.0})

    for b in batches:
        cancelled = b["status"] == "cancelled"
        for m in b["marks"]:
            if not m["user"] or m["user"] == "—" or not win.hit(m["at"]):
                continue
            e = slot(m["user"])
            e["batches"].add(b["id"])
            e["marks"] += 1
            e[MARK_ROLES[m["kind"]]] += 1
            if m["kind"] == "baking_end" and not cancelled:
                e["output_closed"] += b["pieces_out"] or 0
    for p in pauses:
        e = slot(p["user_name"])
        e["pauses"] += 1
        e["stop"] += p["duration_min"]
    rows = [{**e, "batches": len(e["batches"]), "stop": round(e["stop"], 1)} for e in acc.values()]
    rows.sort(key=lambda r: (-r["batches"], -r["marks"], r["employee"]))
    return rows


def _union(intervals):
    merged = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _max_parallel(intervals):
    events = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals], key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, step in events:
        cur += step
        best = max(best, cur)
    return best


def _spread(intervals, exclude):
    """Минуты интервалов → {(день, час): минуты} по местному времени."""
    out = defaultdict(float)
    for s, e in intervals:
        t = _local(s)
        end = _local(e)
        while t < end:
            nxt = min(end, (t + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0))
            day = t.date().isoformat()
            if day not in exclude:
                out[(day, t.hour)] += (nxt - t).total_seconds() / 60
            t = nxt
    return out


def window_bounds(f, batches, pauses, now):
    """[начало, конец) периода в местном времени; без дат — по данным и до «сейчас»."""
    if f["date_from"]:
        start = datetime.combine(date.fromisoformat(f["date_from"]), dtime.min, TZ)
    else:
        stamps = [b["started_at"] for b in batches if b["started_at"]] + [p["start_at"] for p in pauses if p["start_at"]]
        start = datetime.combine(_local(min(stamps)).date(), dtime.min, TZ) if stamps else now
    end = datetime.combine(date.fromisoformat(f["date_to"]) + timedelta(days=1), dtime.min, TZ) if f["date_to"] else now
    return start, min(end, now) if now else end


def load_block(batches, ovens, start, end, exclude, max_stage_min):
    """Загрузка расстоечной камеры и печей по часам суток.

    busy — объединение интервалов (физическое время занятости), parallel — сумма
    интервалов партий (сколько партий в среднем шло одновременно). Открытые этапы
    идут до «сейчас» (end), но не дольше лимита этапа.
    """
    keys = [(PROOFER_KEY, "Расстоечная камера")] + [(o["code"], f"{o['name']} печь") for o in ovens]
    raw = {k: [] for k, _ in keys}
    for b in batches:
        if b["status"] == "cancelled":
            continue
        for code in ("proofing", "baking"):
            s = b[code]
            if not s or not s["start_at"]:
                continue
            key = PROOFER_KEY if code == "proofing" else s["oven"]
            if key not in raw:
                continue
            if s["status"] == "closed":
                if not s["valid"]:
                    continue
                stop = s["end_at"]
            elif s["status"] == "open" and b["status"] == "active":
                stop = min(end, s["start_at"] + timedelta(minutes=max_stage_min))
            else:
                continue
            a, z = max(s["start_at"], start), min(stop, end)
            if z > a:
                raw[key].append((a, z))

    days = []
    d = _local(start).date()
    while start < end and datetime.combine(d, dtime.min, TZ) < end:
        if d.isoformat() not in exclude:
            days.append(d.isoformat())
        d += timedelta(days=1)
    span_min = max(0.0, (end - start).total_seconds() / 60) - 1440 * sum(
        1 for x in exclude if start <= datetime.combine(date.fromisoformat(x), dtime.min, TZ) < end)
    n_days = len(days) or 1

    rows = []
    for key, name in keys:
        merged = _union(raw[key])
        busy = _spread(merged, exclude)
        total = _spread(raw[key], exclude)
        b_hour, t_hour = [0.0] * 24, [0.0] * 24
        for (_, h), v in busy.items():
            b_hour[h] += v
        for (_, h), v in total.items():
            t_hour[h] += v
        hours = [{"hour": h, "busy_min": round(b_hour[h] / n_days, 1),
                  "pct": round(b_hour[h] / n_days / 60 * 100, 1),
                  "parallel": round(t_hour[h] / n_days / 60, 2)} for h in range(24)]
        busy_min = sum(busy.values())
        rows.append({
            "key": key, "name": name, "stages": len(raw[key]),
            "busy_hours": round(busy_min / 60, 1),
            "sum_hours": round(sum(total.values()) / 60, 1),
            "utilization": round(busy_min / span_min * 100, 1) if span_min > 0 else None,
            "max_parallel": _max_parallel(raw[key]),
            "hours": hours,
        })
    return {"rows": rows, "days": len(days), "from": _iso(start), "to": _iso(end)}


def pause_blocks(pauses):
    reasons, equipment = {}, {}
    for p in pauses:
        r = reasons.setdefault(p["reason"], {"label": p["reason"], "value": 0.0, "count": 0})
        r["value"] += p["duration_min"]
        r["count"] += 1
        e = equipment.setdefault(p["equipment"], {"equipment": p["equipment"], "count": 0, "minutes": 0.0, "reasons": set()})
        e["count"] += 1
        e["minutes"] += p["duration_min"]
        e["reasons"].add(p["reason"])
    by_reason = sorted(({**r, "value": round(r["value"], 1)} for r in reasons.values()), key=lambda x: -x["value"])
    by_equipment = sorted(({**e, "minutes": round(e["minutes"], 1), "reasons": ", ".join(sorted(e["reasons"]))}
                           for e in equipment.values()), key=lambda x: -x["minutes"])
    rows = [{"date": _day(p["start_at"]), "equipment": p["equipment"], "reason": p["reason"],
             "start_time": p["start_time"], "end_time": p["end_time"], "minutes": round(p["duration_min"], 1),
             "user_name": p["user_name"]} for p in pauses[:200]]
    return by_reason, by_equipment, rows


# ── партии ────────────────────────────────────────────────────────────────────

def stage_view(s):
    if not s:
        return None
    return {
        "status": s["status"], "equipment": s["equipment"], "oven": s["oven"],
        "start": _iso(s["start_at"]), "end": _iso(s["end_at"]),
        "start_date": _day(s["start_at"]), "end_date": _day(s["end_at"]),
        "start_time": _hm(s["start_at"]), "end_time": _hm(s["end_at"]),
        "duration_min": _r(s["duration_min"]), "valid": s["valid"],
        "rack_unit": s["rack_unit"], "racks_start": s["racks_start"], "racks_end": s["racks_end"],
        "pieces_start": s["pieces_start"], "pieces_end": s["pieces_end"],
        "started_by": s["started_by"], "closed_by": s["closed_by"],
    }


def batch_view(b, now):
    proofing, baking = b["proofing"], b["baking"]
    rate = None
    if b["pieces_out"] and baking and baking["valid"]:
        rate = round(b["pieces_out"] / (baking["duration_min"] / 60), 1)
    cycle = None
    if b["status"] == "done" and proofing and baking and baking["end_at"] and proofing["start_at"]:
        cycle = round((baking["end_at"] - proofing["start_at"]).total_seconds() / 60, 1)
    open_stage = next((s for s in (proofing, baking) if s and s["status"] == "open"), None)
    return {
        "id": b["id"], "product": b["product"], "status": b["status"], "status_name": STATUS_NAMES.get(b["status"], b["status"]),
        "date": _day(b["started_at"]), "start_time": _hm(b["started_at"]), "started_at": _iso(b["started_at"]),
        "finished_at": _iso(b["finished_at"]), "oven": b["oven"], "oven_name": b["oven_name"], "oven_unit": b["oven_unit"],
        "proofing": stage_view(proofing), "baking": stage_view(baking),
        "pieces_in": b["pieces_in"], "pieces_out": b["pieces_out"], "rate": rate, "cycle_min": cycle,
        "employees": b["employees"], "started_by": b["user_name"], "next_mark": b["next_mark"],
        "open_for_min": round((now - open_stage["start_at"]).total_seconds() / 60) if open_stage and b["status"] == "active" else None,
        "photos": [{"id": p["id"], "mark": p["mark"], "user_name": p["user_name"], "created_at": p["created_at"]}
                   for p in b["photos"]],
        "photo_count": len(b["photos"]),
    }


def batch_scope(batches, win, f, at):
    """Партии периода: хотя бы одна отметка в окне; без смены и часа — плюс те, что в работе."""
    plain = f["shift"] == "all" and f["hour"] is None
    working = {b["id"] for b in in_work_at(batches, at)} if plain and at else set()
    return [b for b in batches if b["id"] in working or any(win.hit(m["at"]) for m in b["marks"])]


def open_stages(batches, now, max_stage_min):
    rows = []
    for b in batches:
        if b["status"] != "active":
            continue
        for code in ("proofing", "baking"):
            s = b[code]
            if s and s["status"] == "open":
                minutes = (now - s["start_at"]).total_seconds() / 60
                rows.append({"batch_id": b["id"], "product": b["product"], "stage": s["name"],
                             "equipment": s["equipment"], "start_date": _day(s["start_at"]),
                             "start_time": _hm(s["start_at"]), "open_min": round(minutes),
                             "stale": minutes > max_stage_min, "started_by": s["started_by"],
                             "racks": s["racks_start"], "rack_unit": s["rack_unit"],
                             "next_mark": b["next_mark"]})
    rows.sort(key=lambda r: -r["open_min"])
    return rows


# ── сборка ────────────────────────────────────────────────────────────────────

def _prepare(snapshot, f, exclude_dates):
    oven_equipment = {o["code"]: f"{o['name']} печь" for o in snapshot["ovens"]}
    batches = [b for b in snapshot["batches"] if batch_matches(b, f)]
    pauses = [p for p in snapshot["pauses"] if pause_matches(p, f, oven_equipment)]
    return batches, pauses


def build_summary(snapshot, filters=None, exclude_dates=(), now=None, max_stage_hours=18):
    """Одна функция на страницу, Excel и PDF — цифры везде одни и те же."""
    now = now or datetime.now(TZ)
    f = parse_filters(**(filters or {}))
    max_stage_min = max_stage_hours * 60
    win = Window(f["date_from"], f["date_to"], exclude_dates, f["shift"], f["hour"])
    batches, all_pauses = _prepare(snapshot, f, exclude_dates)
    pauses = [p for p in all_pauses if win.hit_interval(p["start_at"], p["end_at"])]
    start, end = window_bounds(f, batches, all_pauses, now)
    at = end
    kpi = kpi_block(batches, pauses, win, at)
    load = load_block(batches, snapshot["ovens"], start, end, set(exclude_dates or ()), max_stage_min)
    lfl_win = None
    if f["date_from"] and f["date_to"]:
        lfl_win = Window(_shift_day(f["date_from"], -7), _shift_day(f["date_to"], -7), exclude_dates, f["shift"], f["hour"])
        if f["date_to"] >= now.date().isoformat():
            lfl_win.cut_day, lfl_win.cutoff = (now.date() - timedelta(days=7)).isoformat(), now.hour * 60 + now.minute
    by_reason, by_equipment, pause_rows = pause_blocks(pauses)
    scope = batch_scope(batches, win, f, at)
    products = product_block(batches, win)
    employees = employee_block(batches, pauses, win)
    hourly = hourly_block(batches, win)
    loaded_proofing = sum((b["proofing"]["racks_start"] or 0) for b in batches
                          if b["proofing"] and b["status"] != "cancelled" and win.hit(b["started_at"]))
    summary = {
        "kind": "proofing",
        "filters_applied": f,
        "kpi": {**kpi, "loaded_proofing": loaded_proofing},
        "daily": daily_block(batches, pauses, win, lfl_win),
        "hourly": hourly,
        "ovens": oven_block(batches, pauses, win, snapshot["ovens"], load["rows"]),
        "products": products,
        "top_products": [{"label": p["product"], "value": p["output"]} for p in products if p["output"] > 0][:12],
        "employees": employees,
        "top_employees": [{"label": e["employee"], "value": e["batches"]} for e in employees if e["batches"]][:12],
        "load": load,
        "pause_reasons": by_reason,
        "pause_equipment": by_equipment,
        "pause_rows": pause_rows,
        "open_stages": open_stages(batches, now, max_stage_min),
        "batches_total": len(scope),
        "batches": [batch_view(b, now) for b in scope[:MAX_DETAIL]],
        "issues": snapshot.get("issues", {}),
    }
    summary["lfl"] = build_lfl(snapshot, f, kpi, exclude_dates, now)
    return summary


def _shift_day(day, delta):
    return (date.fromisoformat(day) + timedelta(days=delta)).isoformat()


def _span(start, end):
    if start == end:
        return start.strftime("%d.%m")
    return f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')}"


def build_lfl(snapshot, f, cur_kpi, exclude_dates=(), now=None):
    """Те же фильтры, тот же отрезок прямо перед текущим. Если период заканчивается
    сегодня — у последнего дня прошлого периода берём события до того же времени суток."""
    try:
        d_from, d_to = date.fromisoformat(f["date_from"]), date.fromisoformat(f["date_to"])
    except (TypeError, ValueError):
        return {"available": False, "reason": "для сравнения нужен период с датами от и до"}
    today = now.date() if now else None
    if today and d_to > today:
        d_to = today
    if d_to < d_from:
        return {"available": False, "reason": "период ещё не начался"}
    partial = today is not None and d_to == today
    cutoff = now.hour * 60 + now.minute if partial else None
    days = (d_to - d_from).days + 1
    prev_to = d_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)
    win = Window(prev_from.isoformat(), prev_to.isoformat(), exclude_dates, f["shift"], f["hour"],
                 prev_to.isoformat(), cutoff)
    batches, all_pauses = _prepare(snapshot, f, exclude_dates)
    pauses = [p for p in all_pauses if win.hit_interval(p["start_at"], p["end_at"])]
    prev = kpi_block(batches, pauses, win)
    empty = not (prev["started"] or prev["completed"] or prev["pauses"])
    kpi = {k: {"prev": prev[k], "change": None if empty else _change(cur_kpi[k], prev[k])} for k in LFL_METRICS}
    return {"available": True, "days": days, "prev_from": prev_from.isoformat(), "prev_to": prev_to.isoformat(),
            "prev_label": _span(prev_from, prev_to), "prev_empty": empty, "partial": partial,
            "cutoff": f"{cutoff // 60:02d}:{cutoff % 60:02d}" if cutoff is not None else "", "kpi": kpi}


def change_text(change):
    if change is None:
        return "—"
    value = f"{abs(change):.1f}".replace(".", ",")
    return f"▲ +{value}%" if change > 0 else (f"▼ −{value}%" if change < 0 else "= 0%")


def kpi_rows(k):
    """(подпись, значение для людей, ключ LFL) — одна таблица для страницы, Excel и PDF."""
    return [
        ("Начато партий", fmt_int(k["started"]) + (f" (отменено {k['cancelled']})" if k["cancelled"] else ""), "started"),
        ("Завершено партий", fmt_int(k["completed"]), "completed"),
        ("В работе на конец периода", fmt_int(k["in_work"]), None),
        ("Выпуск, шт", fmt_int(k["output"]) if k["completed"] else "нет данных", "output"),
        ("Средняя расстойка", fmt_min(k["avg_proofing"]), "avg_proofing"),
        ("Среднее отпекание", fmt_min(k["avg_baking"]), "avg_baking"),
        ("Производительность отпекания, шт/ч", "нет данных" if k["rate"] is None else fmt_int(k["rate"]), "rate"),
        ("Критические остановки", f"{k['pauses']} · {fmt_min(k['stop_minutes']) if k['pauses'] else '0 мин'}", "pauses"),
    ]


def build_analysis(summary, filters=None):
    """Плоские факты для сводки и PDF: числа, без выводов и рекомендаций."""
    k = summary["kpi"]
    blocks = [("Итоги периода", [
        f"Партий начато: {k['started']}" + (f", из них отменено: {k['cancelled']}." if k["cancelled"] else "."),
        f"Партий завершено (отпекание закрыто): {k['completed']}; в работе на конец периода: {k['in_work']}.",
        (f"Фактический выпуск: {fmt_int(k['output'])} шт." if k["completed"] else "Фактический выпуск: нет данных."),
        f"Средняя расстойка: {fmt_min(k['avg_proofing'])} (этапов: {k['proofing_count']}); "
        f"среднее отпекание: {fmt_min(k['avg_baking'])} (этапов: {k['baking_count']}).",
        "Производительность отпекания: " + ("нет данных." if k["rate"] is None else f"{fmt_int(k['rate'])} шт/ч."),
        f"Критических остановок: {k['pauses']}, суммарно {fmt_min(k['stop_minutes']) if k['pauses'] else '0 мин'}.",
    ])]
    if k.get("completed_without_output"):
        blocks[0][1].append(f"Завершено без выхода в штуках: {k['completed_without_output']}.")
    if k.get("invalid_stages"):
        blocks[0][1].append(f"Этапов с некорректным временем (не вошли в средние): {k['invalid_stages']}.")

    lfl = summary.get("lfl") or {}
    if lfl.get("available"):
        head = f"Прошлый период: {lfl['prev_label']} ({lfl['days']} дн.)"
        if lfl.get("partial"):
            head += f", последний день взят до {lfl['cutoff']} — как и сегодняшний"
        rows = [head + "."]
        if lfl["prev_empty"]:
            rows.append("За прошлый период с этими фильтрами данных нет — сравнивать не с чем.")
        else:
            for title, key, unit in (("Начато", "started", ""), ("Завершено", "completed", ""),
                                     ("Выпуск", "output", " шт"), ("Средняя расстойка", "avg_proofing", " мин"),
                                     ("Среднее отпекание", "avg_baking", " мин"), ("Производительность", "rate", " шт/ч"),
                                     ("Остановок", "pauses", ""), ("Остановки", "stop_minutes", " мин")):
                c = lfl["kpi"][key]
                cur = k[key]
                cur_t = "нет данных" if cur is None else _num_text(cur) + unit
                prev_t = "нет данных" if c["prev"] is None else _num_text(c["prev"]) + unit
                rows.append(f"{title}: {cur_t} против {prev_t}, {change_text(c['change'])}.")
        blocks.append(("Сравнение с прошлым периодом", rows))

    ovens = [o for o in summary["ovens"] if o["batches"] or o["pauses"]]
    if ovens:
        blocks.append(("По печам", [
            f"{o['name']}: партий {o['batches']}, выпуск {fmt_int(o['output'])} шт, загружено {o['loaded_text']}, "
            f"отпекание в среднем {fmt_min(o['avg_baking'])}, "
            + ("шт/ч нет данных" if o["rate"] is None else f"{fmt_int(o['rate'])} шт/ч")
            + (f", остановок {o['pauses']} ({fmt_min(o['stop'])})" if o["pauses"] else "") + "."
            for o in ovens]))
    loaded = [f"Расстойка: {units_text(k['loaded_proofing'], 'racks')}."] if k.get("loaded_proofing") else []
    loaded += [f"{o['equipment']}: {o['loaded_text']}." for o in ovens if o["loaded"]]
    if loaded:
        blocks.append(("Загружено (поды и шпильки не складываются)", loaded))

    load = summary.get("load") or {}
    rows = []
    for r in load.get("rows", []):
        if r["stages"]:
            rows.append(f"{r['name']}: занято {_num_text(r['busy_hours'])} ч (пересечения партий не суммируются), "
                        f"сумма по партиям {_num_text(r['sum_hours'])} ч, одновременно до {r['max_parallel']} парт."
                        + (f", занято {_num_text(r['utilization'])}% времени периода" if r["utilization"] is not None else "") + ".")
    if rows:
        blocks.append(("Загрузка оборудования", rows))

    top = summary["top_products"][:5]
    if top:
        total = k["output"] or 1
        blocks.append(("Топ изделий по выпуску", [f"{i + 1}. {x['label']} — {fmt_int(x['value'])} шт ({_num_text(round(x['value'] / total * 100, 1))}%)"
                                                  for i, x in enumerate(top)]))
    emp = summary["employees"][:5]
    if emp:
        blocks.append(("Партии по сотрудникам", [
            f"{i + 1}. {e['employee']} — партий {e['batches']}, отметок {e['marks']}"
            f" (начал расстойку {e['start_proofing']}, закрыл отпекание {e['end_baking']})" for i, e in enumerate(emp)]))
    reasons = summary["pause_reasons"]
    if reasons:
        total = sum(r["value"] for r in reasons) or 1
        blocks.append(("Остановки по причинам", [f"{i + 1}. {r['label']} — {r['count']} {plural(r['count'], ('раз', 'раза', 'раз'))}, "
                                                 f"{fmt_min(r['value'])} ({_num_text(round(r['value'] / total * 100, 1))}%)" for i, r in enumerate(reasons[:6])]))
    opened = summary.get("open_stages") or []
    if opened:
        stale = sum(1 for r in opened if r["stale"])
        blocks.append(("Незакрытые этапы", [f"Сейчас открыто этапов: {len(opened)}"
                                            + (f", из них дольше лимита: {stale}." if stale else ".")]))
    issues = {key: v for key, v in (summary.get("issues") or {}).items() if v}
    if issues:
        names = {"duplicate_ops": "дубли шагов (учтены один раз)", "pieces_mismatch": "выход партии ≠ выходу отпекания (взят выход отпекания)",
                 "done_without_baking": "партия отпечена без закрытого отпекания (выход не учтён)",
                 "invalid_duration": "этапы с некорректным временем", "orphan_ops": "шаги без партии в выборке"}
        blocks.append(("Проверка данных (вся выборка)", [f"{names.get(key, key)}: {v}" for key, v in issues.items()]))
    if filters:
        applied = [f"{name}: {value}" for name, value in filters if value]
        if applied:
            blocks.append(("Применённые фильтры", applied))
    return blocks


def build_meta(snapshot, exclude_dates=()):
    excluded = set(exclude_dates or ())
    batches = snapshot["batches"]
    pauses = snapshot["pauses"]
    days = {_day(b["started_at"]) for b in batches if b["started_at"]}
    days |= {_day(output_at(b)) for b in batches if output_at(b)}
    days |= {_day(p["start_at"]) for p in pauses if p["start_at"]}
    days = sorted(d for d in days if d and d not in excluded)
    employees = sorted({e for b in batches for e in b["employees"]} | {p["user_name"] for p in pauses if p["user_name"] != "—"})
    return {
        "products": sorted({b["product"] for b in batches}),
        "equipment": snapshot["equipment"],
        "ovens": snapshot["ovens"],
        "employees": employees,
        "statuses": [{"code": k, "name": v} for k, v in STATUS_NAMES.items()],
        "date_min": days[0] if days else "",
        "date_max": days[-1] if days else "",
        "production_count": len(batches),
        "pause_count": len(pauses),
        "photo_count": len(snapshot.get("photos", {})),
        "excluded_dates": sorted(excluded),
    }
