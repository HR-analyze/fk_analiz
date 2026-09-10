"""Independent scheduler for the two daily production PDF reports."""
import asyncio
import logging
import os
from datetime import datetime
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
            f"📊 Оперативная сводка (дневная смена)\n"
            f"Период: {start:%d.%m.%Y %H:%M}–{end:%H:%M}\n"
            "Краткий отчёт о производстве за дневную смену."
        )
        filename = f"fk_day_shift_{start:%Y%m%d}.pdf"
    else:
        caption = (
            f"📊 Оперативная сводка (ночная смена)\n"
            f"Период: {start:%d.%m.%Y %H:%M}–{end:%d.%m.%Y %H:%M}\n"
            "Краткий отчёт о производстве за ночную смену."
        )
        filename = f"fk_night_shift_{end:%Y%m%d}.pdf"
    bot = Bot(TOKEN)
    try:
        from aiogram.types import BufferedInputFile
        await bot.send_document(int(CHAT), BufferedInputFile(pdf, filename=filename), caption=caption)
    finally:
        await bot.session.close()
    log.info("Report sent: %s", filename)


async def scheduler_loop():
    log.info("Production report scheduler started: day shift 20:00 (08:00–20:00), night shift 08:00 (20:00–08:00) (%s)", TZ.key)
    last_operational = None
    last_final = None
    while True:
        now = datetime.now(TZ)
        if now.hour == 20 and now.minute == 0 and last_operational != now.date():
            last_operational = now.date()
            try:
                await send("operational")
            except Exception:
                log.exception("Day shift report failed")
        if now.hour == 8 and now.minute == 0 and last_final != now.date():
            last_final = now.date()
            try:
                await send("final")
            except Exception:
                log.exception("Night shift report failed")
        await asyncio.sleep(max(5, 60 - now.second))


async def main():
    # One-shot test mode. Set REPORT_NOW=operational or REPORT_NOW=final,
    # redeploy once, and the PDF is sent immediately without starting a
    # second Telegram polling loop. Remove the variable after the test.
    report_now = os.getenv("REPORT_NOW", "").strip().lower()
    if report_now in {"operational", "final"}:
        log.info("Manual report test requested: %s", report_now)
        await send(report_now)
        log.info("Manual report test completed")
        return
    await scheduler_loop()


if __name__ == "__main__":
    asyncio.run(main())
