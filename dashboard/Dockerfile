FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py aggregate.py sources.py ./
COPY static ./static
COPY demo_data.json ./demo_data.json

EXPOSE 8090
CMD ["python", "app.py"]
