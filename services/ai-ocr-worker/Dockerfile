# OCR worker Price Hunter (ADR-008): RabbitMQ ocr.requested -> Tesseract (CPU) -> VLM Ollama bila ragu
# -> PostgreSQL + event loyalty.earn.requested / notify.push.
# SEC-47: base image dipin tag@digest (build reprodusibel); Dependabot `docker` memperbarui digest per tag.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_THREAD_LIMIT=1

WORKDIR /app

# Tesseract + data bahasa Indonesia & Inggris. Tidak perlu libgl (hanya untuk OpenCV/PaddleOCR).
# G18 (PR #60, Trivy): `apt-get upgrade` mengambil perbaikan keamanan Debian (debian-security) yang terbit setelah
# digest base dipin - mis. libexpat1 2.9.0 (CVE-2026-66046/76956/76957/77214/93990) dan libcurl4t64 8.14.1-2+deb13u7
# (CVE-2026-8286/8927/92392), keduanya dependensi transitif tesseract. Gate Trivy di CI = pemeriksa hasilnya.
# APT_REFRESH: layer ini di-cache build (GHA) -> `apt-get upgrade` tidak berjalan ulang saat Debian merilis perbaikan
# baru. Ganti tanggalnya untuk memaksa layer dibangun ulang. 2026-10-24 (PR #68, Trivy): libbrotli1 1.1.0-2+deb13u1
# (CVE-2026-111497, dependensi transitif tesseract via libfreetype).
ARG APT_REFRESH=2026-10-24
RUN echo "apt refresh ${APT_REFRESH}" \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
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
