"""Расчёт показателей дашборда. Чистые функции над снимком данных."""
import re
from datetime import date, timedelta

HHMM = re.compile(r"^(\d{1,2})[:.\-](\d{2})")
DAY_SHIFT = (8 * 60, 20 * 60)  # 08:00–19:59 — день, остальное — ночь
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def parse_hhmm(value):
    if not value:
        return None
    m = HHMM.match(str(value).strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def duration_min(start, end):
    a, b = parse_hhmm(start), parse_hhmm(end)
    if a is None or b is None:
        return 0
    delta = b - a
    if delta < 0:
        delta += 1440  # переход через полночь
    return delta


def shift_of(start):
    a = parse_hhmm(start)
    if a is None:
        return "unknown"
    return "day" if DAY_SHIFT[0] <= a < DAY_SHIFT[1] else "night"


def _avg(values):
    values = [v for v in values if v]
    return sum(values) / len(values) if values else 0.0


def covers_hour(start, end, hour):
    """Операция считается идущей в этот час, если её интервал накрывает хотя бы одну его минуту."""
    a = parse_hhmm(start)
    dur = duration_min(start, end)
    if a is None:
        return False
    if dur <= 0:
        return a // 60 == hour
    return any(((a + offset) // 60) % 24 == hour for offset in range(dur))


def apply_filters(snapshot, date_from="", date_to="", equipment="", product="",
                  shift="", employee="", hour="", exclude_dates=()):
    equipment = equipment or "all"
    product = product or "all"
    shift = shift or "all"
    employee = employee or "all"
    excluded = set(exclude_dates or ())
    try:
        hour_num = int(hour) if hour not in ("", None, "all") else None
    except (TypeError, ValueError):
        hour_num = None
    if hour_num is not None and not 0 <= hour_num <= 23:
        hour_num = None

    def in_range(row):
        d = row.get("date", "")
        if not d or d in excluded:
            return False
        if date_from and d < date_from:
            return False
        if date_to and d > date_to:
            return False
        return True

    production = [
        r for r in snapshot["production"]
        if in_range(r)
        and (equipment == "all" or r["equipment"] == equipment)
        and (product == "all" or r["product"] == product)
        and (shift == "all" or shift_of(r["start_time"]) == shift)
        and (employee == "all" or r["user_name"] == employee)
        and (hour_num is None or covers_hour(r["start_time"], r["end_time"], hour_num))
    ]
    pauses = [
        r for r in snapshot["pauses"]
        if in_range(r)
        and (equipment == "all" or r["equipment"] == equipment)
        and (shift == "all" or shift_of(r["start_time"]) == shift)
        and (employee == "all" or r["user_name"] == employee)
        and (hour_num is None or covers_hour(r["start_time"], r["end_time"], hour_num))
    ]
    return production, pauses


def _sum_by(rows, key, value_fn, limit=None):
    acc = {}
    for r in rows:
        acc[r[key]] = acc.get(r[key], 0) + value_fn(r)
    items = [{"label": k, "value": round(v, 1)} for k, v in acc.items() if v > 0]
    items.sort(key=lambda x: -x["value"])
    return items[:limit] if limit else items


def daily_output(production, pauses, plans=None, plan_filters=None):
    acc = {}
    for r in production:
        d = acc.setdefault(r["date"], {"date": r["date"], "ops": 0, "qty": 0, "durations": [], "stop": 0})
        d["ops"] += 1
        d["qty"] += r["qty"]
        dur = duration_min(r["start_time"], r["end_time"])
        if dur:
            d["durations"].append(dur)
    for r in pauses:
        d = acc.setdefault(r["date"], {"date": r["date"], "ops": 0, "qty": 0, "durations": [], "stop": 0})
        d["stop"] += duration_min(r["start_time"], r["end_time"])
    filters = dict(plan_filters or {})
    filters.pop("date_from", None)
    filters.pop("date_to", None)
    out = []
    for d in sorted(acc.values(), key=lambda x: x["date"]):
        plan = 0
        if plans:
            per_day, _ = plan_for_period(plans, d["date"], d["date"], **filters)
            plan = sum(v["plan"] for v in per_day.values())
        out.append({
            "date": d["date"],
            "weekday": WEEKDAYS[date.fromisoformat(d["date"]).weekday()] if d["date"] else "",
            "ops": d["ops"],
            "qty": d["qty"],
            "avg_duration": round(_avg(d["durations"])),
            "stop": d["stop"],
            "plan": plan,
            "done": round(d["qty"] / plan * 100, 1) if plan else None,
        })
    return out


def hourly_productivity(production):
    """Штуки распределяются равномерно по минутам операции и складываются в часовые корзины."""
    buckets = [0.0] * 24
    days = [set() for _ in range(24)]
    for r in production:
        start = parse_hhmm(r["start_time"])
        dur = duration_min(r["start_time"], r["end_time"])
        qty = r["qty"]
        if start is None or dur <= 0 or qty <= 0:
            continue
        per_min = qty / dur
        try:
            base = date.fromisoformat(r["date"]) if r["date"] else None
        except ValueError:
            base = None
        for offset in range(dur):
            absolute = start + offset
            hour = (absolute // 60) % 24
            buckets[hour] += per_min
            if base:
                days[hour].add((base + timedelta(days=absolute // 1440)).isoformat())
    return [
        {"label": f"{h:02d}:00", "value": round(buckets[h] / len(days[h]), 1) if days[h] else 0.0}
        for h in range(24)
    ]


def product_timings(production, limit=25):
    acc = {}
    for r in production:
        p = acc.setdefault(r["product"], {"product": r["product"], "ops": 0, "qty": 0, "durations": [], "rates": [], "starts": []})
        p["ops"] += 1
        p["qty"] += r["qty"]
        dur = duration_min(r["start_time"], r["end_time"])
        if dur:
            p["durations"].append(dur)
            if r["qty"] > 0:
                p["rates"].append(r["qty"] / (dur / 60))
        start = parse_hhmm(r["start_time"])
        if start is not None:
            p["starts"].append(start)
    rows = []
    for p in acc.values():
        starts = sorted(p["starts"])
        rows.append({
            "product": p["product"],
            "ops": p["ops"],
            "qty": p["qty"],
            "avg_duration": round(_avg(p["durations"]), 1),
            "avg_rate": round(_avg(p["rates"]), 1),
            "start_range": f"{starts[0] // 60:02d}:{starts[0] % 60:02d} – {starts[-1] // 60:02d}:{starts[-1] % 60:02d}" if starts else "—",
        })
    rows.sort(key=lambda x: -x["qty"])
    return rows[:limit]


def equipment_timings(production, pauses):
    acc = {}
    for r in production:
        e = acc.setdefault(r["equipment"], {"equipment": r["equipment"], "ops": 0, "qty": 0, "work": 0, "stop": 0, "rates": []})
        e["ops"] += 1
        e["qty"] += r["qty"]
        dur = duration_min(r["start_time"], r["end_time"])
        e["work"] += dur
        if dur and r["qty"] > 0:
            e["rates"].append(r["qty"] / (dur / 60))
    for r in pauses:
        e = acc.setdefault(r["equipment"], {"equipment": r["equipment"], "ops": 0, "qty": 0, "work": 0, "stop": 0, "rates": []})
        e["stop"] += duration_min(r["start_time"], r["end_time"])
    rows = []
    for e in acc.values():
        total = e["work"] + e["stop"]
        rows.append({
            "equipment": e["equipment"],
            "ops": e["ops"],
            "qty": e["qty"],
            "work": e["work"],
            "stop": e["stop"],
            "avg_rate": round(_avg(e["rates"]), 1),
            "utilization": round(e["work"] / total * 100, 1) if total else 0.0,
        })
    rows.sort(key=lambda x: -x["qty"])
    return rows


def employee_timings(production, pauses):
    acc = {}

    def slot(name):
        return acc.setdefault(name, {"employee": name, "ops": 0, "qty": 0, "work": 0,
                                     "stop": 0, "pauses": 0, "rates": [], "durations": [],
                                     "products": set(), "equipment": set(), "days": set()})

    for r in production:
        e = slot(r["user_name"])
        e["ops"] += 1
        e["qty"] += r["qty"]
        dur = duration_min(r["start_time"], r["end_time"])
        e["work"] += dur
        if dur:
            e["durations"].append(dur)
            if r["qty"] > 0:
                e["rates"].append(r["qty"] / (dur / 60))
        e["products"].add(r["product"])
        e["equipment"].add(r["equipment"])
        if r["date"]:
            e["days"].add(r["date"])
    for r in pauses:
        e = slot(r["user_name"])
        e["pauses"] += 1
        e["stop"] += duration_min(r["start_time"], r["end_time"])
        if r["date"]:
            e["days"].add(r["date"])

    total_qty = sum(e["qty"] for e in acc.values())
    rows = []
    for e in acc.values():
        busy = e["work"] + e["stop"]
        rows.append({
            "employee": e["employee"],
            "ops": e["ops"],
            "qty": e["qty"],
            "share": round(e["qty"] / total_qty * 100, 1) if total_qty else 0.0,
            "avg_duration": round(_avg(e["durations"]), 1),
            "avg_rate": round(_avg(e["rates"]), 1),
            "work": e["work"],
            "stop": e["stop"],
            "pauses": e["pauses"],
            "utilization": round(e["work"] / busy * 100, 1) if busy else 0.0,
            "days": len(e["days"]),
            "products": len(e["products"]),
            "equipment": len(e["equipment"]),
        })
    rows.sort(key=lambda x: -x["qty"])
    return rows


def plan_for_period(plans, date_from="", date_to="", equipment="all", product="all",
                    exclude_dates=()):
    """Складывает план по датам периода. Ключ в хранилище: "оборудование|продукт"."""
    excluded = set(exclude_dates or ())
    acc, days = {}, []
    for day, items in (plans or {}).items():
        if day in excluded:
            continue
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        days.append(day)
        for key, qty in items.items():
            eq, _, pr = str(key).partition("|")
            if equipment not in ("all", "") and eq != equipment:
                continue
            if product not in ("all", "") and pr != product:
                continue
            slot = acc.setdefault((eq, pr), {"equipment": eq, "product": pr, "plan": 0})
            slot["plan"] += int(qty or 0)
    return acc, sorted(days)


def plan_vs_fact(production, plans, date_from="", date_to="", equipment="all",
                 product="all", exclude_dates=()):
    acc, days = plan_for_period(plans, date_from, date_to, equipment, product, exclude_dates)
    for r in production:
        slot = acc.setdefault((r["equipment"], r["product"]),
                              {"equipment": r["equipment"], "product": r["product"], "plan": 0})
        slot["fact"] = slot.get("fact", 0) + r["qty"]

    rows = []
    for slot in acc.values():
        plan = slot.get("plan", 0)
        fact = slot.get("fact", 0)
        rows.append({
            "equipment": slot["equipment"],
            "product": slot["product"],
            "plan": plan,
            "fact": fact,
            "diff": fact - plan,
            "done": round(fact / plan * 100, 1) if plan else None,
        })
    # Сначала самые крупные отставания, затем всё остальное по факту.
    rows.sort(key=lambda x: (x["diff"] if x["plan"] else 10**12, -x["fact"]))
    total_plan = sum(r["plan"] for r in rows)
    total_fact = sum(r["fact"] for r in rows)
    planned = [r for r in rows if r["plan"] > 0]
    # Недобор считаем по отстающим позициям: перевыполнение по одной номенклатуре
    # не закрывает провал по другой — на производстве это разные линии и заказы.
    shortfall = sum(r["plan"] - r["fact"] for r in planned if r["fact"] < r["plan"])
    return {
        "has_plan": total_plan > 0,
        "plan_days": days,
        "total_plan": total_plan,
        "total_fact": total_fact,
        "diff": total_fact - total_plan,
        "done": round(total_fact / total_plan * 100, 1) if total_plan else None,
        "positions": len(planned),
        "positions_done": sum(1 for r in planned if r["fact"] >= r["plan"]),
        "shortfall": shortfall,
        "rows": rows[:40],
        "behind": [r for r in planned if r["fact"] < r["plan"]][:10],
    }


def build_summary(production, pauses, detail_limit=300, plans=None, plan_filters=None):
    durations = [duration_min(r["start_time"], r["end_time"]) for r in production]
    work_minutes = sum(durations)
    stop_minutes = sum(duration_min(r["start_time"], r["end_time"]) for r in pauses)
    rates = [
        r["qty"] / (duration_min(r["start_time"], r["end_time"]) / 60)
        for r in production
        if r["qty"] > 0 and duration_min(r["start_time"], r["end_time"]) > 0
    ]
    total_qty = sum(r["qty"] for r in production)
    total = work_minutes + stop_minutes

    detail = sorted(production, key=lambda r: r["created_at"], reverse=True)[:detail_limit]
    hourly = hourly_productivity(production)
    worked = [h["value"] for h in hourly if h["value"] > 0]
    # Цель по часам — средняя за период: столбцы ниже неё и есть провалы.
    hourly_target = round(sum(worked) / len(worked), 1) if worked else 0.0
    pf = plan_vs_fact(production, plans or {}, **(plan_filters or {}))

    return {
        "kpi": {
            "operations": len(production),
            "quantity": total_qty,
            "avg_duration": round(_avg(durations)),
            "avg_rate": round(_avg(rates), 1),
            "pauses": len(pauses),
            "stop_minutes": stop_minutes,
            "work_minutes": work_minutes,
            "utilization": round(work_minutes / total * 100, 1) if total else 0.0,
            "open_operations": sum(1 for r in production if r["status"] != "closed"),
        },
        "daily": daily_output(production, pauses, plans, plan_filters),
        "hourly": hourly,
        "hourly_target": hourly_target,
        "hourly_below": sum(1 for h in hourly if 0 < h["value"] < hourly_target),
        "plan": pf,
        "by_equipment": _sum_by(production, "equipment", lambda r: r["qty"]),
        "top_products": _sum_by(production, "product", lambda r: r["qty"], limit=12),
        "pause_reasons": _sum_by(pauses, "reason", lambda r: duration_min(r["start_time"], r["end_time"])),
        "work_vs_stop": [
            {"label": "Работа (мин)", "value": work_minutes},
            {"label": "Простои (мин)", "value": stop_minutes},
        ],
        "top_employees": _sum_by(production, "user_name", lambda r: r["qty"], limit=12),
        "product_timings": product_timings(production),
        "equipment_timings": equipment_timings(production, pauses),
        "employee_timings": employee_timings(production, pauses),
        "detail": detail,
    }


def build_meta(snapshot, exclude_dates=()):
    excluded = set(exclude_dates or ())
    prod = [r for r in snapshot["production"] if r["date"] not in excluded]
    stop = [r for r in snapshot["pauses"] if r["date"] not in excluded]
    equipment = sorted({r["equipment"] for r in prod} | {r["equipment"] for r in stop})
    products = sorted({r["product"] for r in prod})
    employees = sorted({r["user_name"] for r in prod} | {r["user_name"] for r in stop})
    dates = sorted({r["date"] for r in prod if r["date"]} | {r["date"] for r in stop if r["date"]})
    return {
        "equipment": equipment,
        "products": products,
        "employees": employees,
        "date_min": dates[0] if dates else "",
        "date_max": dates[-1] if dates else "",
        "production_count": len(prod),
        "pause_count": len(stop),
        "excluded_dates": sorted(excluded),
    }
