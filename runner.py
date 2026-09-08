"""Runs the production bot plus the independent report scheduler.

Set REPORTS_ONLY=1 in a Preview environment to run only the PDF scheduler.
This is important when Preview shares Production BOT_TOKEN: the second bot
polling process must not be started. The scheduler itself sends reports using
the configured BOT_TOKEN and reads the configured database.
"""
import logging
import os
import signal
import subprocess
import sys

logging.basicConfig(level=logging.INFO)


def main():
    reports_only = os.getenv("REPORTS_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}
    scheduler = subprocess.Popen([sys.executable, "report_scheduler.py"])
    bot = None if reports_only else subprocess.Popen([sys.executable, "bot.py"])

    if reports_only:
        logging.info("REPORTS_ONLY enabled: Telegram polling bot is disabled; PDF scheduler only")

    def stop(*_):
        for proc in (scheduler, bot):
            if proc is not None and proc.poll() is None:
                proc.terminate()
        for proc in (scheduler, bot):
            if proc is not None:
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while True:
        if scheduler.poll() is not None:
            logging.error("Report scheduler exited with code %s; restarting it", scheduler.returncode)
            scheduler = subprocess.Popen([sys.executable, "report_scheduler.py"])
        if bot is not None and bot.poll() is not None:
            scheduler.terminate()
            raise SystemExit(bot.returncode or 1)
        signal.pause()


if __name__ == "__main__":
    main()
