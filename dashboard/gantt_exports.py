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


PRODUCT_COLUMNS = ["Продукт", "Цеха", "Дней за период", "Весь путь в среднем", "Путь — минимум", "Путь — максимум",
                   "Выработка в день", "Ед.", "Выработка — с какого этапа и операции", "Выработка в час на человека",
                   "Чел·ч в день", "Операций", "Источник"]


def product_rows(summary):
    out = []
    for p in summary["averages"]["products"]:
        o = p.get("output") or {}
        out.append([p["product"], ", ".join(p["sections"]), p["days"], _xl_time(p["lead_min"]), _xl_time(p["lead_min_min"]),
                    _xl_time(p["lead_min_max"]), o.get("qty_day"), o.get("unit", ""),
                    f"{o['stage_title']} · {o['operation']}" if o else "", o.get("rate_per_person"), p["person_hours_day"],
                    len(p["ops"]), "есть переписка кондитерского цеха — неточно" if p.get("from_chat") else "бот"])
    return out


STAGE_COLUMNS = ["Продукт", "Цех", "Этап", "Дней с этапом", "Начало от старта продукта", "Длится (от начала до конца)",
                 "Занято (без пауз)"]


def stage_rows(summary):
    out = []
    for p in summary["averages"]["products"]:
        for st in p["stages"]:
            out.append([p["product"], st["title"].split(" · ")[0], _stage_part(st["title"]), st["days"],
                        _xl_time(st["offset_min"]), _xl_time(st["span_min"]), _xl_time(st["busy_min"])])
    return out


PROCESS_COLUMNS = ["Цех", "Этап цеха", "Продукт", "Операция", "Шаг в продукте", "Дней с операцией", "Дней продукта за период",
                   "Начало от старта продукта", "Длится (от начала до конца)", "Занято (без пауз)", "Людей в среднем",
                   "Выработка в день", "Ед.", "Выработка в час", "Выработка в час на человека", "Оборудование",
                   "Без конца / некорректно", "Источник"]
PROCESS_TIME_COLS = (8, 9, 10)   # 1-based: начало, длится, занято


def process_rows(summary):
    """Одна строка — продукт × цех × операция: по ним собираются все процессы каждого цеха (фильтр «Цех»)."""
    order = {st["key"]: i for i, st in enumerate(summary.get("stages") or [])}
    rows = []
    for p in summary["averages"]["products"]:
        for step, o in enumerate(p["ops"], 1):
            rows.append((order.get(o["stage"], 99), p["product"].lower(), step, [
                o["section_title"], _stage_part(o["stage_title"]), p["product"], o["operation"], step, o["days"], p["days"],
                _xl_time(o["offset_min"]), _xl_time(o["span_min"]), _xl_time(o["busy_min"]), o["people_avg"],
                o.get("qty_day_value"), o.get("qty_unit", ""), o["rate"], o["rate_per_person"], o["equipment"],
                o["no_end"] or None, _source(o)]))
    rows.sort(key=lambda r: r[:3])
    return [r[3] for r in rows]


SHOP_OP_COLUMNS = ["Цех", "Этап цеха", "Операция", "Продуктов", "Продукты", "Дней (всего по продуктам)",
                   "Длится — в среднем по продуктам", "Длится — минимум", "Длится — максимум", "Людей в среднем", "Источник"]


