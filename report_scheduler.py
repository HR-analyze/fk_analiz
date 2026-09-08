"""Independent scheduler for the two daily production PDF reports."""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from reports import build_report

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("report_scheduler")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT = os.getenv("WORK_CHAT_ID", "").strip()


async def send(kind: str):
    if not TOKEN or not CHAT:
        raise RuntimeError("BOT_TOKEN and WORK_CHAT_ID are required for reports")
    pdf, start, end = await build_report(kind)
    if kind == "operational":
        caption = (
            f"📊 Оперативная сводка за {start:%d.%m.%Y}\n"
            f"Период: {start:%H:%M}–{end:%H:%M}\n"
            "Краткий отчёт о производстве за текущий день."
        )
    else:
        caption = (
            f"📊 Итоговая сводка за {start:%d.%m.%Y}\n"
            "Период: 00:00–24:00\n"
            "Итоговый отчёт о производстве за полный предыдущий день."
        )
    filename = f"fk_{kind}_{start:%Y%m%d}.pdf"
    bot = Bot(TOKEN)
    try:
        from aiogram.types import BufferedInputFile
        await bot.send_document(int(CHAT), BufferedInputFile(pdf, filename=filename), caption=caption)
    finally:
        await bot.session.close()
    log.info("Report sent: %s", filename)


async def scheduler_loop():
    log.info("Production report scheduler started: operational 20:00, final 08:00 (%s)", TZ.key)
    last_operational = None
    last_final = None
    while True:
        now = datetime.now(TZ)
        if now.hour == 20 and now.minute == 0 and last_operational != now.date():
            last_operational = now.date()
            try:
                await send("operational")
            except Exception:
                log.exception("Operational report failed")
        if now.hour == 8 and now.minute == 0 and last_final != now.date():
            last_final = now.date()
            try:
                await send("final")
            except Exception:
                log.exception("Final report failed")
        await asyncio.sleep(max(5, 60 - now.second))


if __name__ == "__main__":
    asyncio.run(scheduler_loop())