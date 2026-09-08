"""Runs the unchanged production bot plus the independent report scheduler."""
import asyncio
import logging
import os
import signal
import subprocess
import sys

logging.basicConfig(level=logging.INFO)


def main():
    bot = subprocess.Popen([sys.executable, "bot.py"])
    scheduler = subprocess.Popen([sys.executable, "report_scheduler.py"])

    def stop(*_):
        for proc in (scheduler, bot):
            if proc.poll() is None:
                proc.terminate()
        for proc in (scheduler, bot):
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while True:
        if bot.poll() is not None:
            scheduler.terminate()
            raise SystemExit(bot.returncode or 1)
        if scheduler.poll() is not None:
            logging.error("Report scheduler exited with code %s; restarting it", scheduler.returncode)
            scheduler = subprocess.Popen([sys.executable, "report_scheduler.py"])
        signal.pause()


if __name__ == "__main__":
    main()
