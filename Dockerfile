FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY sat /app/sat
COPY config /app/config

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && playwright install --with-deps chromium firefox

EXPOSE 8000

CMD ["sat", "web", "--host", "0.0.0.0", "--port", "8000", "--config", "config/docker.toml"]