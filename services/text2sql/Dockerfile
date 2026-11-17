# Service Text-to-SQL "Tanya Data" (G38, docs/04 §23): Odoo -> HTTPS 8443 -> LLM (9router/Ollama) -> guard sqlglot
# -> ClickHouse efn_t2s (row policy per PT). Base image dipin tag@digest (SEC-47), sama dengan ai-ocr-worker.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# G41 (PR #90, Trivy image): apt 3.0.4 (CVE-2026-109518) dan zlib1g 1:1.3.dfsg+really1.3.1-1+deb13u1 (CVE-2026-85091)
# terbit setelah digest base dipin -> dinaikkan eksplisit (hanya paket ber-CVE, pola G26). Hapus saat digest
# python:3.12-slim berikutnya sudah memuatnya.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade apt zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin text2sql

# Kode + semantic layer dimiliki root (read-only untuk user service).
COPY efn_text2sql ./efn_text2sql
COPY semantic_layer.yaml ./semantic_layer.yaml

USER 10001
EXPOSE 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "efn_text2sql.healthcheck"]

CMD ["python", "-m", "efn_text2sql"]
