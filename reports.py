"""Automated PDF production reports. Kept isolated from the production write path."""
import io
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
API_URL = os.getenv("DASHBOARD_API_URL", "http://127.0.0.1:8080").strip().rstrip("/")
API_KEY = os.getenv("DASHBOARD_API_KEY", "").strip()
FONT_PATH = os.getenv("REPORT_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
if os.path.exists(FONT_PATH):
    pdfmetrics.registerFont(TTFont("ReportFont", FONT_PATH))
    FONT = "ReportFont"
else:
    FONT = "Helvetica"


def report_period(kind: str, now=None):
    now = now or datetime.now(TZ)
    if kind == "operational":
        # Evening report: current day 08:00 -> 20:00.
        start = now.replace(hour=8, minute=0, second=0, microsecond=0)
        end = now.replace(hour=20, minute=0, second=0, microsecond=0)
    elif kind == "final":
        # Morning operational report for the full operational day:
        # previous day 08:00 -> current day 08:00.
        end = now.replace(hour=8, minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=24)
    else:
        raise ValueError(f"Unknown report kind: {kind}")
    return start, end


def _as_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=TZ)
    except ValueError:
        return None


async def fetch_dashboard(start, end):
    if not API_KEY:
        raise RuntimeError("DASHBOARD_API_KEY is required")
    day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    params = {"date_from": day_start.isoformat(), "date_to": end.date().isoformat()}
    headers = {"X-API-Key": API_KEY}
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{API_URL}/api/dashboard/all", params=params, headers=headers) as response:
            response.raise_for_status()
            data = await response.json()
    data["production"] = [x for x in data.get("production", []) if start <= (_as_dt(x.get("created_at")) or start) < end]
    data["pauses"] = [x for x in data.get("pauses", []) if start <= (_as_dt(x.get("created_at")) or start) < end]
    return data


def _fmt(value):
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _styles():
    styles = getSampleStyleSheet()
    for name in ("Title", "Normal", "Heading2"):
        styles[name].fontName = FONT
    return styles


def make_pdf(data, start, end, title):
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=12*mm, rightMargin=12*mm, topMargin=12*mm, bottomMargin=12*mm)
    styles = _styles()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 3*mm), Paragraph(f"Период: {start:%d.%m.%Y %H:%M} — {end:%d.%m.%Y %H:%M}", styles["Normal"]), Spacer(1, 5*mm)]
    production = data.get("production", [])
    pauses = data.get("pauses", [])
    closed = [x for x in production if x.get("status") == "closed"]
    total_qty = sum(int(x.get("quantity") or 0) for x in closed)
    summary = [["Показатель", "Значение"], ["Производств создано", str(len(production))], ["Завершено", str(len(closed))], ["Произведено, шт.", _fmt(total_qty)], ["Критических остановок", str(len(pauses))]]
    story.append(Table(summary, colWidths=[90*mm, 60*mm], style=TableStyle([("GRID", (0,0), (-1,-1), .4, colors.grey), ("BACKGROUND", (0,0), (-1,0), colors.lightgrey), ("FONTNAME", (0,0), (-1,0), FONT), ("FONTNAME", (0,1), (-1,-1), FONT), ("FONTSIZE", (0,0), (-1,-1), 8)])))
    story.append(Spacer(1, 5*mm))
    rows = [["Оборудование", "Продукция", "Кол-во", "Статус"]]
    for item in production:
        rows.append([str(item.get("equipment") or ""), str(item.get("product") or ""), _fmt(item.get("quantity")) if item.get("quantity") is not None else "—", str(item.get("status") or "")])
    if len(rows) > 1:
        story.append(Table(rows, repeatRows=1, colWidths=[43*mm, 75*mm, 25*mm, 22*mm], style=TableStyle([("GRID", (0,0), (-1,-1), .3, colors.grey), ("BACKGROUND", (0,0), (-1,0), colors.lightgrey), ("FONTNAME", (0,0), (-1,-1), FONT), ("FONTSIZE", (0,0), (-1,-1), 6.5), ("VALIGN", (0,0), (-1,-1), "TOP")])) )
    if pauses:
        story.append(Spacer(1, 5*mm)); story.append(Paragraph("Критические остановки", styles["Heading2"]))
        pause_rows = [["Оборудование", "Причина", "Период"]]
        for item in pauses:
            pause_rows.append([str(item.get("equipment") or ""), str(item.get("reason") or ""), f"{item.get('start_time') or '—'}–{item.get('end_time') or '—'}"])
        story.append(Table(pause_rows, repeatRows=1, colWidths=[55*mm, 65*mm, 35*mm], style=TableStyle([("GRID", (0,0), (-1,-1), .3, colors.grey), ("BACKGROUND", (0,0), (-1,0), colors.lightgrey), ("FONTNAME", (0,0), (-1,-1), FONT), ("FONTSIZE", (0,0), (-1,-1), 6.5)])))
    doc.build(story)
    return output.getvalue()


async def build_report(kind: str):
    start, end = report_period(kind)
    data = await fetch_dashboard(start, end)
    if kind == "operational":
        title = "Оперативная сводка (дневная смена)"
    else:
        title = "Оперативная сводка за смену"
    return make_pdf(data, start, end, title), start, end
