"""Выгрузки раздела «Гант»: Excel (строки диаграммы, операции, общий тайминг, среднее по продукту,
передачи на фасовку, справочник) и PDF с диаграммой. Берут тот же расчёт gantt.build, что и страница."""
import io
from datetime import datetime

from exports import TZ, _register_fonts
from product_map import SECTIONS

SECTION_COLORS = {"bhm": "#EB6834", "fk": "#2A78D6", "proofing": "#1BAF7A", "kc": "#7C5CF5"}
STAGE_COLORS = {"bhm": "#EB6834", "fk": "#2A78D6", "proofing": "#1BAF7A", "baking": "#0F766E", "kc": "#7C5CF5",
                "pack": "#9CA3AF"}
FLAG_NAMES = {"ok": "", "check": "проверить: время по сообщению", "invalid": "некорректное время"}


def _v(value):
    return "" if value is None else value


def _qty(h):
    """«930 шт » или пусто: в переписке количество при сдаче называли не всегда."""
    return "" if h.get("qty") is None else f"{h['qty']:g} {h['unit']} "


def _hm(minutes):
    if minutes is None:
        return ""
    m = int(round(minutes))
    sign = "−" if m < 0 else ""
    m = abs(m)
    return f"{sign}{m} мин" if m < 60 else f"{sign}{m // 60} ч {m % 60:02d} мин"


def bar_rows(summary):
    rows = []
    for r in summary["rows"]:
        for b in r["bars"]:
            rows.append([b["section_title"], r["product"], b["product"] if b["product"] != r["product"] else "",
                         b["operation"], b["start"][:16].replace("T", " "),
                         b["end"][:16].replace("T", " ") if b["end"] else ("идёт" if b["running"] else "конец не отмечен"),
                         _v(b["duration_min"]), _v(b["people"]), _v(b["person_hours"]), _v(b["qty"]), b["unit"],
                         _v(b["rate"]), _v(b["rate_per_person"]), b["equipment"], b["user"], b["closed_by"],
                         "чат Telegram (неточно)" if b["source"] == "chat" else "бот", FLAG_NAMES.get(b["flag"], b["flag"]), b["note"]])
    return rows


BAR_COLUMNS = ["Цех", "Продукт (единый)", "Название в цехе", "Операция", "Начало", "Конец", "Длительность, мин",
               "Людей", "Чел·ч", "Выработка", "Ед.", "В час", "На человека в час", "Оборудование", "Начал",
               "Завершил", "Источник", "Отметка", "Примечание"]


def _n(value):
    """Число по-русски: «1 370», «114,8» (без «.0»)."""
    if value is None:
        return "—"
    text = f"{value:,.1f}".replace(",", " ").replace(".", ",")
    return text[:-2] if text.endswith(",0") else text


def _rate_text(x):
    if x.get("rate_per_person") is not None:
        return f"{_n(x['rate_per_person'])} {x['rate_unit']}/чел·ч"
    if x.get("rate") is not None:
        return f"{_n(x['rate'])} {x['rate_unit']}/ч (людей нет)"
    return ""


ROW_COLUMNS = ["Цех", "Продукт (единый)", "Операция", "Уровень", "Запусков", "Занято, мин", "Оборудование",
               "Людей", "Чел·ч", "Выработка", "Выработка в час на человека"]


def grouped_rows(summary):
    """Как на диаграмме: строка продукта, под ней его операции."""
    out = []
    for p in summary.get("products", []):
        made = (p["qty"] or "") + (f" ({p['output_from']})" if p.get("output_from") and p["ops"] > 1 and p["qty"] else "")
        out.append([p["section_title"], p["product"], f"все операции: {p['ops']}", "продукт", p["runs"], _v(p["busy_min"]),
                    p["equipment"], _v(p["people"]), _v(p["person_hours"]), made, _rate_text(p)])
        for r in summary["rows"]:
            if r["section"] == p["section"] and r["product"] == p["product"]:
                out.append([r["section_title"], r["product"], r["operation"], "операция", r["runs"], _v(r["busy_min"]),
                            r["equipment"], _v(r["people"]), _v(r["person_hours"]), r["qty"] if r["qty"] != "—" else "",
                            _rate_text(r)])
    return out


def _xl_time(minutes):
    """Минуты → длительность Excel (доля суток): видно как ч:мм, а складывать и фильтровать можно как число."""
    return None if minutes is None else round(minutes) / 1440


