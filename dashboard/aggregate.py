"""Расчёт показателей дашборда. Чистые функции над снимком данных."""
import re
from datetime import date, timedelta

HHMM = re.compile(r"^(\d{1,2})[:.\-](\d{2})")
DAY_SHIFT = (8 * 60, 20 * 60)  # 08:00–19:59 — день, остальное — ночь


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


def apply_filters(snapshot, date_from="", date_to="", equipment="", product="", shift=""):
    equipment = equipment or "all"
    product = product or "all"
    shift = shift or "all"

    def in_range(row):
        d = row.get("date", "")
        if not d:
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
    ]
    pauses = [
        r for r in snapshot["pauses"]
        if in_range(r)
        and (equipment == "all" or r["equipment"] == equipment)
        and (shift == "all" or shift_of(r["start_time"]) == shift)
    ]
    return production, pauses


def _sum_by(rows, key, value_fn, limit=None):
    acc = {}
    for r in rows:
        acc[r[key]] = acc.get(r[key], 0) + value_fn(r)
    items = [{"label": k, "value": round(v, 1)} for k, v in acc.items() if v > 0]
    items.sort(key=lambda x: -x["value"])
    return items[:limit] if limit else items


def daily_output(production, pauses):
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
    out = []
    for d in sorted(acc.values(), key=lambda x: x["date"]):
        out.append({
            "date": d["date"],
            "ops": d["ops"],
            "qty": d["qty"],
            "avg_duration": round(_avg(d["durations"])),
            "stop": d["stop"],
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


def build_summary(production, pauses, detail_limit=300):
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
        "daily": daily_output(production, pauses),
        "hourly": hourly_productivity(production),
        "by_equipment": _sum_by(production, "equipment", lambda r: r["qty"]),
        "top_products": _sum_by(production, "product", lambda r: r["qty"], limit=12),
        "pause_reasons": _sum_by(pauses, "reason", lambda r: duration_min(r["start_time"], r["end_time"])),
        "work_vs_stop": [
            {"label": "Работа (мин)", "value": work_minutes},
            {"label": "Простои (мин)", "value": stop_minutes},
        ],
        "product_timings": product_timings(production),
        "equipment_timings": equipment_timings(production, pauses),
        "detail": detail,
    }


def build_meta(snapshot):
    equipment = sorted({r["equipment"] for r in snapshot["production"]} | {r["equipment"] for r in snapshot["pauses"]})
    products = sorted({r["product"] for r in snapshot["production"]})
    dates = sorted({r["date"] for r in snapshot["production"] if r["date"]} | {r["date"] for r in snapshot["pauses"] if r["date"]})
    return {
        "equipment": equipment,
        "products": products,
        "date_min": dates[0] if dates else "",
        "date_max": dates[-1] if dates else "",
        "production_count": len(snapshot["production"]),
        "pause_count": len(snapshot["pauses"]),
    }
