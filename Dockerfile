# FK + BHM + PROOFING + KC DASHBOARD ONLY. The Telegram bots run from their own repos/branches.
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
    dashboard/plans.py dashboard/pg_plan_store.py dashboard/exports.py \
    dashboard/proofing_source.py dashboard/proofing_aggregate.py dashboard/proofing_exports.py \
    dashboard/kc_source.py dashboard/kc_aggregate.py dashboard/kc_exports.py ./
COPY dashboard/static ./static
COPY dashboard/demo_data.json dashboard/bhm_demo_data.json dashboard/proofing_demo_data.json \
    dashboard/kc_demo_data.json ./
EXPOSE 8080
CMD ["python", "app.py"]