def _stage_part(stage_title):
    """«Печной цех · расстойка» → «расстойка»; у цехов из одного этапа — пусто."""
    return stage_title.split(" · ", 1)[1] if " · " in stage_title else ""


def _source(item):
    return "переписка кондитерского цеха — неточно" if item.get("from_chat") else "бот"


def _units_text(op):
    """Выработка операции по всем её единицам: «13,8 лист · 12 пласт · 1 замес» (округление как везде — _n)."""
    units = op.get("qty_by_unit") or {}
    return " · ".join(f"{_n(v)} {u}" for u, v in units.items() if v) or ""


def _output_days(p):
    """Выработка продукта часто есть не каждый день — пишем, по скольким дням она посчитана."""
    o = p.get("output") or {}
    return f"по {o['days']} дн. из {p['days']}" if o and o["days"] < p["days"] else ""


SECTION_ORDER = {k: i for i, k in enumerate(SECTIONS)}


PRODUCT_COLUMNS = ["Продукт", "Цеха", "Дней за период", "Весь путь в среднем", "Путь — минимум", "Путь — максимум",
                   "Выработка в день", "Ед.", "Дней с выработкой", "Выработка — последний этап, самая массовая операция",
                   "Выработка в час на человека", "Чел·ч в день", "Операций", "Источник"]


def product_rows(summary):
    out = []
    for p in sorted(summary["averages"]["products"], key=lambda x: x["product"].lower()):
        o = p.get("output") or {}
        out.append([p["product"], ", ".join(p["sections"]), p["days"], _xl_time(p["lead_min"]), _xl_time(p["lead_min_min"]),
                    _xl_time(p["lead_min_max"]), o.get("qty_day"), o.get("unit", ""), o.get("days"),
                    f"{o['stage_title']} · {o['operation']}" if o else "", o.get("rate_per_person"), p["person_hours_day"],
                    len(p["ops"]), "есть переписка кондитерского цеха — неточно" if p.get("from_chat") else "бот"])
    return out


STAGE_COLUMNS = ["Продукт", "Цех", "Этап цеха", "Дней с этапом", "Дней продукта за период", "Начало от старта продукта",
                 "Длится (от начала до конца)", "Занято (без пауз)"]


def stage_rows(summary):
    out = []
    for p in sorted(summary["averages"]["products"], key=lambda x: x["product"].lower()):
        for st in p["stages"]:
            out.append([p["product"], st["title"].split(" · ")[0], _stage_part(st["title"]), st["days"], p["days"],
                        _xl_time(st["offset_min"]), _xl_time(st["span_min"]), _xl_time(st["busy_min"])])
    return out


PROCESS_COLUMNS = ["Цех", "Продукт", "Этап цеха", "Операция", "Шаг в продукте", "Дней с операцией", "Дней продукта за период",
                   "Начало от старта продукта", "Длится (от начала до конца)", "Занято (без пауз)", "Людей в среднем",
                   "Выработка в день", "Ед.", "Выработка (все единицы)", "Выработка в час", "Выработка в час на человека",
                   "Оборудование", "Без конца / некорректно", "Источник"]
PROCESS_TIME_COLS = (8, 9, 10)   # 1-based: начало, длится, занято


def process_rows(summary):
    """Одна строка — цех × продукт × операция, по ходу процесса продукта: фильтр «Цех» собирает все процессы цеха."""
    rows = []
    for p in summary["averages"]["products"]:
        for step, o in enumerate(p["ops"], 1):
            rows.append((SECTION_ORDER.get(o["section"], 99), p["product"].lower(), step, [
                o["section_title"], p["product"], _stage_part(o["stage_title"]), o["operation"], step, o["days"], p["days"],
                _xl_time(o["offset_min"]), _xl_time(o["span_min"]), _xl_time(o["busy_min"]), o["people_avg"],
                o.get("qty_day_value"), o.get("qty_unit", ""), _units_text(o), o["rate"], o["rate_per_person"], o["equipment"],
                o["no_end"] or None, _source(o)]))
    rows.sort(key=lambda r: r[:3])
    return [r[3] for r in rows]


SHOP_OP_COLUMNS = ["Цех", "Этап цеха", "Операция", "Обычно начинается от старта продукта", "Продуктов", "Продукты",
                   "Дней (всего по продуктам)", "Длится — в среднем по продуктам", "Длится — минимум", "Длится — максимум",
                   "Людей в среднем", "Источник"]


