"""Диаграмма Ганта и общий тайминг по всем цехам. Чистые функции над снимками источников.

Одна полоса = одна операция: продукт · операция · люди · выработка · оборудование.
Строки сгруппированы: цех → продукт (сворачивается) → его операции.
Источники: формовка (бот ФК), БХМ (тесто), расстойка (расстойка и отпекание), КЦ (бот + разобранный
чат Telegram до запуска бота). Фасовки пока нет ни в одном боте — этап заложен, данных нет.

Правила:
- время — по местному поясу; операции через полночь не режутся по датам;
- окно — смена: «сутки 08–08», «день 08–20», «ночь 20–08»; в окно попадает операция,
  которая хотя бы минутой с ним пересекается;
- полоса без конца: у бота — операция ещё идёт (до «сейчас»), у чата — конец не отмечен
  (рисуется только отметка начала, длительность не считается);
- общий тайминг связывает цеха по ЕДИНОМУ продукту (справочник соответствий), а не по
  номеру партии: у ботов нет общего идентификатора партии;
- выработку разных единиц (шт, кг, пласты…) не складываем, штуки разных операций — тоже:
  выработка на человека считается по строке операции, а у продукта — только если операция одна;
- «людей» у продукта — максимум одновременно занятых (имён в чате нет, сумма по операциям
  посчитала бы одного человека несколько раз);
- среднее по продукту — по рабочим суткам 08–08: каждая операция от начала работы с продуктом
  в эти сутки, затем усреднение по дням.
"""
import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from aggregate import duration_min, parse_hhmm
from product_map import SECTIONS, norm
from sources import TZ, _as_local

HISTORY_FILE = os.getenv("KC_CHAT_HISTORY_FILE", str(Path(__file__).parent / "kc_chat_history.json"))
STAGES = [
    {"key": "bhm", "title": "Тестозаготовочный цех"},
    {"key": "fk", "title": "Формовочный цех"},
    {"key": "proofing", "title": "Печной цех · расстойка"},
    {"key": "baking", "title": "Печной цех · отпекание"},
    {"key": "kc", "title": "Кондитерский цех"},
    {"key": "pack", "title": "Фасовка"},
]
STAGE_INDEX = {s["key"]: i for i, s in enumerate(STAGES)}
WINDOWS = {"full": ("Сутки 08:00–08:00", 8, 24), "day": ("День 08:00–20:00", 8, 12),
           "night": ("Ночь 20:00–08:00", 20, 12), "calendar": ("Календарные сутки 00–24", 0, 24)}
RATE_UNITS = {"шт", "кг"}
# Единицы продукции для «выработки продукта», по приоритету. Замес, форма, кольцо — полуфабрикат
# или подготовка, а не продукция: в выработку продукта не идут.
OUTPUT_UNITS = ("шт", "пласт", "лист", "кг")
CHAT_NOTE = ("Данные кондитерского цеха неточные: до запуска бота они вручную разобраны из переписки в Telegram. "
             "Время — по сообщениям (часто это время отправки, а не начала или конца), людей и количество "
             "писали не всегда. Годятся для общей картины, не для норм.")


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
            days.add(_work_day(b["start"]))
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


class Namer:
    """Единые названия продуктов и операций: справочник, затем автоматическое сведение
    написаний (регистр, «ё», пробелы, знаки) к самому частому варианту. Результаты кешируются:
    на запрос — тысячи операций с повторяющимися названиями."""

    def __init__(self, bars_by_section, history=None, mapping=None, ops_map=None):
        self._maps = {"p": self._index(mapping), "o": self._index(ops_map)}
        self._memo = {}
        products, ops = defaultdict(Counter), defaultdict(Counter)
        for sec, bars in bars_by_section.items():
            for b in bars:
                u = self._unify("p", sec, b["product"])
                products[norm(u)][u] += 1
                o = self._unify("o", sec, b["operation"])
                ops[(sec, norm(o))][o] += 1
        for h in (history or {}).get("handoffs", []):
            u = self._unify("p", "kc", h["product"])
            products[norm(u)][u] += 1
        pick = lambda c: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]  # noqa: E731
        self._products = {k: pick(c) for k, c in products.items()}
        self._ops = {k: pick(c) for k, c in ops.items()}

    @staticmethod
    def _index(mapping):
        """{section: (точные, по norm)} — то же, что product_map.unify, без перебора на каждый вызов."""
        return {sec: (dict(items), {norm(k): v for k, v in items.items()}) for sec, items in (mapping or {}).items()}

    def _unify(self, kind, section, name):
        key = (kind, section, name)
        if key not in self._memo:
            exact, by_norm = self._maps[kind].get(section, ({}, {}))
            self._memo[key] = exact.get(name) or by_norm.get(norm(name)) or name
        return self._memo[key]

    def product(self, section, name):
        u = self._unify("p", section, name)
        return self._products.get(norm(u), u)

    def operation(self, section, name):
        o = self._unify("o", section, name)
        return self._ops.get((section, norm(o)), o)

    def apply(self, b):
        return dict(b, unified=self.product(b["section"], b["product"]), operation=self.operation(b["section"], b["operation"]),
                    operation_raw=b["operation"])