def shop_operation_rows(summary):
    """Свод по цеху: какие операции в нём есть, у каких продуктов и сколько длятся."""
    order = {st["key"]: i for i, st in enumerate(summary.get("stages") or [])}
    groups = {}
    for p in summary["averages"]["products"]:
        for o in p["ops"]:
            g = groups.setdefault((order.get(o["stage"], 99), o["section_title"], _stage_part(o["stage_title"]), o["operation"]),
                                  {"products": [], "days": 0, "spans": [], "people": [], "chat": False})
            g["products"].append(p["product"])
            g["days"] += o["days"]
            if o["span_min"] is not None:
                g["spans"].append(o["span_min"])
            if o["people_avg"] is not None:
                g["people"].append(o["people_avg"])
            g["chat"] = g["chat"] or bool(o.get("from_chat"))
    out = []
    for (_, shop, stage, op), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][3].lower())):
        sp = g["spans"]
        out.append([shop, stage, op, len(set(g["products"])), ", ".join(sorted(set(g["products"]), key=str.lower)), g["days"],
                    _xl_time(sum(sp) / len(sp)) if sp else None, _xl_time(min(sp)) if sp else None, _xl_time(max(sp)) if sp else None,
                    round(sum(g["people"]) / len(g["people"]), 1) if g["people"] else None,
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

    def sheet(title, columns, rows, widths=None, time_cols=()):
        ws = wb.create_sheet(title[:31])
        ws.append(columns)
        for row in rows:
            ws.append(row)
        for col in time_cols:   # длительность: число (доля суток), видно как ч:мм — суммы и фильтры работают
            for r in range(2, len(rows) + 2):
                ws.cell(row=r, column=col).number_format = "[h]:mm"
        if not rows:
            ws.append(["нет данных"])
        for i, c in enumerate(columns, 1):
            cell = ws.cell(row=1, column=i)
            cell.fill, cell.font = head_fill, head_font
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            longest = max([len(str(c)) if i in time_cols else len(str(c))]
                          + [6 if i in time_cols else len(str(r[i - 1] if r[i - 1] is not None else "")) for r in rows[:200] if i - 1 < len(r)])
            ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, min(max(longest + 2, 9), 60))
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"

    # Сначала — процессы за период «Среднее с … по …»: плоско (строка — продукт × цех × операция),
    # время числом ч:мм — фильтр «Цех» собирает все процессы цеха, работают суммы и сводные таблицы.
    sheet("Процессы по цехам", PROCESS_COLUMNS, process_rows(summary), {4: 34, 3: 30, 16: 26}, PROCESS_TIME_COLS)
    sheet("Цеха · операции", SHOP_OP_COLUMNS, shop_operation_rows(summary), {3: 34, 5: 60}, (7, 8, 9))
    sheet("Продукты (среднее)", PRODUCT_COLUMNS, product_rows(summary), {1: 30, 9: 40}, (4, 5, 6))
    sheet("Этапы по продуктам", STAGE_COLUMNS, stage_rows(summary), {1: 30}, (5, 6, 7))
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


def _process_drawing(p, width, font, bold, row_h=10):
    """Средний день продукта: этапы и операции от старта продукта (+1 ч, +2 ч…), как на сайте."""
    import math

    from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
    from reportlab.lib.colors import HexColor

    rows = []
    for st in p["stages"]:
        rows.append((True, st["title"], st["offset_min"], st["span_min"], st["key"], False))
        for o in p["ops"]:
            if o["stage"] == st["key"]:
                rows.append((False, o["operation"], o["offset_min"], o["span_min"], o["stage"], bool(o["no_end"])))
    end = max([60] + [(off or 0) + (span or 0) for _, _, off, span, _, _ in rows])
    hours = math.ceil(end / 60)
    step = 1 if hours <= 24 else 2
    total = hours * 60
    label_w = 210
    plot_w = width - label_w
    height = 14 + len(rows) * row_h + 2
    d = Drawing(width, height)
    for h in range(0, hours + 1, step):
        x = label_w + plot_w * h * 60 / total
        d.add(Line(x, 0, x, height - 12, strokeColor=HexColor("#E6EAF0"), strokeWidth=0.4))
        d.add(String(x, height - 9, "0" if h == 0 else f"+{h}ч", fontName=font, fontSize=5.5, textAnchor="middle",
                     fillColor=HexColor("#8A94A6")))
    y = height - 14
    for is_stage, label, off, span, key, hatched in rows:
        y -= row_h
        color = HexColor(STAGE_COLORS.get(key, "#667085"))
        d.add(String(2 if is_stage else 10, y + 2.5, label[:52], fontName=bold if is_stage else font,
                     fontSize=6 if is_stage else 5.7, fillColor=HexColor("#101828" if is_stage else "#3C4658")))
        d.add(Line(0, y, width, y, strokeColor=HexColor("#F1F4F8"), strokeWidth=0.3))
        if off is None:
            continue
        x = label_w + plot_w * off / total
        if span:
            w = max(plot_w * span / total, 1.5)
            d.add(Rect(x, y + 1.5, w, row_h - 3, fillColor=color, strokeColor=None,
                       fillOpacity=1 if is_stage else (0.5 if hatched else 0.85)))
            if is_stage and w > 30:
                d.add(String(x + 2, y + 3, _hm(span), fontName=bold, fontSize=5, fillColor=HexColor("#FFFFFF")))
        else:   # конца нет — только отметка начала
            cy = y + row_h / 2
            d.add(Polygon([x, cy + 3, x + 3, cy, x, cy - 3, x - 3, cy], fillColor=color, strokeColor=None))
    return d


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
    prod_h = ParagraphStyle("prod", fontName=bold, fontSize=11.5, leading=14, spaceBefore=8, spaceAfter=2)
    flag = ParagraphStyle("flag", parent=muted, textColor=colors.HexColor("#A15C00"))

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

    out = [Paragraph(f"Процессы по продуктам · {av['from']} — {av['to']}", h1),
           Paragraph("Средний день каждого продукта за период: этапы (цеха) и операции, когда начинаются от старта работы "
                     "с продуктом в рабочие сутки 08–08 и сколько длятся. «Длится» — от первого начала до последнего конца, "
                     "«занято» — без пауз между запусками. Операции без отметки конца — ромбом, в длительность не входят.", muted),
           Spacer(1, 2 * mm)]
    products = av.get("products") or []
    if not products:
        return out + [Paragraph("За период операций нет.", body), PageBreak()]

    # сводка по всем продуктам — оглавление раздела
    rows = [[Paragraph(c, head) for c in ("Продукт", "Цеха", "Дней", "Весь путь", "Выработка в день", "На чел·ч", "Операций")]]
    for p in products:
        o = p.get("output") or {}
        rows.append([Paragraph(p["product"], cell), Paragraph(", ".join(p["sections"]), cell), Paragraph(str(p["days"]), cell),
                     Paragraph(_hm(p["lead_min"]) or "—", cell),
                     Paragraph(f"{_n(o['qty_day'])} {o['unit']}" if o else "—", cell),
                     Paragraph(f"{_n(o['rate_per_person'])} {o['unit']}/чел·ч" if o.get("rate_per_person") is not None else "—", cell),
                     Paragraph(str(len(p["ops"])), cell)])
    out += [Paragraph("Все продукты", h2), grid(rows, [width * x for x in (.26, .3, .06, .1, .1, .1, .08)]), PageBreak()]

    widths = [width * x for x in (.27, .08, .09, .08, .07, .12, .13, .12, .04)]
    for p in products:
        o = p.get("output") or {}
        info = [", ".join(p["sections"]), f"дней {p['days']}",
                f"весь путь {_hm(p['lead_min']) or '—'}" + (f" (от {_hm(p['lead_min_min'])} до {_hm(p['lead_min_max'])})"
                                                            if p["days"] > 1 and p["lead_min"] is not None else "")]
        if o:
            info.append(f"выработка {_n(o['qty_day'])} {o['unit']} в день — {o['stage_title']}, «{o['operation']}»"
                        + (f"; {_n(o['rate_per_person'])} {o['unit']}/чел·ч" if o.get("rate_per_person") is not None else ""))
        if p["person_hours_day"] is not None:
            info.append(f"чел·ч в день {_n(p['person_hours_day'])}")
        top = [Paragraph(p["product"], prod_h), Paragraph(" · ".join(info), muted)]
        if p.get("from_chat"):
            top.append(Paragraph("Часть данных — из переписки кондитерского цеха, неточно.", flag))
        top += [Spacer(1, 1.5 * mm), _process_drawing(p, width, font, bold), Spacer(1, 1.5 * mm)]

        data = [[Paragraph(c, head) for c in ("Этап · операция", "Начало", "Длится", "Занято", "Людей",
                                              "Выработка в день", "В час на чел. / в час", "Оборудование", "Дней")]]
        stage_idx = []
        for st in p["stages"]:
            stage_idx.append(len(data))
            data.append([Paragraph(st["title"], cell_b), Paragraph("+" + _hm(st["offset_min"]) if st["offset_min"] is not None else "—", cell_b),
                         Paragraph(_hm(st["span_min"]) or "—", cell_b), Paragraph(_hm(st["busy_min"]) or "—", cell_b),
                         "", "", "", "", Paragraph(str(st["days"]), cell_b)])
            for op in p["ops"]:
                if op["stage"] != st["key"]:
                    continue
                name = op["operation"] + (f" · без конца {op['no_end']}" if op["no_end"] else "") + (" · переписка" if op.get("from_chat") else "")
                data.append([Paragraph("&nbsp;&nbsp;&nbsp;" + name, cell),
                             Paragraph("+" + _hm(op["offset_min"]) if op["offset_min"] is not None else "—", cell),
                             Paragraph(_hm(op["span_min"]) or "—", cell), Paragraph(_hm(op["busy_min"]) or "—", cell),
                             Paragraph(_n(op["people_avg"]), cell),
                             Paragraph(op["qty_day"] if op["qty_day"] != "—" else "—", cell),
                             Paragraph(_rate_text(op).replace(" (людей нет)", "") or "—", cell),
                             Paragraph(op["equipment"] or "—", cell), Paragraph(str(op["days"]), cell)])
        # шапка и рисунок держатся с таблицей; длинная таблица переносится, её шапка повторяется на новой странице
        out.append(KeepTogether(top + [grid(data, widths, stage_idx, (1, 6))]))
        out.append(Spacer(1, 4 * mm))
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
    width = doc.width
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=16, leading=20, spaceAfter=4)
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=11, leading=14, spaceBefore=6, spaceAfter=4)
    body = ParagraphStyle("body", fontName=font, fontSize=8.2, leading=11)
    muted = ParagraphStyle("muted", fontName=font, fontSize=7.6, leading=10, textColor=colors.HexColor("#667085"))
    cell = ParagraphStyle("c", fontName=font, fontSize=6.2, leading=7.6)
    warn = ParagraphStyle("warn", fontName=bold, fontSize=8.6, leading=11.5, textColor=colors.HexColor("#7A4B00"),
                          backColor=colors.HexColor("#FFF7E6"), borderPadding=4, spaceBefore=4, spaceAfter=6)
    story = [Paragraph(title, h1), Paragraph(", ".join(f"{n}: {v}" for n, v in filters if v), muted),
             Paragraph(f"Сформировано {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}", muted), Spacer(1, 3 * mm)]
    if summary.get("chat_note"):
        story.append(Paragraph("Внимание. " + summary["chat_note"], warn))
    for heading, lines in analysis:
        story.append(KeepTogether([Paragraph(heading, h2)] + [Paragraph(x, body) for x in lines]))
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
                  + [[Paragraph(str(c), cell) for c in row] for row in data[:limit]], repeatRows=1)
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
    doc.build(story)
    return out.getvalue()