def shop_operation_rows(summary):
    """Свод по цеху: какие операции в нём есть, у каких продуктов и сколько длятся — по ходу процесса цеха
    (по среднему началу от старта продукта), а не по алфавиту."""
    groups = {}
    for p in summary["averages"]["products"]:
        for o in p["ops"]:
            key = (SECTION_ORDER.get(o["section"], 99), o["section_title"], _stage_part(o["stage_title"]), o["operation"])
            g = groups.setdefault(key, {"products": set(), "days": 0, "spans": [], "offsets": [], "people": [], "chat": False})
            g["products"].add(p["product"])
            g["days"] += o["days"]
            for name, value in (("spans", o["span_min"]), ("offsets", o["offset_min"]), ("people", o["people_avg"])):
                if value is not None:
                    g[name].append(value)
            g["chat"] = g["chat"] or bool(o.get("from_chat"))

    def mean(values):
        return sum(values) / len(values) if values else None

    out = []
    for (order, shop, stage, op), g in sorted(groups.items(), key=lambda kv: (
            kv[0][0], kv[0][2], mean(kv[1]["offsets"]) if kv[1]["offsets"] else 1e9, kv[0][3].lower())):
        sp = g["spans"]
        people = mean(g["people"])
        out.append([shop, stage, op, _xl_time(mean(g["offsets"])), len(g["products"]),
                    ", ".join(sorted(g["products"], key=str.lower)), g["days"],
                    _xl_time(mean(sp)), _xl_time(min(sp)) if sp else None, _xl_time(max(sp)) if sp else None,
                    round(people, 1) if people is not None else None,
                    "есть переписка — неточно" if g["chat"] else "бот"])
    return out


def timing_rows(summary):
    rows = []
    for t in summary["timing"]:
        row = [t["product"], t["stages_count"], _hm(t["lead_min"])]
        for st in t["stages"]:
            if st["key"] == "pack":
                hs = st.get("handoffs") or []
                row.append("; ".join(f"сдано {_qty(h)}в {h['time']}" for h in hs) or "нет источника")
            elif st.get("present"):
                row.append(f"{st['start_time']}–{st['end_time'] or '…'} · {_hm(st['span_min']) or '—'}"
                           f" · {st['qty']}" + (f" · до {st['people_max']} чел." if st.get("people_max") else "")
                           + (f" · ожидание {_hm(st['gap_min'])}" if st.get("gap_min") is not None else ""))
            else:
                row.append("—")
        rows.append(row)
    return rows


def timing_columns(summary):
    return ["Продукт (единый)", "Этапов с данными", "От первого начала до последнего конца"] + [s["title"] for s in summary["stages"]]


def build_analysis(summary, filters=None):
    t = summary["totals"]
    rows = [f"Окно: {summary['day']}, {summary['window_title']}.",
            f"Операций на шкале: {t['ops']} (закрыто {t['closed']}, без отметки конца или идут {t['open']}).",
            "По цехам: " + ", ".join(f"{k} — {v}" for k, v in t["by_section"].items()) + ".",
            f"С известным числом людей: {t['people_known']}; человеко-часов (где известно): {str(t['person_hours']).replace('.', ',')}."]
    if t["from_chat"]:
        rows.append(f"Из разобранного чата кондитерского цеха: {t['from_chat']} (до запуска бота); "
                    f"время по времени сообщения — {t['check']}. Эти данные неточные.")
    blocks = [("Итоги окна", rows)]
    av = summary.get("averages") or {}
    if av.get("products"):
        blocks.append((f"Среднее по продукту за {av['from']} — {av['to']} (рабочие сутки 08–08)", [
            f"{x['product']}: дней {x['days']}, путь в среднем {_hm(x['lead_min']) or '—'}"
            + (f", выработка {_n(x['output']['qty_day'])} {x['output']['unit']} в день" if x.get("output") else "") + "; этапы: "
            + ", ".join(f"{st['title']} {_hm(st['span_min']) or '—'}" for st in x["stages"]) + "."
            for x in av["products"][:10]]))
    chains = [x for x in summary["timing"] if x["stages_count"] > 1]
    if chains:
        blocks.append(("Общий тайминг: продукты, прошедшие несколько цехов", [
            f"{x['product']}: этапов {x['stages_count']}, от первого начала до последнего конца {_hm(x['lead_min']) or '—'}."
            for x in chains[:10]]))
    else:
        blocks.append(("Общий тайминг", ["В этом окне нет продукта, найденного больше чем в одном цехе. "
                                         "Сведи названия в справочнике продуктов, если у цехов они разные."]))
    if summary["handoffs"]:
        blocks.append(("Сдано / передано на фасовку (из чата кондитерского цеха)", [f"{h['product']} — {_qty(h)}в {h['time']}"
                                                           for h in summary["handoffs"]]))
    errors = summary.get("section_errors") or {}
    if errors:
        blocks.append(("Источники с ошибками", [f"{k}: {v}" for k, v in errors.items()]))
    caveats = [summary["chat_note"]] if summary.get("chat_note") else []
    blocks.append(("Оговорки", caveats + ["Цеха связаны по единому продукту, а не по номеру партии — у ботов нет общего номера.",
                                "Фасовки пока нет ни в одном боте: этап заложен, данных нет.",
                                "Людей знает только кондитерский цех (бот и чат); у тестозаготовочного, формовочного и печного цехов — только отметивший оператор, "
                                "поэтому выработка у них — в час на всех, не на человека.",
                                "Людей у продукта — максимум одновременно занятых: имён в чате нет, сумма посчитала бы человека дважды.",
                                "Штуки разных операций не складываются: выработка на человека — по строке операции."]))
    if filters:
        blocks.append(("Применённые фильтры", [f"{n}: {v}" for n, v in filters if v]))
    return blocks