def _work_day(dt):
    """Рабочие сутки 08–08: ночь до 08:00 относится к прошлой дате."""
    return (dt.astimezone(TZ) - timedelta(hours=8)).date().isoformat()


def _timed(b):
    """Операция с честной длительностью: конец отмечен и время не некорректное."""
    return b["end"] is not None and b["flag"] != "invalid" and b["end"] > b["start"]


def _rate_stats(items):
    """Выработка группы операций: Σшт/Σчел·ч (где люди известны) и Σшт/Σч. Единицы не смешиваются:
    берётся основная единица группы."""
    timed = [b for b in items if _timed(b) and b["qty"] and b["unit"] in RATE_UNITS]
    if not timed:
        return {"rate": None, "rate_per_person": None, "rate_unit": ""}
    unit = Counter(b["unit"] for b in timed).most_common(1)[0][0]
    timed = [b for b in timed if b["unit"] == unit]
    hours = sum((b["end"] - b["start"]).total_seconds() / 3600 for b in timed)
    with_people = [b for b in timed if b["people"]]
    ph = sum((b["end"] - b["start"]).total_seconds() / 3600 * b["people"] for b in with_people)
    return {"rate": round(sum(b["qty"] for b in timed) / hours, 1) if hours else None,
            "rate_per_person": round(sum(b["qty"] for b in with_people) / ph, 1) if ph else None,
            "rate_unit": unit}


def _person_hours(items):
    v = sum((b["end"] - b["start"]).total_seconds() / 3600 * b["people"] for b in items if _timed(b) and b["people"])
    return round(v, 2) if v else None


def _qty_groups(items):
    qty = defaultdict(float)
    for b in items:
        if b["qty"] and b["end"]:
            qty[b["unit"] or "ед."] += b["qty"]
    return qty


def _peak_people(items, now):
    """Максимум людей, одновременно занятых продуктом (по операциям с известным числом людей)."""
    events = []
    for b in items:
        e = bar_end(b, now)
        if b["people"] and e and e > b["start"]:
            events += [(b["start"], 1, b["people"]), (e, 0, -b["people"])]
    cur = best = 0
    for _, _, d in sorted(events):  # конец раньше начала в ту же минуту — эстафета, не параллель
        cur += d
        best = max(best, cur)
    return best or None


def _row_stats(items, now, clip=None):
    """clip=(start, end): «занято» считается внутри окна, а не за все дни идущей операции."""
    people = [b["people"] for b in items if b["people"]]
    intervals = [(b["start"], bar_end(b, now)) for b in items if bar_end(b, now)]
    if clip:
        intervals = [(max(s, clip[0]), min(e, clip[1])) for s, e in intervals if min(e, clip[1]) > max(s, clip[0])]
    return {"people": max(people) if people else None, "person_hours": _person_hours(items),
            "qty": _qty_text(_qty_groups(items)), "busy_min": round(_union_minutes(intervals)) if intervals else None,
            "equipment": ", ".join(sorted({b["equipment"] for b in items if b["equipment"]})), **_rate_stats(items)}


def _day_output(items):
    """Выработка продукта за рабочие сутки: последний этап с количеством (ближе всего к готовой
    продукции; единица — по приоритету OUTPUT_UNITS), внутри этапа — максимум по операциям. Операции НЕ складываются:
    одни и те же изделия проходят несколько операций. Повторные запуски одной операции —
    разные партии, они складываются."""
    done = [b for b in items if _timed(b) and b["qty"] and b["unit"] in OUTPUT_UNITS]
    if not done:
        return None
    unit = min({b["unit"] for b in done}, key=OUTPUT_UNITS.index)
    done = [b for b in done if b["unit"] == unit]
    stage = max(done, key=lambda b: STAGE_INDEX[b["stage"]])["stage"]
    per_op, last_end = defaultdict(float), {}
    for b in done:
        if b["stage"] == stage:
            per_op[b["operation"]] += b["qty"]
            last_end[b["operation"]] = max(last_end.get(b["operation"], b["end"]), b["end"])
    # равное количество — берём операцию, закончившуюся позже: она ближе к готовой продукции
    op, qty = max(per_op.items(), key=lambda kv: (kv[1], last_end[kv[0]]))
    return {"qty": qty, "unit": unit, "stage": stage, "operation": op}


