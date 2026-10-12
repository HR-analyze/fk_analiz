"""Диаграмма Ганта и общий тайминг по всем цехам. Чистые функции над снимками источников.

Одна полоса = одна операция: продукт · операция · люди · выработка · оборудование.
Источники: ФК (формовка), БХМ (тесто), расстойка (расстойка и отпекание), КЦ (бот + разобранный
чат Telegram до запуска бота). Фасовки пока нет ни в одном боте — этап заложен, данных нет.

Правила:
- время — по местному поясу; операции через полночь не режутся по датам;
- окно — смена: «сутки 08–08», «день 08–20», «ночь 20–08»; в окно попадает операция,
  которая хотя бы минутой с ним пересекается;
- полоса без конца: у бота — операция ещё идёт (до «сейчас»), у чата — конец не отмечен
  (рисуется только отметка начала, длительность не считается);
- общий тайминг связывает цеха по ЕДИНОМУ продукту (справочник соответствий), а не по
  номеру партии: у ботов нет общего идентификатора партии;
- выработку разных единиц (шт, кг, пласты…) не складываем.
"""
import json
import os
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from aggregate import duration_min, parse_hhmm
from product_map import SECTIONS, unify
from sources import TZ, _as_local

HISTORY_FILE = os.getenv("KC_CHAT_HISTORY_FILE", str(Path(__file__).parent / "kc_chat_history.json"))
STAGES = [
    {"key": "bhm", "title": "БХМ · тесто"},
    {"key": "fk", "title": "ФК · формовка"},
    {"key": "proofing", "title": "Расстойка"},
    {"key": "baking", "title": "Отпекание"},
    {"key": "kc", "title": "КЦ · кондитерка"},
    {"key": "pack", "title": "Фасовка"},
]
STAGE_INDEX = {s["key"]: i for i, s in enumerate(STAGES)}
WINDOWS = {"full": ("Сутки 08:00–08:00", 8, 24), "day": ("День 08:00–20:00", 8, 12),
           "night": ("Ночь 20:00–08:00", 20, 12), "calendar": ("Календарные сутки 00–24", 0, 24)}
RATE_UNITS = {"шт", "кг"}


def load_history(path=None):
    p = Path(path or HISTORY_FILE)
    if not p.exists():
        return {"operations": [], "handoffs": [], "source": ""}
    return json.loads(p.read_text(encoding="utf-8"))


def _iso(dt):
    return dt.astimezone(TZ).isoformat() if dt else ""