def build_xlsx(summary, filters, mapping, ops_map=None):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    head_fill, head_font = PatternFill("solid", fgColor="E8EEFB"), Font(bold=True)

    def sheet(title, columns, rows, widths=None, time_cols=(), caption=""):
        """caption — строка над шапкой (период): по скопированному листу видно, за что эти средние."""
        ws = wb.create_sheet(title[:31])
        top = 1
        if caption:
            ws.append([caption])
            ws.cell(row=1, column=1).font = Font(bold=True)
            top = 2
        ws.append(columns)
        for row in rows:
            ws.append(row)
        for col in time_cols:   # длительность: число (доля суток), видно как ч:мм — суммы и фильтры работают
            for r in range(top + 1, len(rows) + top + 1):
                ws.cell(row=r, column=col).number_format = "[h]:mm"
        if not rows:
            ws.append(["нет данных"])
        for i, c in enumerate(columns, 1):
            cell = ws.cell(row=top, column=i)
            cell.fill, cell.font = head_fill, head_font
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            longest = max([len(str(c))] + [6 if i in time_cols else len(str(r[i - 1] if r[i - 1] is not None else ""))
                                           for r in rows[:200] if i - 1 < len(r)])
            ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, min(max(longest + 2, 9), 60))
        ws.freeze_panes = f"A{top + 1}"
        if rows:
            ws.auto_filter.ref = f"A{top}:{get_column_letter(len(columns))}{len(rows) + top}"

    av = summary.get("averages") or {}
    period = (f"Среднее за {av.get('from', '')} — {av.get('to', '')} (рабочие сутки 08–08): время — от старта работы с продуктом, "
              f"длительности — ч:мм. Данные кондитерского цеха из переписки — неточные.")
    # Сначала — процессы за период «Среднее с … по …»: плоско (строка — продукт × цех × операция),
    # время числом ч:мм — фильтр «Цех» собирает все процессы цеха, работают суммы и сводные таблицы.
    sheet("Процессы по цехам", PROCESS_COLUMNS, process_rows(summary), {2: 30, 4: 34, 14: 28, 17: 26}, PROCESS_TIME_COLS, period)
    sheet("Цеха · операции", SHOP_OP_COLUMNS, shop_operation_rows(summary), {3: 34, 4: 16, 6: 60}, (4, 8, 9, 10), period)
    sheet("Продукты (среднее)", PRODUCT_COLUMNS, product_rows(summary), {1: 30, 10: 40}, (4, 5, 6), period)
    sheet("Этапы по продуктам", STAGE_COLUMNS, stage_rows(summary), {1: 30}, (6, 7, 8), period)
    # Затем — выбранные рабочие сутки (окно диаграммы)
    sheet("Строки диаграммы", ROW_COLUMNS, grouped_rows(summary))
    sheet("Операции", BAR_COLUMNS, bar_rows(summary))
    sheet("Общий тайминг", timing_columns(summary), timing_rows(summary))
    sheet("Передано на фасовку", ["Продукт", "Когда", "Количество", "Ед.", "Время точное"],
          [[h["product"], h["time"], _v(h["qty"]), h["unit"] if h["qty"] is not None else "", "да" if h["exact"] else "по сообщению"]
           for h in summary["handoffs"]])
    sheet("Справочник продуктов", ["Цех", "Продукт в цехе", "Единый продукт"],
          [[SECTIONS.get(sec, sec), name, unified] for sec, items in sorted(mapping.items()) for name, unified in sorted(items.items())])
    sheet("Справочник операций", ["Цех", "Операция в цехе", "Единая операция"],
          [[SECTIONS.get(sec, sec), name, unified] for sec, items in sorted((ops_map or {}).items())
           for name, unified in sorted(items.items())])
    ws = wb.create_sheet("Фильтры")
    ws.append(["Параметр", "Значение"])
    for n, v in filters:
        ws.append([n, v])
    ws.append(["Выгружено", datetime.now(TZ).strftime("%d.%m.%Y %H:%M")])
    for line in build_analysis(summary)[-1][1]:
        ws.append(["Оговорка", line])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 26, 100
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _gantt_drawing(summary, rows, width, font, row_h=11):
    """Диаграмма на примитивах reportlab: строка — продукт · операция, полосы по времени."""
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib.colors import HexColor

    start = datetime.fromisoformat(summary["start"])
    end = datetime.fromisoformat(summary["end"])
    total = (end - start).total_seconds() or 1
    label_w, meta_w = 170, 92
    plot_w = width - label_w - meta_w
    lanes = sum(r["lanes"] for r in rows)
    height = 18 + lanes * row_h + 4
    d = Drawing(width, height)
    hours = int(total // 3600)
    for h in range(hours + 1):
        x = label_w + meta_w + plot_w * h * 3600 / total
        d.add(Line(x, 0, x, height - 14, strokeColor=HexColor("#E6EAF0"), strokeWidth=0.4))
        label = (start.hour + h) % 24
        d.add(String(x, height - 10, f"{label:02d}", fontName=font, fontSize=5.5, textAnchor="middle",
                     fillColor=HexColor("#8A94A6")))
    y = height - 18
    for r in rows:
        block_h = r["lanes"] * row_h
        y -= block_h
        text = f"{r['section_title']} · {r['product']} · {r['operation']}"
        d.add(String(2, y + block_h - 8, text[:48], fontName=font, fontSize=5.6, fillColor=HexColor("#3C4658")))
        info = " · ".join(x for x in (f"{r['people']} чел" if r.get("people") else "",
                                       _rate_text(r).replace(" (людей нет)", "")) if x)
        d.add(String(label_w, y + block_h - 8, info[:30], fontName=font, fontSize=5.2, fillColor=HexColor("#667085")))
        d.add(Line(0, y, width, y, strokeColor=HexColor("#F1F4F8"), strokeWidth=0.3))
        for b in r["bars"]:
            s = max(datetime.fromisoformat(b["start"]), start)
            by = y + (r["lanes"] - 1 - b["lane"]) * row_h + 2
            color = HexColor(SECTION_COLORS.get(b["section"], "#667085"))
            x = label_w + meta_w + plot_w * (s - start).total_seconds() / total
            if b["draw_end"]:
                e = min(datetime.fromisoformat(b["draw_end"]), end)
                w = max(plot_w * (e - s).total_seconds() / total, 1.2)
                d.add(Rect(x, by, w, row_h - 4, fillColor=color, strokeColor=None,
                           fillOpacity=0.45 if b["flag"] != "ok" or b["running"] else 1))
                info = " · ".join(x for x in (f"{b['people']} чел" if b["people"] else "",
                                               f"{b['qty']:,.0f} {b['unit']}".replace(",", " ") if b["qty"] else "") if x)
                if info and w > 40:
                    d.add(String(x + 2, by + 2, info[:int(w / 3)], fontName=font, fontSize=4.8, fillColor=HexColor("#FFFFFF")))
            else:
                d.add(Rect(x - 1.5, by, 3, row_h - 4, fillColor=color, strokeColor=None))
    return d


def _esc(value):
    """Текст данных для Paragraph: «<», «&» в названиях — не разметка (иначе PDF падает)."""
    from xml.sax.saxutils import escape
    return escape("" if value is None else str(value))


def _process_rows(p):
    """Строки рисунка/таблицы продукта: этап, под ним его операции."""
    rows = []
    for st in p["stages"]:
        rows.append({"stage": True, "label": st["title"], "off": st["offset_min"], "span": st["span_min"], "key": st["key"],
                     "days": st["days"], "hatched": False})
        for o in p["ops"]:
            if o["stage"] == st["key"]:
                rows.append({"stage": False, "label": o["operation"], "off": o["offset_min"], "span": o["span_min"],
                             "key": o["stage"], "days": o["days"], "hatched": bool(o["no_end"])})
    return rows


DRAW_ROWS = 40   # строк на один рисунок: выше — делим, иначе рисунок не влезет на страницу (LayoutError)


def _process_drawings(p, width, font, bold, row_h=10):
    """Средний день продукта: этапы и операции от старта продукта (+1 ч, +2 ч…), как на сайте.
    Строки, посчитанные по части дней продукта, подписаны «N/M дн.» и бледнее: средние разных дней,
    порядок на шкале примерный. Много строк — несколько рисунков подряд с одной шкалой."""
    import math

    from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
    from reportlab.lib.colors import HexColor

    rows = _process_rows(p)
    end = max([60] + [(r["off"] or 0) + (r["span"] or 0) for r in rows])
    hours = math.ceil(end / 60)
    step = 1 if hours <= 24 else 2
    total = hours * 60
    label_w = 230
    plot_w = width - label_w
    drawings = []
    for chunk_start in range(0, max(len(rows), 1), DRAW_ROWS):
        chunk = rows[chunk_start:chunk_start + DRAW_ROWS]
        height = 14 + len(chunk) * row_h + 2
        d = Drawing(width, height)
        for h in range(0, hours + 1, step):
            x = label_w + plot_w * h * 60 / total
            d.add(Line(x, 0, x, height - 12, strokeColor=HexColor("#E6EAF0"), strokeWidth=0.4))
            d.add(String(x, height - 9, "0" if h == 0 else f"+{h}ч", fontName=font, fontSize=5.5, textAnchor="middle",
                         fillColor=HexColor("#8A94A6")))
        y = height - 14
        for r in chunk:
            y -= row_h
            color = HexColor(STAGE_COLORS.get(r["key"], "#667085"))
            part = r["days"] < p["days"]
            label = r["label"][:46] + (f"  ({r['days']}/{p['days']} дн.)" if part else "")
            d.add(String(2 if r["stage"] else 10, y + 2.5, label, fontName=bold if r["stage"] else font,
                         fontSize=6 if r["stage"] else 5.7, fillColor=HexColor("#101828" if r["stage"] else "#3C4658")))
            d.add(Line(0, y, width, y, strokeColor=HexColor("#F1F4F8"), strokeWidth=0.3))
            if r["off"] is None:
                continue
            x = label_w + plot_w * r["off"] / total
            faint = r["hatched"] or r["days"] * 2 < p["days"]
            if r["span"]:
                w = max(plot_w * r["span"] / total, 1.5)
                d.add(Rect(x, y + 1.5, w, row_h - 3, fillColor=color, strokeColor=None,
                           fillOpacity=(0.55 if faint else 1) if r["stage"] else (0.4 if faint else 0.85)))
                if r["stage"] and w > 30:
                    d.add(String(x + 2, y + 3, _hm(r["span"]), fontName=bold, fontSize=5, fillColor=HexColor("#FFFFFF")))
            else:   # конца нет — только отметка начала
                cy = y + row_h / 2
                d.add(Polygon([x, cy + 3, x + 3, cy, x, cy - 3, x - 3, cy], fillColor=color, strokeColor=None))
        drawings.append(d)
    return drawings


def _bookmark(title, key, level=0):
    """Закладка PDF (боковая панель «Закладки»): к каждому продукту — в один клик."""
    from reportlab.platypus import Flowable

    class Mark(Flowable):
        width = height = 0

        def draw(self):
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, level=level, closed=level == 0)

    return Mark()