def _product_rate(items, clip):
    """Выработка продукта в строке диаграммы (несколько операций): как в «Среднем по продукту» —
    количество последнего этапа, самая массовая операция (операции не складываются).
    На человека — / чел·ч всех операций продукта; людей нет — / время работы с продуктом в окне.
    Время — только по ЗАКРЫТЫМ операциям: незакрытая операция «идёт» до «сейчас» и раздула бы
    время в разы (забытая с утра «Сборка теста» — 10 часов вместо минут)."""
    out = _day_output(items)
    if not out:
        return {"rate": None, "rate_per_person": None, "rate_unit": "", "qty": "", "output_from": ""}
    closed = [(max(b["start"], clip[0]), min(b["end"], clip[1])) for b in items if _timed(b)]
    work_min = _union_minutes([(s, e) for s, e in closed if e > s])
    ph = _person_hours(items)
    return {"rate": round(out["qty"] / (work_min / 60), 1) if work_min else None,
            "rate_per_person": round(out["qty"] / ph, 1) if ph else None,
            "rate_unit": out["unit"], "qty": _qty_text({out["unit"]: out["qty"]}), "output_qty": out["qty"],
            "work_min": round(work_min), "person_hours_all": ph,
            "output_from": f"{STAGES[STAGE_INDEX[out['stage']]]['title']} · {out['operation']}"}


