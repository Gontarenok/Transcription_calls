# syntax=docker/dockerfile:1
# Стадия 1: сборка зависимостей (компиляция нативных расширений).
FROM python:3.11-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /w

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Стадия 2: рантайм без компиляторов, приложение под непривилегированным пользователем.
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /bin/bash appuser

COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=8s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz').read()"

CMD ["uvicorn", "api_service.main:app", "--host", "0.0.0.0", "--port", "5000"]