def _process_section(summary, width, font, bold, styles):
    """PDF: по каждому продукту — шапка, средний день на шкале и таблица этапов и операций с временем."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Spacer, Table, TableStyle

    av = summary["averages"]
    h1, h2, body, muted, cell = styles["h1"], styles["h2"], styles["body"], styles["muted"], styles["cell"]
    head = ParagraphStyle("ph", fontName=bold, fontSize=6.4, leading=7.8)
    cell_b = ParagraphStyle("cb", parent=cell, fontName=bold)
    prod_h = ParagraphStyle("prod", fontName=bold, fontSize=11.5, leading=14, spaceBefore=12, spaceAfter=2)
    flag = ParagraphStyle("flag", parent=muted, textColor=colors.HexColor("#A15C00"))

    def P(text, style):
        return Paragraph(_esc(text), style)

    def grid(data, widths, stage_rows=(), right=None):
        t = Table(data, colWidths=widths, repeatRows=1)
        style = [("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E6EAF0")),
                 ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF3FB")),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
        if right:
            style.append(("ALIGN", (right[0], 1), (right[1], -1), "RIGHT"))
        style += [("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F5F7FB")) for r in stage_rows]
        t.setStyle(TableStyle(style))
        return t

    title = f"Процессы по продуктам · {av['from']} — {av['to']}"
    out = [_bookmark("Процессы по продуктам", "processes"), P(title, h1),
           P("Средний день каждого продукта за период: этапы (цеха) и операции, когда начинаются от старта работы "
             "с продуктом в рабочие сутки 08–08 и сколько длятся. «Длится» — от первого начала до последнего конца, "
             "«занято» — без пауз между запусками. Операции без отметки конца — ромбом, в длительность не входят. "
             "Этапы и операции усреднены по своим дням («N/M дн.» — посчитано по N дням из M), поэтому порядок на шкале "
             "примерный. Продукты в разделе — по алфавиту; к каждому есть закладка.", muted),
           Spacer(1, 2 * mm)]
    products = sorted(av.get("products") or [], key=lambda x: x["product"].lower())
    if not products:
        return out + [P("За период операций нет.", body), PageBreak()]

    # сводка по всем продуктам — оглавление раздела
    rows = [[P(c, head) for c in ("Продукт", "Цеха", "Дней", "Весь путь", "Выработка в день", "На чел·ч", "Операций")]]
    for p in products:
        o = p.get("output") or {}
        made = f"{_n(o['qty_day'])} {o['unit']}" + (f" ({_output_days(p)})" if _output_days(p) else "") if o else "—"
        rows.append([P(p["product"], cell), P(", ".join(p["sections"]), cell), P(p["days"], cell),
                     P(_hm(p["lead_min"]) or "—", cell), P(made, cell),
                     P(f"{_n(o['rate_per_person'])} {o['unit']}/чел·ч" if o.get("rate_per_person") is not None else "—", cell),
                     P(len(p["ops"]), cell)])
    out += [P("Все продукты", h2), grid(rows, [width * x for x in (.25, .28, .05, .1, .16, .1, .06)], right=(2, 6)), PageBreak()]

    widths = [width * x for x in (.27, .08, .08, .08, .055, .14, .13, .1, .065)]
    for i, p in enumerate(products):
        o = p.get("output") or {}
        info = [", ".join(p["sections"]), f"дней {p['days']}",
                f"весь путь {_hm(p['lead_min']) or '—'}" + (f" (от {_hm(p['lead_min_min'])} до {_hm(p['lead_min_max'])})"
                                                            if p["days"] > 1 and p["lead_min"] is not None else "")]
        if o:
            info.append(f"выработка {_n(o['qty_day'])} {o['unit']} в день"
                        + (f" ({_output_days(p)})" if _output_days(p) else "")
                        + f" — последний этап «{o['stage_title']}», самая массовая операция «{o['operation']}»"
                        + (f"; {_n(o['rate_per_person'])} {o['unit']}/чел·ч" if o.get("rate_per_person") is not None else ""))
        if p["person_hours_day"] is not None:
            info.append(f"чел·ч в день {_n(p['person_hours_day'])}")
        top = [_bookmark(p["product"], f"product-{i}", level=1), P(p["product"], prod_h), P(" · ".join(info), muted)]
        if p.get("from_chat"):
            top.append(P("Часть данных — из переписки кондитерского цеха, неточно.", flag))
        drawings = _process_drawings(p, width, font, bold)
        top += [Spacer(1, 1.5 * mm), drawings[0], Spacer(1, 1.5 * mm)]

        data = [[P(c, head) for c in ("Этап · операция", "Начало", "Длится", "Занято", "Людей",
                                      "Выработка в день", "В час на чел. / в час", "Оборудование", "Дней")]]
        stage_idx = []
        for st in p["stages"]:
            stage_idx.append(len(data))
            data.append([P(st["title"], cell_b), P("+" + _hm(st["offset_min"]) if st["offset_min"] is not None else "—", cell_b),
                         P(_hm(st["span_min"]) or "—", cell_b), P(_hm(st["busy_min"]) or "—", cell_b),
                         "", "", "", "", P(f"{st['days']}/{p['days']}", cell_b)])
            for op in p["ops"]:
                if op["stage"] != st["key"]:
                    continue
                name = op["operation"] + (f" · без конца {op['no_end']}" if op["no_end"] else "") + (" · переписка" if op.get("from_chat") else "")
                data.append([Paragraph("&nbsp;&nbsp;&nbsp;" + _esc(name), cell),
                             P("+" + _hm(op["offset_min"]) if op["offset_min"] is not None else "—", cell),
                             P(_hm(op["span_min"]) or "—", cell), P(_hm(op["busy_min"]) or "—", cell),
                             P(_n(op["people_avg"]), cell), P(_units_text(op) or "—", cell),
                             P(_rate_text(op).replace(" (людей нет)", "") or "—", cell),
                             P(op["equipment"] or "—", cell), P(f"{op['days']}/{p['days']}", cell)])
        # шапка и первый рисунок держатся вместе; длинная таблица переносится, её шапка повторяется на новой странице
        out.append(KeepTogether(top))
        out += [x for d in drawings[1:] for x in (d, Spacer(1, 1.5 * mm))]
        out.append(grid(data, widths, stage_idx, (1, 6)))
    out.append(PageBreak())
    return out


def build_pdf(summary, analysis, filters, title="Тайминги: диаграмма Ганта и общий тайминг"):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font, bold = _register_fonts()
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm, title=title)
    width = doc.width - 12   # внутренние поля кадра (6 + 6 pt): иначе таблицы и рисунки вылезают за кадр
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=16, leading=20, spaceAfter=4)
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=11, leading=14, spaceBefore=6, spaceAfter=4)
    body = ParagraphStyle("body", fontName=font, fontSize=8.2, leading=11)
    muted = ParagraphStyle("muted", fontName=font, fontSize=7.6, leading=10, textColor=colors.HexColor("#667085"))
    cell = ParagraphStyle("c", fontName=font, fontSize=6.2, leading=7.6)
    warn = ParagraphStyle("warn", fontName=bold, fontSize=8.6, leading=11.5, textColor=colors.HexColor("#7A4B00"),
                          backColor=colors.HexColor("#FFF7E6"), borderPadding=4, spaceBefore=4, spaceAfter=6)
    story = [Paragraph(title, h1), Paragraph(_esc(", ".join(f"{n}: {v}" for n, v in filters if v)), muted),
             Paragraph(f"Сформировано {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}", muted), Spacer(1, 3 * mm)]
    if summary.get("chat_note"):
        story.append(Paragraph("Внимание. " + summary["chat_note"], warn))
    for heading, lines in analysis:
        story.append(KeepTogether([Paragraph(_esc(heading), h2)] + [Paragraph(_esc(x), body) for x in lines]))
    story.append(PageBreak())
    story += _process_section(summary, width, font, bold, styles={"h1": h1, "h2": h2, "body": body, "muted": muted,
                                                                  "cell": cell, "warn": warn})
    story.append(Paragraph(f"Диаграмма Ганта · {summary['day']} · {summary['window_title']}", h2))
    legend = " · ".join(f"<font color='{SECTION_COLORS[k]}'>■</font> {n}" for k, n in SECTIONS.items())
    story.append(Paragraph(legend + " · бледная полоса — время по сообщению или операция идёт; штрих — конец не отмечен", muted))
    rows, chunk, lanes = summary["rows"], [], 0
    for r in rows:
        if lanes + r["lanes"] > 40 and chunk:
            story += [_gantt_drawing(summary, chunk, width, font), PageBreak()]
            chunk, lanes = [], 0
        chunk.append(r)
        lanes += r["lanes"]
    if chunk:
        story.append(_gantt_drawing(summary, chunk, width, font))
    if not rows:
        story.append(Paragraph("В этом окне операций нет.", body))
    story.append(PageBreak())

    def table(columns, data, limit=300):
        t = Table([[Paragraph(str(c), ParagraphStyle("h", fontName=bold, fontSize=6.4, leading=7.8)) for c in columns]]
                  + [[Paragraph(_esc(c), cell) for c in row] for row in data[:limit]], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E6EAF0")),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF3FB")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        return t

    story.append(Paragraph("Общий тайминг по продуктам", h2))
    story.append(table(timing_columns(summary), timing_rows(summary)))
    story.append(PageBreak())
    story.append(Paragraph("Операции", h2))
    cols = [0, 1, 3, 4, 5, 6, 7, 9, 10, 12, 13, 17]
    story.append(table([BAR_COLUMNS[i] for i in cols], [[r[i] for i in cols] for r in bar_rows(summary)]))
    def page_number(canv, doc_):
        canv.setFont(font, 7)
        canv.setFillColor(colors.HexColor("#8A94A6"))
        canv.drawRightString(doc_.pagesize[0] - 10 * mm, 5 * mm, f"стр. {doc_.page}")

    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return out.getvalue()
