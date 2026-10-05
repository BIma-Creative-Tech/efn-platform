# OCR worker Price Hunter (ADR-008): RabbitMQ ocr.requested -> Tesseract (CPU) -> VLM Ollama bila ragu
# -> PostgreSQL + event loyalty.earn.requested / notify.push.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_THREAD_LIMIT=1

WORKDIR /app

# Tesseract + data bahasa Indonesia & Inggris. Tidak perlu libgl (hanya untuk OpenCV/PaddleOCR).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin worker

# Kode dimiliki root (read-only untuk user worker).
COPY efn_ocr ./efn_ocr

USER 10001
EXPOSE 9100

# Probe /metrics di loopback; HTTPS otomatis bila METRICS_TLS_CERT_FILE di-set (lihat efn_ocr/healthcheck.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "efn_ocr.healthcheck"]

CMD ["python", "-m", "efn_ocr"]
