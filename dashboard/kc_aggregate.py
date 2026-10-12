"""Расчёт показателей «КЦ» (кондитерский цех). Чистые функции над снимком kc_source.

Правила (они же в README):
- каждая запись — самостоятельная операция, маршрута изделия нет; штуки разных операций
  одной продукции НЕ складываются в готовый выпуск. Сумма штук называется «Обработано
  изделий по операциям»: одно изделие, прошедшее три операции, учтено три раза;
- завершённая работа (операции, штуки, чел·ч, длительность) считается по времени КОНЦА
  операции (end_at), начатые — по началу; фильтры периода, смены и часа — по времени события;
- человеко-часы = длительность, ч × люди; шт/ч = шт / длительность, ч;
  шт/чел·ч = шт / чел·ч; средние по группе — Σ шт / Σ часов (Σ чел·ч), не среднее коэффициентов;
- в средние и нормы идут только корректно закрытые операции (0 < t ≤ лимита);
- people — численность группы, а не список работников: выработка сотрудникам не приписывается.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from proofing_aggregate import (Window, WEEKDAYS, _change, _day, _hm, _iso, _local, _num_text, _shift_day, _span,
                                _valid_day, fmt_int, fmt_min, plural, shift_of)
from sources import TZ

STATUS_NAMES = {"closed": "завершена", "open": "в работе", "cancelled": "отменена"}
MAX_DETAIL = 1000
LFL_METRICS = ("completed", "pcs", "person_hours", "avg_duration", "pcs_per_ph", "pauses", "stop_minutes")
MIX_WARNING = ("Шт/чел·ч по всему цеху смешивает разные операции, а одно изделие учитывается на каждой "
               "операции заново — это не выработка готовой продукции. Сравнивай нормы по паре «продукция + операция».")
PCS_NOTE = "сумма штук по всем операциям: изделие, прошедшее несколько операций, учтено несколько раз"
NORM_NOTE = ("Наблюдаемые значения по факту работы за период — основа для расчёта нормативов, "
             "а не утверждённый норматив предприятия.")


def _r(value, digits=1):
    return None if value is None else round(value, digits)


def _div(a, b, digits=1):
    return round(a / b, digits) if b else None


# ── фильтры ───────────────────────────────────────────────────────────────────

def parse_filters(date_from="", date_to="", product="all", operation="all", employee="all", shift="all",
                  hour="", status="all"):
    def clean(value):
        value = (value or "").strip()
        return value or "all"

    try:
        hour_num = int(hour) if str(hour or "").strip() not in ("", "all") else None
    except (TypeError, ValueError):
        hour_num = None
    if hour_num is not None and not 0 <= hour_num <= 23:
        hour_num = None
    shift, status = clean(shift), clean(status)
    return {"date_from": _valid_day(date_from), "date_to": _valid_day(date_to), "product": clean(product),
            "operation": clean(operation), "employee": clean(employee),
            "shift": shift if shift in ("day", "night") else "all", "hour": hour_num,
            "status": status if status in STATUS_NAMES else "all"}


def op_matches(o, f):
    return ((f["product"] == "all" or o["product"] == f["product"])
            and (f["operation"] == "all" or o["operation"] == f["operation"])
            and (f["employee"] == "all" or f["employee"] in (o["user_name"], o["closed_by_name"]))
            and (f["status"] == "all" or o["status"] == f["status"]))


def pause_matches(p, f):
    # У остановки нет продукции, операции и статуса — эти фильтры её не сужают.
    return f["employee"] == "all" or p["user_name"] == f["employee"]


def done_at(o):
    """Время завершённой работы: конец закрытой операции."""
    return o["end_at"] if o["status"] == "closed" else None


def ended_at(o):
    """Когда операция перестала идти: конец, или момент отмены."""
    if o["status"] == "closed":
        return o["end_at"]
    if o["status"] == "cancelled":
        return o["closed_at"] or o["start_at"]
    return None


def in_work_at(ops, at):
    return [o for o in ops if o["start_at"] <= at and (ended_at(o) is None or ended_at(o) > at)]


def _rated(rows):
    """Корректно закрытые операции со штуками и людьми — база для шт/ч и шт/чел·ч."""
    return [o for o in rows if o["valid"] and o["pcs"] and o["people"]]


def group_stats(rows):
    """Сводка по группе завершённых операций: Σ шт / Σ часов, а не среднее коэффициентов."""
    valid = [o for o in rows if o["valid"]]
    rated = _rated(rows)
    hours = sum(o["duration_min"] for o in rated) / 60
    ph = sum(o["person_hours"] for o in rated)
    durations = [o["duration_min"] for o in valid]
    people = [o["people"] for o in valid if o["people"]]
    return {
        "completed": len(rows),
        "pcs": sum(o["pcs"] or 0 for o in rows),
        "person_hours": round(sum(o["person_hours"] or 0 for o in valid), 2),
        "avg_duration": _r(sum(durations) / len(durations)) if durations else None,
        "min_duration": _r(min(durations)) if durations else None,
        "max_duration": _r(max(durations)) if durations else None,
        "avg_people": round(sum(people) / len(people), 2) if people else None,
        "pcs_per_hour": _div(sum(o["pcs"] for o in rated), hours),
        "pcs_per_ph": _div(sum(o["pcs"] for o in rated), ph),
        "invalid": len(rows) - len(valid),
    }


# ── KPI ───────────────────────────────────────────────────────────────────────

def kpi_block(ops, pauses, win, at=None):
    completed = [o for o in ops if win.hit(done_at(o))]
    started = [o for o in ops if win.hit(o["start_at"])]
    g = group_stats(completed)
    return {
        "completed": g["completed"],
        "started": len(started),
        "cancelled": sum(1 for o in started if o["status"] == "cancelled"),
        "in_work": len(in_work_at(ops, at)) if at else 0,
        "pcs": g["pcs"],
        "person_hours": g["person_hours"],
        "avg_duration": g["avg_duration"],
        "avg_people": g["avg_people"],
        "pcs_per_hour": g["pcs_per_hour"],
        "pcs_per_ph": g["pcs_per_ph"],
        "invalid": g["invalid"],
        "pauses": len(pauses),
        "stop_minutes": round(sum(p["duration_min"] for p in pauses), 1),
    }


def kpi_rows(k):
    """(подпись, значение для людей, ключ LFL) — одна таблица для страницы, Excel и PDF."""
    return [
        ("Завершено операций", fmt_int(k["completed"]), "completed"),
        ("Операций в работе (на конец периода)", fmt_int(k["in_work"]), None),
        ("Обработано изделий по операциям, шт", fmt_int(k["pcs"]) if k["completed"] else "нет данных", "pcs"),
        ("Отработано человеко-часов", _num_text(k["person_hours"]) if k["completed"] else "нет данных", "person_hours"),
        ("Средняя длительность операции", fmt_min(k["avg_duration"]), "avg_duration"),
        ("Средняя выработка, шт/чел·ч", "нет данных" if k["pcs_per_ph"] is None else _num_text(k["pcs_per_ph"]), "pcs_per_ph"),
        ("Критических остановок", fmt_int(k["pauses"]), "pauses"),
        ("Продолжительность простоев", fmt_min(k["stop_minutes"]) if k["pauses"] else "0 мин", "stop_minutes"),
    ]


# ── разрезы ───────────────────────────────────────────────────────────────────

def daily_block(ops, pauses, win, lfl_win=None):
    acc = {}

    def slot(day):
        return acc.setdefault(day, {"date": day, "rows": [], "started": 0, "pauses": 0, "stop": 0.0})

    for o in ops:
        if win.hit(o["start_at"]):
            slot(_day(o["start_at"]))["started"] += 1
        if win.hit(done_at(o)):
            slot(_day(o["end_at"]))["rows"].append(o)
    for p in pauses:
        d = slot(_day(p["start_at"]))
        d["pauses"] += 1
        d["stop"] += p["duration_min"]
    week_ago = defaultdict(lambda: {"completed": 0, "pcs": 0})
    if lfl_win is not None:
        for o in ops:
            if lfl_win.hit(done_at(o)):
                w = week_ago[_day(o["end_at"])]
                w["completed"] += 1
                w["pcs"] += o["pcs"] or 0
    out = []
    for d in sorted(acc.values(), key=lambda x: x["date"]):
        g = group_stats(d["rows"])
        row = {"date": d["date"], "weekday": WEEKDAYS[date.fromisoformat(d["date"]).weekday()],
               "started": d["started"], "completed": g["completed"], "pcs": g["pcs"],
               "person_hours": g["person_hours"], "avg_duration": g["avg_duration"], "pcs_per_ph": g["pcs_per_ph"],
               "pauses": d["pauses"], "stop": round(d["stop"], 1)}
        if lfl_win is not None:
            ref = (date.fromisoformat(d["date"]) - timedelta(days=7)).isoformat()
            row.update(lfl_date=ref, lfl_completed=week_ago[ref]["completed"], lfl_pcs=week_ago[ref]["pcs"],
                       lfl_change=_change(row["pcs"], week_ago[ref]["pcs"]))
        out.append(row)
    return out


def hourly_block(ops, win):
    """Штуки и операции по часу ЗАВЕРШЕНИЯ операции."""
    pcs, count = [0] * 24, [0] * 24
    for o in ops:
        if win.hit(done_at(o)):
            h = _local(o["end_at"]).hour
            pcs[h] += o["pcs"] or 0
            count[h] += 1
    return [{"label": f"{h:02d}:00", "hour": h, "value": pcs[h], "completed": count[h]} for h in range(24)]


def by_key(ops, win, key):
    groups = defaultdict(list)
    for o in ops:
        if win.hit(done_at(o)):
            groups[o[key]].append(o)
    rows = [{key: name, **group_stats(rows)} for name, rows in groups.items()]
    rows.sort(key=lambda r: (-r["person_hours"], -r["pcs"], r[key]))
    return rows


def norms_block(ops, win, prev_win=None):
    """Нормы по паре «продукция + операция»: только сопоставимые одинаковые работы."""
    groups, prev = defaultdict(list), defaultdict(list)
    for o in ops:
        if win.hit(done_at(o)):
            groups[(o["product"], o["operation"])].append(o)
        elif prev_win is not None and prev_win.hit(done_at(o)):
            prev[(o["product"], o["operation"])].append(o)
    rows = []
    for (product, operation), items in groups.items():
        g = group_stats(items)
        p = group_stats(prev.get((product, operation), [])) if prev_win is not None else None
        rows.append({"product": product, "operation": operation, **g,
                     "prev_pcs_per_ph": p["pcs_per_ph"] if p else None,
                     "prev_completed": p["completed"] if p else 0,
                     "pcs_per_ph_change": _change(g["pcs_per_ph"], p["pcs_per_ph"]) if p else None})
    rows.sort(key=lambda r: (r["product"].lower(), -r["person_hours"], r["operation"].lower()))
    return rows


def shift_key(dt):
    """(дата смены, смена): ночь 20:00–08:00 относится к дате её начала (00:00–07:59 — к вчерашней)."""
    local = _local(dt)
    kind = shift_of(local)
    day = local.date() - timedelta(days=1) if kind == "night" and local.hour < 8 else local.date()
    return day.isoformat(), kind


def shift_block(ops, win):
    groups = defaultdict(list)
    for o in ops:
        if win.hit(done_at(o)):
            groups[shift_key(o["end_at"])].append(o)
    rows = []
    for (day, kind), items in groups.items():
        g = group_stats(items)
        rows.append({"shift_date": day, "shift": kind, "shift_name": "день 08–20" if kind == "day" else "ночь 20–08",
                     "completed": g["completed"], "pcs": g["pcs"], "person_hours": g["person_hours"],
                     "pcs_per_ph": g["pcs_per_ph"],
                     "midnight": sum(1 for o in items if _day(o["start_at"]) != _day(o["end_at"]))})
    rows.sort(key=lambda r: (r["shift_date"], r["shift"] == "night"), reverse=True)
    return rows


def employee_block(ops, pauses, win):
    """Кто начал / завершил / отменил. Выработку людям не приписываем: people — численность группы."""
    acc = {}

    def slot(name):
        return acc.setdefault(name, {"employee": name, "started": 0, "closed": 0, "cancelled": 0,
                                     "ops": set(), "pcs_closed": 0, "pauses": 0, "stop": 0.0,
                                     "hours": [0] * 24})

    for o in ops:
        if win.hit(o["start_at"]) and o["user_name"] != "—":
            e = slot(o["user_name"])
            e["started"] += 1
            e["ops"].add(o["id"])
            e["hours"][_local(o["start_at"]).hour] += 1
        if o["closed_by_name"]:
            at = done_at(o) if o["status"] == "closed" else (o["closed_at"] if o["status"] == "cancelled" else None)
            if win.hit(at):
                e = slot(o["closed_by_name"])
                e["ops"].add(o["id"])
                e["hours"][_local(at).hour] += 1
                if o["status"] == "closed":
                    e["closed"] += 1
                    e["pcs_closed"] += o["pcs"] or 0
                else:
                    e["cancelled"] += 1
    for p in pauses:
        e = slot(p["user_name"])
        e["pauses"] += 1
        e["stop"] += p["duration_min"]
    rows = [{**e, "ops": len(e["ops"]), "stop": round(e["stop"], 1), "marks": e["started"] + e["closed"] + e["cancelled"]}
            for e in acc.values()]
    rows.sort(key=lambda r: (-r["marks"], r["employee"]))
    return rows


def pause_blocks(pauses):
    reasons = {}
    daily = defaultdict(lambda: {"count": 0, "minutes": 0.0})
    for p in pauses:
        r = reasons.setdefault(p["reason"], {"label": p["reason"], "value": 0.0, "count": 0})
        r["value"] += p["duration_min"]
        r["count"] += 1
        d = daily[_day(p["start_at"])]
        d["count"] += 1
        d["minutes"] += p["duration_min"]
    by_reason = sorted(({**r, "value": round(r["value"], 1)} for r in reasons.values()), key=lambda x: -x["value"])
    by_day = [{"date": k, "count": v["count"], "minutes": round(v["minutes"], 1)} for k, v in sorted(daily.items())]
    rows = [{"date": _day(p["start_at"]), "reason": p["reason"], "start_time": _hm(p["start_at"]),
             "end_time": _hm(p["end_at"]), "minutes": round(p["duration_min"], 1), "user_name": p["user_name"]}
            for p in pauses[:300]]
    return by_reason, by_day, rows


def op_view(o, now):
    hours = o["duration_min"] / 60 if o["valid"] else None
    return {
        "id": o["id"], "product": o["product"], "operation": o["operation"], "status": o["status"],
        "status_name": STATUS_NAMES.get(o["status"], o["status"]),
        "date": _day(o["end_at"] or o["start_at"]), "start_date": _day(o["start_at"]), "start_time": _hm(o["start_at"]),
        "end_date": _day(o["end_at"]), "end_time": _hm(o["end_at"]), "start_at": _iso(o["start_at"]),
        "end_at": _iso(o["end_at"]),
        "midnight": bool(o["end_at"]) and _day(o["start_at"]) != _day(o["end_at"]),
        "duration_min": _r(o["duration_min"]), "valid": o["valid"], "pcs": o["pcs"], "people": o["people"],
        "person_hours": _r(o["person_hours"], 2),
        "pcs_per_hour": _div(o["pcs"], hours) if o["pcs"] and hours else None,
        "pcs_per_ph": _div(o["pcs"], o["person_hours"]) if o["pcs"] and o["person_hours"] else None,
        "started_by": o["user_name"], "closed_by": o["closed_by_name"],
        "open_min": round((now - o["start_at"]).total_seconds() / 60) if o["status"] == "open" else None,
    }


def op_scope(ops, win, f, at):
    """Операции периода: начало, конец или отмена в окне; без смены и часа — плюс идущие на конец периода."""
    plain = f["shift"] == "all" and f["hour"] is None
    working = {o["id"] for o in in_work_at(ops, at)} if plain and at else set()
    return [o for o in ops if o["id"] in working or win.hit(o["start_at"]) or win.hit(ended_at(o))]


# ── сборка ────────────────────────────────────────────────────────────────────

def _bounds(f, ops, pauses, now):
    if f["date_to"]:
        end = datetime.combine(date.fromisoformat(f["date_to"]) + timedelta(days=1), datetime.min.time(), TZ)
        return min(end, now)
    return now


def build_summary(snapshot, filters=None, exclude_dates=(), now=None):
    """Одна функция на страницу, Excel и PDF — цифры везде одни и те же."""
    now = now or datetime.now(TZ)
    f = parse_filters(**(filters or {}))
    win = Window(f["date_from"], f["date_to"], exclude_dates, f["shift"], f["hour"])
    ops = [o for o in snapshot["operations"] if op_matches(o, f)]
    all_pauses = [p for p in snapshot["pauses"] if pause_matches(p, f)]
    pauses = [p for p in all_pauses if win.hit_interval(p["start_at"], p["end_at"])]
    at = _bounds(f, ops, all_pauses, now)
    kpi = kpi_block(ops, pauses, win, at)

    lfl_win = prev_win = None
    lfl = {"available": False, "reason": "для сравнения нужен период с датами от и до"}
    if f["date_from"] and f["date_to"]:
        lfl_win = Window(_shift_day(f["date_from"], -7), _shift_day(f["date_to"], -7), exclude_dates, f["shift"], f["hour"])
        if f["date_to"] >= now.date().isoformat():
            lfl_win.cut_day, lfl_win.cutoff = (now.date() - timedelta(days=7)).isoformat(), now.hour * 60 + now.minute
        lfl, prev_win = build_lfl(ops, all_pauses, f, kpi, exclude_dates, now)

    by_reason, by_day, pause_rows = pause_blocks(pauses)
    products = by_key(ops, win, "product")
    operations = by_key(ops, win, "operation")
    norms = norms_block(ops, win, prev_win)
    scope = op_scope(ops, win, f, at)
    opened = [op_view(o, now) for o in ops if o["status"] == "open" and o["start_at"] <= at]
    opened.sort(key=lambda r: -(r["open_min"] or 0))
    return {
        "kind": "kc",
        "filters_applied": f,
        "kpi": kpi,
        "notes": {"pcs": PCS_NOTE, "mix": MIX_WARNING, "norms": NORM_NOTE},
        "daily": daily_block(ops, pauses, win, lfl_win),
        "hourly": hourly_block(ops, win),
        "products": products,
        "operations": operations,
        "top_products": [{"label": r["product"], "value": r["pcs"]} for r in sorted(products, key=lambda r: -r["pcs"]) if r["pcs"]][:12],
        "top_operations": [{"label": r["operation"], "value": r["pcs"]} for r in sorted(operations, key=lambda r: -r["pcs"]) if r["pcs"]][:12],
        "labor_products": [{"label": r["product"], "value": r["person_hours"]} for r in products if r["person_hours"]][:12],
        "labor_operations": [{"label": r["operation"], "value": r["person_hours"]} for r in operations if r["person_hours"]][:12],
        "heavy": sorted(norms, key=lambda r: -r["person_hours"])[:10],
        "norms": norms,
        "shifts": shift_block(ops, win),
        "employees": employee_block(ops, pauses, win),
        "pause_reasons": by_reason,
        "pause_daily": by_day,
        "pause_rows": pause_rows,
        "open_ops": opened,
        "ops_total": len(scope),
        "ops": [op_view(o, now) for o in scope[:MAX_DETAIL]],
        "issues": snapshot.get("issues", {}),
        "lfl": lfl,
    }


def build_lfl(ops, all_pauses, f, cur_kpi, exclude_dates=(), now=None):
    """Тот же отрезок прямо перед текущим, те же фильтры; сегодняшний день — до того же времени суток."""
    d_from, d_to = date.fromisoformat(f["date_from"]), date.fromisoformat(f["date_to"])
    today = now.date() if now else None
    if today and d_to > today:
        d_to = today
    if d_to < d_from:
        return {"available": False, "reason": "период ещё не начался"}, None
    partial = today is not None and d_to == today
    cutoff = now.hour * 60 + now.minute if partial else None
    days = (d_to - d_from).days + 1
    prev_to = d_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)
    win = Window(prev_from.isoformat(), prev_to.isoformat(), exclude_dates, f["shift"], f["hour"], prev_to.isoformat(), cutoff)
    pauses = [p for p in all_pauses if win.hit_interval(p["start_at"], p["end_at"])]
    prev = kpi_block(ops, pauses, win)
    empty = not (prev["completed"] or prev["started"] or prev["pauses"])
    kpi = {k: {"prev": prev[k], "change": None if empty else _change(cur_kpi[k], prev[k])} for k in LFL_METRICS}
    return ({"available": True, "days": days, "prev_from": prev_from.isoformat(), "prev_to": prev_to.isoformat(),
             "prev_label": _span(prev_from, prev_to), "prev_empty": empty, "partial": partial,
             "cutoff": f"{cutoff // 60:02d}:{cutoff % 60:02d}" if cutoff is not None else "", "kpi": kpi}, win)


def change_text(change):
    if change is None:
        return "—"
    value = f"{abs(change):.1f}".replace(".", ",")
    return f"▲ +{value}%" if change > 0 else (f"▼ −{value}%" if change < 0 else "= 0%")


def build_analysis(summary, filters=None):
    """Плоские факты для сводки и PDF: числа и оговорки, без выводов и рекомендаций."""
    k = summary["kpi"]
    rows = [
        f"Операций завершено: {k['completed']}; начато: {k['started']}"
        + (f", из них отменено: {k['cancelled']}" if k["cancelled"] else "") + f"; в работе на конец периода: {k['in_work']}.",
        (f"Обработано изделий по операциям: {fmt_int(k['pcs'])} шт ({PCS_NOTE})." if k["completed"]
         else "Обработано изделий по операциям: нет данных."),
        f"Отработано человеко-часов: {_num_text(k['person_hours'])}." if k["completed"] else "Человеко-часы: нет данных.",
        f"Средняя длительность операции: {fmt_min(k['avg_duration'])}"
        + (f", в среднем людей: {_num_text(k['avg_people'])}." if k["avg_people"] else "."),
        "Средняя выработка по цеху: " + ("нет данных." if k["pcs_per_ph"] is None else f"{_num_text(k['pcs_per_ph'])} шт/чел·ч — {MIX_WARNING}"),
        f"Критических остановок: {k['pauses']}, суммарно {fmt_min(k['stop_minutes']) if k['pauses'] else '0 мин'}.",
    ]
    if k["invalid"]:
        rows.append(f"Операций с некорректным временем (не вошли в средние и нормы): {k['invalid']}.")
    blocks = [("Итоги периода", rows)]

    lfl = summary.get("lfl") or {}
    if lfl.get("available"):
        head = f"Прошлый период: {lfl['prev_label']} ({lfl['days']} дн.)"
        if lfl.get("partial"):
            head += f", последний день взят до {lfl['cutoff']} — как и сегодняшний"
        rows = [head + "."]
        if lfl["prev_empty"]:
            rows.append("За прошлый период с этими фильтрами данных нет — сравнивать не с чем.")
        else:
            for title, key, unit in (("Завершено операций", "completed", ""), ("Обработано изделий", "pcs", " шт"),
                                     ("Человеко-часы", "person_hours", ""), ("Средняя длительность", "avg_duration", " мин"),
                                     ("Выработка", "pcs_per_ph", " шт/чел·ч"), ("Остановок", "pauses", ""),
                                     ("Простои", "stop_minutes", " мин")):
                c = lfl["kpi"][key]
                cur_t = "нет данных" if k[key] is None else _num_text(k[key]) + unit
                prev_t = "нет данных" if c["prev"] is None else _num_text(c["prev"]) + unit
                rows.append(f"{title}: {cur_t} против {prev_t}, {change_text(c['change'])}.")
        blocks.append(("Сравнение с прошлым периодом", rows))

    norms = sorted(summary["norms"], key=lambda r: -r["person_hours"])[:8]
    if norms:
        blocks.append(("Нормы: наиболее трудоёмкие пары «продукция + операция»", [
            f"{r['product']} — {r['operation']}: выполнений {r['completed']}, {fmt_int(r['pcs'])} шт, "
            f"в среднем {fmt_min(r['avg_duration'])}, людей {_num_text(r['avg_people']) if r['avg_people'] else '—'}, "
            f"{_num_text(r['person_hours'])} чел·ч, "
            + ("шт/чел·ч нет данных" if r["pcs_per_ph"] is None else f"{_num_text(r['pcs_per_ph'])} шт/чел·ч")
            + (f" ({change_text(r['pcs_per_ph_change'])} к прошлому периоду)" if r["pcs_per_ph_change"] is not None else "") + "."
            for r in norms] + [NORM_NOTE]))
    if summary["labor_operations"]:
        blocks.append(("Человеко-часы по операциям", [f"{i + 1}. {x['label']} — {_num_text(x['value'])} чел·ч"
                                                      for i, x in enumerate(summary["labor_operations"][:6])]))
    if summary["labor_products"]:
        blocks.append(("Человеко-часы по продукции", [f"{i + 1}. {x['label']} — {_num_text(x['value'])} чел·ч"
                                                      for i, x in enumerate(summary["labor_products"][:6])]))
    emp = summary["employees"][:6]
    if emp:
        blocks.append(("Сотрудники (отметки, не личная выработка)", [
            f"{i + 1}. {e['employee']} — начал {e['started']}, завершил {e['closed']}"
            + (f", отменил {e['cancelled']}" if e["cancelled"] else "") for i, e in enumerate(emp)]))
    reasons = summary["pause_reasons"]
    if reasons:
        total = sum(r["value"] for r in reasons) or 1
        blocks.append(("Остановки по причинам", [
            f"{i + 1}. {r['label']} — {r['count']} {plural(r['count'], ('раз', 'раза', 'раз'))}, {fmt_min(r['value'])} "
            f"({_num_text(round(r['value'] / total * 100, 1))}%)" for i, r in enumerate(reasons[:6])]))
    if summary["open_ops"]:
        blocks.append(("Операции в работе", [f"Открыто операций: {len(summary['open_ops'])}."]))
    issues = {key: v for key, v in (summary.get("issues") or {}).items() if v}
    if issues:
        names = {"duplicate_ops": "дубли записей (учтены один раз)", "invalid_duration": "операции с некорректным временем",
                 "closed_without_pcs": "завершены без штук", "closed_without_people": "завершены без числа людей"}
        blocks.append(("Проверка данных (вся выборка)", [f"{names.get(key, key)}: {v}" for key, v in issues.items()]))
    if filters:
        applied = [f"{name}: {value}" for name, value in filters if value]
        if applied:
            blocks.append(("Применённые фильтры", applied))
    return blocks


def build_meta(snapshot, exclude_dates=()):
    excluded = set(exclude_dates or ())
    ops, pauses = snapshot["operations"], snapshot["pauses"]
    days = {_day(o["start_at"]) for o in ops} | {_day(o["end_at"]) for o in ops if o["end_at"]}
    days |= {_day(p["start_at"]) for p in pauses}
    days = sorted(d for d in days if d and d not in excluded)
    employees = ({o["user_name"] for o in ops} | {o["closed_by_name"] for o in ops if o["closed_by_name"]}
                 | {p["user_name"] for p in pauses})
    return {
        "products": sorted({o["product"] for o in ops}, key=str.lower),
        "operations": sorted({o["operation"] for o in ops}, key=str.lower),
        "employees": sorted(e for e in employees if e and e != "—"),
        "statuses": [{"code": k, "name": v} for k, v in STATUS_NAMES.items()],
        "equipment": [],
        "date_min": days[0] if days else "",
        "date_max": days[-1] if days else "",
        "production_count": len(ops),
        "pause_count": len(pauses),
        "excluded_dates": sorted(excluded),
    }
