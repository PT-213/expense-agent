FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    TZ=Asia/Shanghai \
    EXPENSE_DATA=/app/data \
    EXPENSE_SEED=/app/seed

WORKDIR /app

RUN apt-get update -o Acquire::Retries=8 -o Acquire::http::Timeout=120 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=8 -o Acquire::http::Timeout=120 \
        libgl1 libglib2.0-0 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY wheelhouse ./wheelhouse
RUN pip install --no-cache-dir --no-index --find-links=/app/wheelhouse -r requirements.txt

COPY app ./app
COPY seed ./seed
COPY scripts ./scripts

RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
