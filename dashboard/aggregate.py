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


def kpi_block(production, pauses):
    durations = [duration_min(r["start_time"], r["end_time"]) for r in production]
    work_minutes = sum(durations)
    stop_minutes = sum(duration_min(r["start_time"], r["end_time"]) for r in pauses)
    rates = [
        r["qty"] / (duration_min(r["start_time"], r["end_time"]) / 60)
        for r in production
        if r["qty"] > 0 and duration_min(r["start_time"], r["end_time"]) > 0
    ]
    total = work_minutes + stop_minutes
    return {
        "operations": len(production),
        "quantity": sum(r["qty"] for r in production),
        "avg_duration": round(_avg(durations)),
        "avg_rate": round(_avg(rates), 1),
        "pauses": len(pauses),
        "stop_minutes": stop_minutes,
        "work_minutes": work_minutes,
        "utilization": round(work_minutes / total * 100, 1) if total else 0.0,
        "open_operations": sum(1 for r in production if r["status"] != "closed"),
    }


def hourly_target_of(hourly):
    """Цель по часам — средняя за период: столбцы ниже неё и есть провалы."""
    worked = [h["value"] for h in hourly if h["value"] > 0]
    return round(sum(worked) / len(worked), 1) if worked else 0.0


def build_summary(production, pauses, detail_limit=300, plans=None, plan_filters=None):
    detail = sorted(production, key=lambda r: r["created_at"], reverse=True)[:detail_limit]
    hourly = hourly_productivity(production)
    hourly_target = hourly_target_of(hourly)
    pf = plan_vs_fact(production, plans or {}, **(plan_filters or {}))
    kpi = kpi_block(production, pauses)

    return {
        "kpi": kpi,
        "daily": daily_output(production, pauses, plans, plan_filters),
        "hourly": hourly,
        "hourly_target": hourly_target,
        "hourly_below": sum(1 for h in hourly if 0 < h["value"] < hourly_target),
        "plan": pf,
        "by_equipment": _sum_by(production, "equipment", lambda r: r["qty"]),
        "top_products": _sum_by(production, "product", lambda r: r["qty"], limit=12),
        "pause_reasons": _sum_by(pauses, "reason", lambda r: duration_min(r["start_time"], r["end_time"])),
        "work_vs_stop": [
            {"label": "Работа (мин)", "value": kpi["work_minutes"]},
            {"label": "Простои (мин)", "value": kpi["stop_minutes"]},
        ],
        "top_employees": _sum_by(production, "user_name", lambda r: r["qty"], limit=12),
        "product_timings": product_timings(production),
        "equipment_timings": equipment_timings(production, pauses),
        "employee_timings": employee_timings(production, pauses),
        "detail": detail,
    }


# ── LFL: сравнение с тем же отрезком в прошлом ────────────────────────────────
#
# Сравниваем «как с как»: те же фильтры, та же длина отрезка. Если период
# заканчивается сегодня, сегодняшний день ещё не закончился — поэтому у дня,
# с которым он сравнивается, берём только записи до того же времени суток.
# Иначе утром любой период «проседает» просто потому, что день не доработан.

# Недель в полосе «неделя к неделе»: не меньше MIN_WEEKS, чтобы тренд был виден
# и при недельном периоде, и не больше MAX_WEEKS, чтобы полоса не разрасталась.
MIN_WEEKS, MAX_WEEKS = 4, 8
# Показатель → как считать изменение: "pct" — в процентах, "pp" — в процентных
# пунктах (коэффициент использования уже в процентах, относительная доля от доли
# только путает).
LFL_METRICS = (("operations", "pct"), ("quantity", "pct"), ("avg_duration", "pct"),
               ("avg_rate", "pct"), ("pauses", "pct"), ("stop_minutes", "pct"),
               ("utilization", "pp"))


def _day_minute(row):
    """Время записи в минутах от полуночи. Дата строки берётся из created_at — время тоже."""
    stamp = row.get("created_at") or ""
    return parse_hhmm(stamp[11:16]) if len(stamp) >= 16 else None


def _change(cur, prev, mode="pct"):
    if mode == "pp":
        return round(cur - prev, 1)
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _window(rows, start, end, cut_day=None, cutoff=None):
    """Строки за [start, end]; у дня cut_day — только записанные не позже cutoff."""
    s, e = start.isoformat(), end.isoformat()
    out = []
    for r in rows:
        d = r["date"]
        if not s <= d <= e:
            continue
        if cutoff is not None and cut_day is not None and d == cut_day.isoformat():
            minute = _day_minute(r)
            if minute is not None and minute > cutoff:
                continue
        out.append(r)
    return out


