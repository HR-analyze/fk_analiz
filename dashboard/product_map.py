"""Справочник соответствий продуктов цехов → единый продукт (для общего тайминга Ганта).

У ботов свои названия («Батон / ромашка» в БХМ, «Батон классический» в ФК). Чтобы
собрать путь продукта БХМ → ФК → расстойка → отпекание → КЦ, названия сводятся
к одному. Справочник загружают Excel-файлом на дашборде.

Хранение — там же, где планы ФК, но в ОТДЕЛЬНОЙ таблице fk_dashboard_product_map:
таблицу планов (fk_dashboard_plan_state) этот модуль не читает и не меняет.
Без PostgreSQL (локальный запуск) — JSON-файл рядом с PLAN_FILE.
"""
import io
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from threading import Lock

import plans

SECTIONS = {"bhm": "БХМ", "fk": "ФК", "proofing": "Расстойка", "kc": "КЦ"}
TITLE_TO_KEY = {v.lower(): k for k, v in SECTIONS.items()} | {k: k for k in SECTIONS}
MAX_ROWS = 5000
_lock = Lock()


class MapError(Exception):
    """Понятная пользователю ошибка разбора файла."""


def _file():
    return Path(os.getenv("PRODUCT_MAP_FILE", str(Path(plans.PLAN_FILE).with_name("product_map.json"))))


def _pg():
    import pg_plan_store
    return pg_plan_store._connect()


def init_storage():
    if not plans.PG_PLANS:
        return
    with _pg() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS fk_dashboard_product_map (
                id smallint PRIMARY KEY CHECK (id=1),
                payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        db.execute("INSERT INTO fk_dashboard_product_map(id,payload) VALUES (1,'{}'::jsonb) ON CONFLICT (id) DO NOTHING")


def _clean(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()


def load():
    """{"map": {section: {name: unified}}, "updated_at": iso, "file": имя загруженного файла}."""
    if plans.PG_PLANS:
        with _pg() as db:
            row = db.execute("SELECT payload FROM fk_dashboard_product_map WHERE id=1").fetchone()
        payload = (row[0] if row else None) or {}
    else:
        path = _file()
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    payload.setdefault("map", {})
    return payload


def save(mapping, filename=""):
    payload = {"map": mapping, "updated_at": datetime.now().astimezone().isoformat(), "file": filename}
    with _lock:
        if plans.PG_PLANS:
            from psycopg.types.json import Jsonb
            with _pg() as db:
                db.execute("UPDATE fk_dashboard_product_map SET payload=%s, updated_at=now() WHERE id=1", (Jsonb(payload),))
        else:
            path = _file()
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
    return payload


def unify(mapping, section, name):
    """Единое имя продукта; нет в справочнике — само название (связка по точному совпадению)."""
    return (mapping.get(section) or {}).get(name) or name


def template_xlsx(products, mapping):
    """Шаблон для заполнения: все продукты всех цехов, колонка «Единый продукт» — текущая."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Соответствия"
    ws.append(["Цех", "Продукт в цехе", "Единый продукт"])
    for key, title in SECTIONS.items():
        for name in sorted(products.get(key, []), key=str.lower):
            ws.append([title, name, (mapping.get(key) or {}).get(name, "")])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E8EEFB")
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 46
    ws.column_dimensions["C"].width = 46
    ws.freeze_panes = "A2"
    help_ws = wb.create_sheet("Как заполнять")
    for line in ("В колонке «Единый продукт» напиши одно и то же название для продукта во всех цехах.",
                 "Пример: БХМ «Батон / ромашка» и ФК «Батон классический» → «Батон классический».",
                 "Пустая ячейка — продукт связывается только по точному совпадению названия.",
                 "Загружай файл на вкладке «Справочник продуктов» раздела «Гант» с ключом загрузки плана."):
        help_ws.append([line])
    help_ws.column_dimensions["A"].width = 110
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def parse_xlsx(stream):
    import openpyxl
    try:
        wb = openpyxl.load_workbook(stream, read_only=True, data_only=True)
    except Exception as exc:
        raise MapError(f"не удалось открыть файл как Excel: {exc}") from exc
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    head = [(_clean(c) or "").lower() for c in next(rows, [])]

    def col(*names):
        for i, h in enumerate(head):
            if any(n in h for n in names):
                return i
        return None

    i_sec, i_name, i_unified = col("цех"), col("продукт в цехе", "в цехе"), col("единый")
    if None in (i_sec, i_name, i_unified):
        raise MapError("нужны колонки «Цех», «Продукт в цехе», «Единый продукт» — скачай шаблон")
    mapping, count, unknown = {}, 0, set()
    for n, row in enumerate(rows, 2):
        if n > MAX_ROWS:
            raise MapError(f"больше {MAX_ROWS} строк")
        sec = _clean(row[i_sec] if i_sec < len(row) else "").lower()
        name = _clean(row[i_name] if i_name < len(row) else "")
        unified = _clean(row[i_unified] if i_unified < len(row) else "")
        if not sec and not name:
            continue
        key = TITLE_TO_KEY.get(sec)
        if not key:
            unknown.add(sec)
            continue
        if name and unified and unified != name:
            mapping.setdefault(key, {})[name] = unified
            count += 1
    if unknown:
        raise MapError("неизвестный цех: " + ", ".join(sorted(unknown)) + " (нужно: БХМ, ФК, Расстойка, КЦ)")
    return mapping, count