def averages(bars_by_section, namer, day_from, day_to, sections=None, product="all", equipment="all", now=None):
    """Средний день продукта за период: когда от начала работы с продуктом идёт каждая операция
    и этап и сколько длится. Каждые рабочие сутки продукта — один «прогон»."""
    now = now or datetime.now(TZ)
    sections = [s for s in (sections or list(SECTIONS)) if s in SECTIONS]
    runs = defaultdict(list)
    for sec in sections:
        for b in bars_by_section.get(sec, []):
            b = namer.apply(b)
            if product not in ("all", "") and b["unified"] != product:
                continue
            if equipment not in ("all", "") and b["equipment"] != equipment:
                continue
            day = _work_day(b["start"])
            if day_from <= day <= day_to:
                runs[(b["unified"], day)].append(b)
    by_product = defaultdict(dict)
    for (prod, day), items in runs.items():
        by_product[prod][day] = items

    def avg(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 1) if values else None

    out = []
    for prod, days in by_product.items():
        leads, phs, op_runs, stage_runs, outputs = [], [], defaultdict(list), defaultdict(list), []
        for day, items in days.items():
            day_out = _day_output(items)
            if day_out:
                outputs.append(dict(day_out, person_hours=_person_hours(items)))
            t0 = min(b["start"] for b in items)
            timed = [b for b in items if _timed(b)]
            if timed:
                leads.append((max(b["end"] for b in timed) - t0).total_seconds() / 60)
            phs.append(_person_hours(items))
            groups = defaultdict(list)
            for b in items:
                groups[(b["stage"], b["section"], b["operation"])].append(b)
            for key, ops in groups.items():
                t = [b for b in ops if _timed(b)]
                op_runs[key].append({
                    "offset": (min(b["start"] for b in ops) - t0).total_seconds() / 60,
                    "span": (max(b["end"] for b in t) - min(b["start"] for b in t)).total_seconds() / 60 if t else None,
                    "busy": _union_minutes([(b["start"], b["end"]) for b in t]) if t else None,
                    "people": max((b["people"] for b in ops if b["people"]), default=None),
                    "items": ops})
            stage_items = defaultdict(list)
            for b in items:
                stage_items[b["stage"]].append(b)
            for st, ops in stage_items.items():
                t = [b for b in ops if _timed(b)]
                stage_runs[st].append({
                    "offset": (min(b["start"] for b in ops) - t0).total_seconds() / 60,
                    "span": (max(b["end"] for b in t) - min(b["start"] for b in t)).total_seconds() / 60 if t else None,
                    "busy": _union_minutes([(b["start"], b["end"]) for b in t]) if t else None})
        ops_out = []
        for (stage, sec, op), rs in op_runs.items():
            items = [b for r in rs for b in r["items"]]
            qty = _qty_groups(items)
            rates = _rate_stats(items)
            # основная единица операции — для выгрузки числом (Excel), а не текстом «1 460 шт»
            unit = rates["rate_unit"] or (max(qty, key=qty.get) if qty else "")
            ops_out.append({
                "qty_unit": unit if unit in qty else "",
                "qty_day_value": round(qty[unit] / len(rs), 1) if unit in qty else None,
                # все единицы операции (у разлива бисквита — листы, пласты и замесы): в выгрузке ничего не теряется
                "qty_by_unit": {u: round(q / len(rs), 1) for u, q in sorted(qty.items(), key=lambda kv: (kv[0] != unit, kv[0]))},
                "stage": stage, "stage_title": STAGES[STAGE_INDEX[stage]]["title"], "section": sec,
                "section_title": SECTIONS[sec], "operation": op, "days": len(rs),
                "offset_min": avg(r["offset"] for r in rs), "span_min": avg(r["span"] for r in rs),
                "busy_min": avg(r["busy"] for r in rs), "people_avg": avg(r["people"] for r in rs),
                "qty_day": _qty_text({u: q / len(rs) for u, q in qty.items()}),
                "no_end": sum(1 for b in items if not _timed(b)),
                "from_chat": sum(1 for b in items if b["source"] == "chat"),
                "equipment": ", ".join(sorted({b["equipment"] for b in items if b["equipment"]})),
                **rates})
        ops_out.sort(key=lambda o: (STAGE_INDEX[o["stage"]], o["offset_min"] if o["offset_min"] is not None else 0, o["operation"].lower()))
        stages_out = [{"key": st["key"], "title": st["title"], "days": len(stage_runs[st["key"]]),
                       "offset_min": avg(r["offset"] for r in stage_runs[st["key"]]),
                       "span_min": avg(r["span"] for r in stage_runs[st["key"]]),
                       "busy_min": avg(r["busy"] for r in stage_runs[st["key"]])}
                      for st in STAGES if stage_runs.get(st["key"])]
        output = None
        if outputs:  # дни с другой единицей не смешиваем — берём основную
            unit = Counter(o["unit"] for o in outputs).most_common(1)[0][0]
            same = [o for o in outputs if o["unit"] == unit]
            with_ph = [o for o in same if o["person_hours"]]
            last = Counter((o["stage"], o["operation"]) for o in same).most_common(1)[0][0]
            output = {"qty_day": round(sum(o["qty"] for o in same) / len(same), 1), "unit": unit, "days": len(same),
                      "stage": last[0], "stage_title": STAGES[STAGE_INDEX[last[0]]]["title"], "operation": last[1],
                      "rate_per_person": round(sum(o["qty"] for o in with_ph) / sum(o["person_hours"] for o in with_ph), 1)
                      if with_ph else None,
                      "person_days": len(with_ph)}
        out.append({"product": prod, "days": len(days), "day_list": sorted(days), "output": output,
                    "lead_min": avg(leads), "lead_min_min": round(min(leads)) if leads else None,
                    "lead_min_max": round(max(leads)) if leads else None,
                    "person_hours_day": avg(phs), "stages": stages_out, "ops": ops_out,
                    "from_chat": sum(o["from_chat"] for o in ops_out),
                    "sections": sorted({SECTIONS[o["section"]] for o in ops_out}, key=list(SECTIONS.values()).index)})
    out.sort(key=lambda r: (-r["days"], -len(r["stages"]), r["product"].lower()))
    return {"from": day_from, "to": day_to, "products": out,
            "days": len({d for days in by_product.values() for d in days})}


def default_avg_range(days, span=7):
    if not days:
        return "", ""
    last = date.fromisoformat(days[-1])
    first = max(date.fromisoformat(days[0]), last - timedelta(days=span - 1))
    return first.isoformat(), last.isoformat()


