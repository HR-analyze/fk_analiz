FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY reports.py .
COPY report_scheduler.py .
COPY runner.py .
COPY webapp ./webapp
RUN mkdir -p /app/data

CMD ["python", "runner.py"]
