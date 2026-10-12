# FK + BHM DASHBOARD ONLY. The Telegram bot still runs from main.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RELAXDEV_DEPLOY=1 \
    PORT=8080
COPY dashboard/requirements.txt ./requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt
COPY dashboard/app.py dashboard/aggregate.py dashboard/sources.py dashboard/bhm_source.py \
    dashboard/plans.py dashboard/pg_plan_store.py dashboard/exports.py ./
COPY dashboard/static ./static
COPY dashboard/demo_data.json dashboard/bhm_demo_data.json ./
EXPOSE 8080
CMD ["python", "app.py"]