def build(bars_by_section, history, mapping, day, mode="full", sections=None, product="all", equipment="all",
          now=None, section_errors=None, ops_map=None, avg_from=None, avg_to=None):
    now = now or datetime.now(TZ)
    sections = [s for s in (sections or list(SECTIONS)) if s in SECTIONS]
    start, end = window_bounds(day, mode)
    namer = Namer(bars_by_section, history, mapping, ops_map)
    bars = []
    for sec in sections:
        for b in bars_by_section.get(sec, []):
            b = namer.apply(b)
            if product not in ("all", "") and b["unified"] != product:
                continue
            if equipment not in ("all", "") and b["equipment"] != equipment:
                continue
            if _overlaps(b, start, end, now):
                bars.append(b)
    handoffs = [dict(h, unified=namer.product("kc", h["product"])) for h in handoff_events(history)] if "kc" in sections else []
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
                         "lanes": lanes, "runs": len(items), **_row_stats(items, now, (start, end)),
                         "bars": [dict(view(b, now), lane=b["lane"]) for b in sorted(items, key=lambda x: x["start"])]})
    # Строка продукта (сворачиваемая): итог по его операциям в цехе.
    products = []
    for (sec, prod), items in sorted(_group(bars, lambda b: (b["section"], b["unified"])).items(),
                                     key=lambda kv: (order[kv[0][0]], kv[0][1].lower())):
        ops = sorted({b["operation"] for b in items})
        stats = _row_stats(items, now, (start, end))
        stats["output_from"] = ops[0] if len(ops) == 1 else ""
        if len(ops) > 1:
            stats.update(_product_rate(items, (start, end)))
        known = [bar_end(b, now) for b in items if bar_end(b, now)]
        products.append({"section": sec, "section_title": SECTIONS[sec], "product": prod, "ops": len(ops),
                         "runs": len(items), **stats, "people": _peak_people(items, now),
                         "running": sum(1 for b in items if b["running"]),
                         "people_ops": sum(r["people"] or 0 for r in out_rows if r["section"] == sec and r["product"] == prod) or None,
                         "start": _iso(min(b["start"] for b in items)), "end": _iso(max(known)) if known else ""})
    closed = [b for b in bars if b["end"]]
    totals = {
        "ops": len(bars), "closed": len(closed), "open": len(bars) - len(closed),
        "people_known": sum(1 for b in bars if b["people"]),
        "person_hours": round(sum((b["end"] - b["start"]).total_seconds() / 3600 * b["people"]
                                  for b in closed if b["people"] and b["flag"] != "invalid"), 1),
        "by_section": {SECTIONS[s]: sum(1 for b in bars if b["section"] == s) for s in sections},
        "from_chat": sum(1 for b in bars if b["source"] == "chat"),
        "check": sum(1 for b in bars if b["flag"] == "check"),
        "products": len({b["unified"] for b in bars}),
    }
    in_window = [h for h in handoffs if start <= h["at"] < end]
    days = available_days(bars_by_section)
    if not avg_from or not avg_to:
        avg_from, avg_to = default_avg_range(days)
    if avg_from > avg_to:
        avg_from, avg_to = avg_to, avg_from
    return {
        "kind": "gantt",
        "day": day, "window": mode, "window_title": WINDOWS.get(mode, WINDOWS["full"])[0],
        "start": _iso(start), "end": _iso(end), "now": _iso(now),
        "sections": sections, "section_errors": section_errors or {},
        "rows": out_rows, "products": products, "totals": totals,
        "timing": timing_block(bars, handoffs, start, end, now),
        "averages": averages(bars_by_section, namer, avg_from, avg_to, sections, product, equipment, now),
        "handoffs": [{"product": h["unified"], "time": h["at"].astimezone(TZ).strftime("%d.%m %H:%M"), "qty": h["qty"],
                      "unit": h["unit"], "exact": h["exact"]} for h in in_window],
        "stages": STAGES,
        "history_source": (history or {}).get("source", ""),
        "chat_note": CHAT_NOTE if (history or {}).get("operations") and "kc" in sections else "",
    }


def _group(items, key):
    out = defaultdict(list)
    for x in items:
        out[key(x)].append(x)
    return out


def known_operations(bars_by_section):
    """{section: {operation: [products]}} — для листа «Операции» шаблона справочника."""
    out = defaultdict(lambda: defaultdict(set))
    for sec, bars in bars_by_section.items():
        for b in bars:
            out[sec][b["operation"]].add(b["product"])
    return {sec: {op: sorted(ps) for op, ps in ops.items()} for sec, ops in out.items()}


def meta(bars_by_section, history, mapping, section_errors=None, ops_map=None):
    days = available_days(bars_by_section)
    namer = Namer(bars_by_section, history, mapping, ops_map)
    unified = sorted({namer.product(sec, b["product"]) for sec, bars in bars_by_section.items() for b in bars}, key=str.lower)
    equipment = sorted({b["equipment"] for bars in bars_by_section.values() for b in bars if b["equipment"]}, key=str.lower)
    avg_from, avg_to = default_avg_range(days)
    return {"days": days, "date_min": days[0] if days else "", "date_max": days[-1] if days else "",
            "avg_from": avg_from, "avg_to": avg_to,
            "products": unified, "equipment": equipment, "windows": {k: v[0] for k, v in WINDOWS.items()},
            "sections_list": [{"key": k, "title": v} for k, v in SECTIONS.items()],
            "section_errors": section_errors or {}, "history_source": (history or {}).get("source", ""),
            "history_ops": len((history or {}).get("operations", [])),
            "chat_note": CHAT_NOTE if (history or {}).get("operations") else ""}