def _clock(created, start_time, end_time):
    """ФК/БХМ: дата записи + HH:MM. Запись могла появиться и в начале, и в конце операции:
    если начало получилось позже момента записи больше чем на час — это прошлые сутки."""
    created = _as_local(created)
    s = parse_hhmm(start_time)
    if not created or s is None:
        return None, None
    start = datetime.combine(created.date(), dtime(s // 60, s % 60), TZ)
    if start > created + timedelta(hours=1):
        start -= timedelta(days=1)
    end = None
    if parse_hhmm(end_time) is not None:
        end = start + timedelta(minutes=duration_min(start_time, end_time))
    return start, end


def _bar(section, stage, product, operation, start, end, *, equipment="", people=None, qty=None, unit="",
         user="", closed_by="", status="closed", flag="ok", approx=False, source="bot", note="", running=False, rid=""):
    return {"id": rid, "section": section, "section_title": SECTIONS.get(section, section), "stage": stage,
            "product": product or "Без продукта", "operation": operation or "Не указана", "equipment": equipment or "",
            "start": start, "end": end, "people": people, "qty": qty, "unit": unit, "user": user or "",
            "closed_by": closed_by or "", "status": status, "flag": flag, "approx": approx, "source": source,
            "note": note, "running": running}


def fk_bars(snapshot):
    out = []
    for r in (snapshot or {}).get("production", []):
        start, end = _clock(r.get("created_at"), r.get("start_time"), r.get("end_time"))
        if not start:
            continue
        open_ = r.get("status") != "closed" or end is None
        out.append(_bar("fk", "fk", r["product"], "Формовка", start, None if open_ else end, equipment=r["equipment"],
                        qty=r.get("qty") or None, unit="шт", user=r.get("user_name"), status="open" if open_ else "closed",
                        running=open_, rid=f"fk-{r.get('id')}"))
    return out


def bhm_bars(snapshot):
    out = []
    for r in (snapshot or {}).get("production", []):
        start, end = _clock(r.get("created_at"), r.get("start_time"), r.get("end_time"))
        if not start:
            continue
        open_ = r.get("status") == "open" or end is None
        equipment = r["equipment"] if r["equipment"] != "Без оборудования" else ""
        out.append(_bar("bhm", "bhm", r["product"], r.get("operation") or "Операция", start, None if open_ else end,
                        equipment=equipment, qty=r.get("qty") or None, unit="кг", user=r.get("user_name"),
                        status="open" if open_ else "closed", running=open_, rid=f"bhm-{r.get('id')}"))
    return out


def proofing_bars(snapshot):
    out = []
    for b in (snapshot or {}).get("batches", []):
        if b["status"] == "cancelled":
            continue
        for code in ("proofing", "baking"):
            s = b.get(code)
            if not s or not s.get("start_at"):
                continue
            closed = s["status"] == "closed"
            qty = (s.get("pieces_start") if code == "proofing" else (s.get("pieces_end") if closed else s.get("pieces_start")))
            out.append(_bar("proofing", code, b["product"], s["name"], s["start_at"], s["end_at"] if closed else None,
                            equipment=s["equipment"], qty=qty, unit="шт", user=s["started_by"], closed_by=s.get("closed_by"),
                            status="closed" if closed else "open", flag="ok" if (s["valid"] or not closed) else "invalid",
                            running=not closed and b["status"] == "active", rid=f"pr-{b['id']}-{code}"))
    return out


def kc_bars(snapshot, history=None):
    out = []
    for o in (snapshot or {}).get("operations", []):
        if o["status"] == "cancelled":
            continue
        out.append(_bar("kc", "kc", o["product"], o["operation"], o["start_at"], o["end_at"], people=o["people"],
                        qty=o["pcs"], unit="шт", user=o["user_name"], closed_by=o["closed_by_name"], status=o["status"],
                        flag="ok" if (o["valid"] or o["status"] != "closed") else "invalid",
                        running=o["status"] == "open", rid=f"kc-{o['id']}"))
    for o in (history or {}).get("operations", []):
        start = _as_local(o.get("start_at"))
        if not start:
            continue  # без начала на шкалу не поставить
        out.append(_bar("kc", "kc", o["product"], o["operation"], start, _as_local(o.get("end_at")), people=o.get("people"),
                        qty=o.get("qty"), unit=o.get("unit") or "", status=o["status"], flag=o.get("flag", "ok"),
                        approx=not o.get("start_exact", True) or bool(o.get("end_at") and not o.get("end_exact", True)),
                        source="chat",
                        note=o.get("note", ""), rid=o["id"]))
    return out


def handoff_events(history):
    out = []
    for h in (history or {}).get("handoffs", []):
        at = _as_local(h.get("at"))
        if at:
            out.append({"product": h["product"], "at": at, "qty": h.get("qty"), "unit": h.get("unit", "шт"),
                        "exact": h.get("exact", True), "source": "chat"})
    return out


def window_bounds(day, mode):
    _, hour, length = WINDOWS.get(mode, WINDOWS["full"])
    start = datetime.combine(date.fromisoformat(day), dtime(hour), TZ)
    return start, start + timedelta(hours=length)


def bar_end(b, now):
    """Конец для шкалы: бот — до «сейчас», если идёт; у чата без конца — нет полосы."""
    if b["end"]:
        return b["end"]
    if b["running"]:
        return max(b["start"], now)
    return None


def _overlaps(b, start, end, now):
    e = bar_end(b, now)
    if e is None:
        return start <= b["start"] < end
    return b["start"] < end and e > start


def _lanes(bars, now):
    """Параллельные операции одной строки — на разные дорожки, чтобы не перекрывались."""
    ends = []
    for b in sorted(bars, key=lambda x: x["start"]):
        e = bar_end(b, now) or (b["start"] + timedelta(minutes=10))
        for i, last in enumerate(ends):
            if b["start"] >= last:
                ends[i] = e
                b["lane"] = i
                break
        else:
            ends.append(e)
            b["lane"] = len(ends) - 1
    return len(ends) or 1


def _rate(b, now):
    e = b["end"]
    if not e or b["flag"] == "invalid" or not b["qty"] or b["unit"] not in RATE_UNITS:
        return None, None
    hours = (e - b["start"]).total_seconds() / 3600
    if hours <= 0:
        return None, None
    per_person = round(b["qty"] / (hours * b["people"]), 1) if b["people"] else None
    return round(b["qty"] / hours, 1), per_person


def view(b, now):
    end = bar_end(b, now)
    rate, per_person = _rate(b, now)
    minutes = (b["end"] - b["start"]).total_seconds() / 60 if b["end"] else None
    return {**{k: v for k, v in b.items() if k not in ("start", "end")},
            "start": _iso(b["start"]), "end": _iso(b["end"]), "draw_end": _iso(end),
            "start_time": b["start"].astimezone(TZ).strftime("%H:%M"),
            "end_time": b["end"].astimezone(TZ).strftime("%H:%M") if b["end"] else "",
            "duration_min": round(minutes, 1) if minutes is not None else None,
            "person_hours": round(minutes / 60 * b["people"], 2) if minutes and b["people"] else None,
            "rate": rate, "rate_per_person": per_person}


def collect_bars(snaps, history):
    """{section: [bars]} из доступных снимков; недоступный цех — пустой список."""
    return {"bhm": bhm_bars(snaps.get("bhm")), "fk": fk_bars(snaps.get("fk")),
            "proofing": proofing_bars(snaps.get("proofing")), "kc": kc_bars(snaps.get("kc"), history)}


def known_products(bars_by_section):
    return {sec: sorted({b["product"] for b in bars}) for sec, bars in bars_by_section.items()}


def available_days(bars_by_section, handoffs=()):
    days = set()
    for bars in bars_by_section.values():
        for b in bars:
            local = b["start"].astimezone(TZ)
            # рабочие сутки 08–08: ночь до 08:00 относится к прошлой дате
            days.add((local - timedelta(hours=8)).date().isoformat())
    return sorted(days)


def _union_minutes(intervals):
    total, cur = 0.0, None
    for s, e in sorted(intervals):
        if cur and s <= cur[1]:
            cur[1] = max(cur[1], e)
        else:
            if cur:
                total += (cur[1] - cur[0]).total_seconds() / 60
            cur = [s, e]
    if cur:
        total += (cur[1] - cur[0]).total_seconds() / 60
    return total


def _qty_text(groups):
    return " · ".join(f"{q:,.0f} {u}".replace(",", " ") for u, q in groups.items() if q) or "—"


def timing_block(bars, handoffs, start, end, now):
    """Общий тайминг по единому продукту: этапы БХМ → ФК → расстойка → отпекание → КЦ → фасовка."""
    by_product = defaultdict(lambda: defaultdict(list))
    for b in bars:
        by_product[b["unified"]][b["stage"]].append(b)
    pack = defaultdict(list)
    for h in handoffs:
        if start <= h["at"] < end:
            pack[h["unified"]].append(h)
    rows = []
    for product in sorted(set(by_product) | set(pack), key=str.lower):
        stages, prev_end, first, last = [], None, None, None
        for st in STAGES:
            items = by_product[product].get(st["key"], [])
            if st["key"] == "pack":
                hs = pack.get(product, [])
                stages.append({"key": "pack", "title": st["title"], "present": False, "source": "нет источника",
                               "handoffs": [{"at": _iso(h["at"]), "time": h["at"].astimezone(TZ).strftime("%H:%M"),
                                             "qty": h["qty"], "unit": h["unit"]} for h in hs]})
                if hs:
                    last = max([last] + [h["at"] for h in hs]) if last else max(h["at"] for h in hs)
                continue
            if not items:
                stages.append({"key": st["key"], "title": st["title"], "present": False})
                continue
            s = min(b["start"] for b in items)
            ends = [bar_end(b, now) for b in items]
            known = [e for e in ends if e]
            e = max(known) if known else None
            intervals = [(b["start"], x) for b, x in zip(items, ends) if x]
            qty = defaultdict(float)
            for b in items:
                if b["qty"] and b["end"]:
                    qty[b["unit"] or "ед."] += b["qty"]
            people = [b["people"] for b in items if b["people"]]
            gap = round((s - prev_end).total_seconds() / 60) if prev_end else None
            stages.append({
                "key": st["key"], "title": st["title"], "present": True, "ops": len(items),
                "start": _iso(s), "end": _iso(e), "start_time": s.astimezone(TZ).strftime("%d.%m %H:%M"),
                "end_time": e.astimezone(TZ).strftime("%d.%m %H:%M") if e else "",
                "span_min": round((e - s).total_seconds() / 60) if e else None,
                "busy_min": round(_union_minutes(intervals)), "qty": _qty_text(qty),
                "people_max": max(people) if people else None, "gap_min": gap,
                "open": sum(1 for b in items if not b["end"]),
                # реальные операции этапа: общий интервал от первой до последней — не непрерывная работа
                "bars": [{"start": _iso(b["start"]), "end": _iso(x)} for b, x in sorted(zip(items, ends), key=lambda p: p[0]["start"])],
                "equipment": ", ".join(sorted({b["equipment"] for b in items if b["equipment"]})),
            })
            first = s if first is None else min(first, s)
            if e:
                last = e if last is None else max(last, e)
                prev_end = e
        present = [x for x in stages if x.get("present")]
        rows.append({"product": product, "stages": stages, "stages_count": len(present),
                     "first": _iso(first), "last": _iso(last),
                     "lead_min": round((last - first).total_seconds() / 60) if first and last and last > first else None,
                     "sections": sorted({b["section_title"] for st in by_product[product].values() for b in st})})
    rows.sort(key=lambda r: (-r["stages_count"], r["product"].lower()))
    return rows


def build(bars_by_section, history, mapping, day, mode="full", sections=None, product="all", equipment="all",
          now=None, section_errors=None):
    now = now or datetime.now(TZ)
    sections = [s for s in (sections or list(SECTIONS)) if s in SECTIONS]
    start, end = window_bounds(day, mode)
    bars = []
    for sec in sections:
        for b in bars_by_section.get(sec, []):
            b = dict(b, unified=unify(mapping, sec, b["product"]))
            if product not in ("all", "") and b["unified"] != product:
                continue
            if equipment not in ("all", "") and b["equipment"] != equipment:
                continue
            if _overlaps(b, start, end, now):
                bars.append(b)
    handoffs = [dict(h, unified=unify(mapping, "kc", h["product"])) for h in handoff_events(history)] if "kc" in sections else []
    if product not in ("all", ""):
        handoffs = [h for h in handoffs if h["unified"] == product]

    # Строки: цех → продукт → операция; внутри — дорожки для параллельных операций.
    rows = defaultdict(list)
    for b in bars:
        rows[(b["section"], b["unified"], b["operation"])].append(b)
    order = {k: i for i, k in enumerate(SECTIONS)}
    out_rows = []
    for (sec, prod, op), items in sorted(rows.items(), key=lambda kv: (order[kv[0][0]], kv[0][1].lower(),
                                                                       min(b["start"] for b in kv[1]), kv[0][2].lower())):
        lanes = _lanes(items, now)
        out_rows.append({"section": sec, "section_title": SECTIONS[sec], "product": prod, "operation": op,
                         "lanes": lanes, "equipment": ", ".join(sorted({b["equipment"] for b in items if b["equipment"]})),
                         "bars": [dict(view(b, now), lane=b["lane"]) for b in sorted(items, key=lambda x: x["start"])]})
    closed = [b for b in bars if b["end"]]
    totals = {
        "ops": len(bars), "closed": len(closed), "open": len(bars) - len(closed),
        "people_known": sum(1 for b in bars if b["people"]),
        "person_hours": round(sum((b["end"] - b["start"]).total_seconds() / 3600 * b["people"]
                                  for b in closed if b["people"] and b["flag"] != "invalid"), 1),
        "by_section": {SECTIONS[s]: sum(1 for b in bars if b["section"] == s) for s in sections},
        "from_chat": sum(1 for b in bars if b["source"] == "chat"),
        "check": sum(1 for b in bars if b["flag"] == "check"),
    }
    in_window = [h for h in handoffs if start <= h["at"] < end]
    return {
        "kind": "gantt",
        "day": day, "window": mode, "window_title": WINDOWS.get(mode, WINDOWS["full"])[0],
        "start": _iso(start), "end": _iso(end), "now": _iso(now),
        "sections": sections, "section_errors": section_errors or {},
        "rows": out_rows, "totals": totals,
        "timing": timing_block(bars, handoffs, start, end, now),
        "handoffs": [{"product": h["unified"], "time": h["at"].astimezone(TZ).strftime("%d.%m %H:%M"), "qty": h["qty"],
                      "unit": h["unit"], "exact": h["exact"]} for h in in_window],
        "stages": STAGES,
        "history_source": (history or {}).get("source", ""),
    }


def meta(bars_by_section, history, mapping, section_errors=None):
    days = available_days(bars_by_section)
    unified = sorted({unify(mapping, sec, b["product"]) for sec, bars in bars_by_section.items() for b in bars}, key=str.lower)
    equipment = sorted({b["equipment"] for bars in bars_by_section.values() for b in bars if b["equipment"]}, key=str.lower)
    return {"days": days, "date_min": days[0] if days else "", "date_max": days[-1] if days else "",
            "products": unified, "equipment": equipment, "windows": {k: v[0] for k, v in WINDOWS.items()},
            "sections_list": [{"key": k, "title": v} for k, v in SECTIONS.items()],
            "section_errors": section_errors or {}, "history_source": (history or {}).get("source", ""),
            "history_ops": len((history or {}).get("operations", []))}