def _days_with_data(*groups):
    return len({r["date"] for rows in groups for r in rows if r["date"]})


def _span(start, end):
    if start == end:
        return start.strftime("%d.%m")
    return f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')}"


def build_lfl(snapshot, summary, production, pauses, date_from="", date_to="",
              filters=None, exclude_dates=(), now=None):
    """Дополняет summary сравнением с прошлым: KPI к прошлому периоду той же длины,
    каждый день к тому же дню неделей раньше, недели друг к другу, часы к часам."""
    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
    except (TypeError, ValueError):
        return {"available": False, "reason": "для сравнения нужен период с датами от и до"}
    today = now.date() if now else None
    if today and d_to > today:
        d_to = today  # будущие дни пустые — сравнивать их не с чем
    if d_to < d_from:
        return {"available": False, "reason": "период ещё не начался"}

    partial = today is not None and d_to == today
    cutoff = now.hour * 60 + now.minute if partial else None
    days = (d_to - d_from).days + 1
    weeks = min(MAX_WEEKS, max(MIN_WEEKS, -(-days // 7)))
    week = timedelta(days=7)

    # Одна фильтрация на весь нужный отрезок, дальше только нарезка по датам.
    prev_to = d_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)
    oldest = min(prev_from, d_to - week * weeks - timedelta(days=6), d_from - week)
    rows, stops = apply_filters(snapshot, date_from=oldest.isoformat(), date_to=d_to.isoformat(),
                                exclude_dates=exclude_dates, **(filters or {}))

    # 1. KPI: прошлый период той же длины, вплотную перед текущим.
    prev_rows = _window(rows, prev_from, prev_to, prev_to, cutoff)
    prev_stops = _window(stops, prev_from, prev_to, prev_to, cutoff)
    cur_k, prev_k = summary["kpi"], kpi_block(prev_rows, prev_stops)
    prev_empty = not prev_rows and not prev_stops
    kpi = {}
    for key, mode in LFL_METRICS:
        change = None
        if not prev_empty and not (mode == "pp" and not (prev_k["work_minutes"] + prev_k["stop_minutes"])):
            change = _change(cur_k[key], prev_k[key], mode)
        kpi[key] = {"prev": prev_k[key], "change": change, "mode": mode}

    # 2. Каждый день — к тому же дню недели неделей раньше.
    by_day = {}
    shifted = _window(rows, d_from - week, d_to - week, d_to - week, cutoff)
    for r in shifted:
        by_day[r["date"]] = by_day.get(r["date"], 0) + r["qty"]
    for d in summary["daily"]:
        try:
            ref = (date.fromisoformat(d["date"]) - week).isoformat()
        except ValueError:
            continue
        d["lfl_date"] = ref
        d["lfl_qty"] = by_day.get(ref, 0)
        d["lfl_change"] = _change(d["qty"], d["lfl_qty"])

    # 3. Недели, выровненные по концу периода: последняя — «последние 7 дней».
    def week_stats(end, cut=False):
        start = end - timedelta(days=6)
        w_rows = _window(rows, start, end, end, cutoff if cut else None)
        w_stops = _window(stops, start, end, end, cutoff if cut else None)
        return {"from": start.isoformat(), "to": end.isoformat(), "label": _span(start, end),
                "qty": sum(r["qty"] for r in w_rows), "pauses": len(w_stops),
                "days": _days_with_data(w_rows, w_stops)}

    blocks = [week_stats(d_to - week * k) for k in range(weeks + 1)]
    out_weeks = []
    for k in range(weeks):
        cur, base = blocks[k], blocks[k + 1]
        # Неполную текущую неделю сравниваем с прошлой, обрезанной до того же часа.
        if k == 0 and partial:
            base = week_stats(d_to - week, cut=True)
        empty = not base["days"]
        out_weeks.append({**cur,
                          "partial": k == 0 and partial,
                          "base_label": base["label"], "base_qty": base["qty"],
                          "base_pauses": base["pauses"], "base_days": base["days"],
                          "qty_change": None if empty else _change(cur["qty"], base["qty"]),
                          "pauses_change": None if empty else _change(cur["pauses"], base["pauses"])})
    # Неделя без единой записи — не «ноль выпуска», а отсутствие данных (например,
    # до запуска бота). Показывать её строкой «0 шт» только сбивает с толку.
    out_weeks = [w for w in out_weeks if w["days"]]
    out_weeks.reverse()  # по хронологии, как столбцы графика

    # 4. Часы — к тем же часам прошлого периода.
    prev_hourly = hourly_productivity(prev_rows)
    prev_target = hourly_target_of(prev_hourly)
    cur_hourly = summary["hourly"]
    better = worse = 0
    for cur_h, prev_h in zip(cur_hourly, prev_hourly):
        cur_h["lfl_value"] = prev_h["value"]
        cur_h["lfl_change"] = _change(cur_h["value"], prev_h["value"]) if cur_h["value"] else None
        if cur_h["value"] and prev_h["value"]:
            if cur_h["value"] > prev_h["value"]:
                better += 1
            elif cur_h["value"] < prev_h["value"]:
                worse += 1

    return {
        "available": True,
        "days": days,
        "cur_from": d_from.isoformat(), "cur_to": d_to.isoformat(),
        "prev_from": prev_from.isoformat(), "prev_to": prev_to.isoformat(),
        "prev_label": _span(prev_from, prev_to),
        "cur_days": _days_with_data(production, pauses),
        "prev_days": _days_with_data(prev_rows, prev_stops),
        "prev_empty": prev_empty,
        "partial": partial,
        "cutoff": f"{cutoff // 60:02d}:{cutoff % 60:02d}" if cutoff is not None else "",
        "kpi": kpi,
        "weeks": out_weeks,
        "hourly": {
            "prev_target": prev_target,
            "target_change": _change(summary["hourly_target"], prev_target),
            "prev_below": sum(1 for h in prev_hourly if 0 < h["value"] < prev_target),
            "better": better,
            "worse": worse,
        },
    }


def change_text(change, mode="pct"):
    if change is None:
        return "—"
    unit = " п.п." if mode == "pp" else "%"
    value = f"{abs(change):.1f}".replace(".", ",")
    if change > 0:
        return f"▲ +{value}{unit}"
    if change < 0:
        return f"▼ −{value}{unit}"
    return f"= 0{unit}"


def lfl_lines(lfl, k):
    """Строки сравнения для сводки фактов и PDF. Только числа, без оценок."""
    def n(value):
        return f"{value:,.0f}".replace(",", " ")

    head = f"Прошлый период: {lfl['prev_label']} ({lfl['days']} дн.)"
    if lfl.get("partial"):
        head += f", последний день взят до {lfl['cutoff']} — как и сегодняшний"
    rows = [head + "."]
    if lfl.get("prev_empty"):
        rows.append("За прошлый период с этими фильтрами данных нет — сравнивать не с чем.")
        return rows
    if lfl["prev_days"] != lfl["cur_days"]:
        rows.append(f"Дней с данными: сейчас {lfl['cur_days']}, в прошлом периоде {lfl['prev_days']}"
                    " — суммы сравниваются неравные.")
    c = lfl["kpi"]
    for title, key, unit in (("Выпуск", "quantity", " шт"), ("Операций", "operations", ""),
                             ("Средняя длительность", "avg_duration", " мин"),
                             ("Производительность", "avg_rate", " шт/ч"),
                             ("Простоев", "pauses", ""), ("Простои", "stop_minutes", " мин"),
                             ("Коэффициент использования", "utilization", "%")):
        prev = c[key]["prev"]
        prev_text = n(prev) if isinstance(prev, int) else f"{prev}".replace(".", ",")
        cur = k[key]
        cur_text = n(cur) if isinstance(cur, int) else f"{cur}".replace(".", ",")
        line = (f"{title}: {cur_text}{unit} против {prev_text}{unit}, "
                f"{change_text(c[key]['change'], c[key]['mode'])}")
        rows.append(line if line.endswith(".") else line + ".")
    h = lfl.get("hourly") or {}
    if h.get("better") or h.get("worse"):
        rows.append(f"По часам: выше прошлого периода в {h['better']} ч, ниже — в {h['worse']} ч.")
    return rows


def build_analysis(summary, filters=None):
    """Плоская выжимка фактов для PDF: суммы, доли, кто и сколько.

    Без причин и рекомендаций — названия причин простоя берём ровно так,
    как их записал бот, ничего не додумывая.
    """
    k = summary["kpi"]
    daily = summary["daily"]
    blocks = []

    total = k["quantity"]

    def n(value):
        return f"{value:,.0f}".replace(",", " ")

    def share(v):
        return f" ({round(v / total * 100, 1)}%)" if total else ""

    period = [
        f"Операций за период: {k['operations']}, из них открытых: {k['open_operations']}.",
        f"Выпуск: {n(k['quantity'])} шт.",
        f"Средняя длительность операции: {k['avg_duration']} мин, средняя производительность: {k['avg_rate']} шт/ч.",
        f"Простоев зафиксировано: {k['pauses']}, суммарно {n(k['stop_minutes'])} мин.",
        f"Коэффициент использования: {k['utilization']}% (работа {n(k['work_minutes'])} мин, простои {n(k['stop_minutes'])} мин).",
    ]
    blocks.append(("Итоги периода", period))

    lfl = summary.get("lfl") or {}
    if lfl.get("available"):
        blocks.append(("Сравнение с прошлым периодом", lfl_lines(lfl, k)))
        if lfl.get("weeks"):
            blocks.append(("Неделя к неделе", [
                f"{w['label']}{' (до ' + lfl['cutoff'] + ')' if w['partial'] else ''}: "
                f"{n(w['qty'])} шт {change_text(w['qty_change'])}, "
                f"простоев {w['pauses']} {change_text(w['pauses_change'])}"
                for w in lfl["weeks"]]))

    if daily:
        best = max(daily, key=lambda d: d["qty"])
        worst = min(daily, key=lambda d: d["qty"])
        rows = [
            f"Дней с данными: {len(daily)}.",
            f"Наибольший выпуск: {best['date']} — {n(best['qty'])} шт ({best['ops']} операций).",
            f"Наименьший выпуск: {worst['date']} — {n(worst['qty'])} шт ({worst['ops']} операций).",
        ]
        with_stop = [d for d in daily if d["stop"]]
        if with_stop:
            top_stop = max(with_stop, key=lambda d: d["stop"])
            rows.append(f"Дней с простоями: {len(with_stop)}; наибольший — {top_stop['date']}, {n(top_stop['stop'])} мин.")
        else:
            rows.append("Дней с зафиксированными простоями: 0.")
        blocks.append(("По дням", rows))

    for title, items, unit in (
        ("Топ продуктов", summary["top_products"][:5], "шт"),
        ("Выпуск по оборудованию", summary["by_equipment"][:5], "шт"),
        ("Выпуск по сотрудникам", summary.get("top_employees", [])[:5], "шт"),
    ):
        if items:
            blocks.append((title, [f"{i + 1}. {x['label']} — {n(x['value'])} {unit}{share(x['value'])}"
                                   for i, x in enumerate(items)]))

    target = summary.get("hourly_target", 0)
    if target:
        below = [h["label"] for h in summary["hourly"] if 0 < h["value"] < target]
        rows = [f"Средняя производительность по часам: {target} шт/ч.",
                f"Часов с производительностью ниже средней: {len(below)}."]
        if below:
            rows.append("Это часы: " + ", ".join(below) + ".")
        idle = [h["label"] for h in summary["hourly"] if h["value"] == 0]
        if idle:
            rows.append(f"Часов без выпуска: {len(idle)} ({', '.join(idle)}).")
        blocks.append(("Производительность по часам", rows))

    reasons = summary["pause_reasons"]
    if reasons:
        total_stop = sum(r["value"] for r in reasons)
        rows = [f"Причин в справочнике за период: {len(reasons)}, суммарно {n(total_stop)} мин."]
        rows += [f"{i + 1}. {r['label']} — {n(r['value'])} мин"
                 f" ({round(r['value'] / total_stop * 100, 1)}%)" for i, r in enumerate(reasons[:5])]
        blocks.append(("Простои по причинам", rows))

    plan = summary.get("plan") or {}
    if plan.get("has_plan"):
        skew = plan.get("unsupported_filters") or []
        rows = [
            f"План: {n(plan['total_plan'])} шт на даты {', '.join(plan['plan_days'])}.",
            (f"Факт: {n(plan['total_fact'])} шт, доля от полного плана {plan['done']}%."
             if skew else f"Факт: {n(plan['total_fact'])} шт, выполнение {plan['done']}%."),]
        if skew:
            rows.append("В файле плана нет разреза по " + ", ".join(skew)
                        + ": факт сужен фильтром, план взят целиком.")
        rows += [
            f"Закрыто позиций: {plan['positions_done']} из {plan['positions']}.",
            f"Недобор по отстающим позициям: {n(plan['shortfall'])} шт.",
        ]
        rows += [f"{i + 1}. {b['product']} ({b['equipment']}) — план {n(b['plan'])}, факт {n(b['fact'])}, {b['done']}%"
                 for i, b in enumerate(plan["behind"][:5])]
        blocks.append(("План и факт", rows))

    if filters:
        applied = [f"{name}: {value}" for name, value in filters if value]
        if applied:
            blocks.append(("Применённые фильтры", applied))
    return blocks


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
