# Service Text-to-SQL "Tanya Data" (G38, docs/04 §23): Odoo -> HTTPS 8443 -> LLM (9router/Ollama) -> guard sqlglot
# -> ClickHouse efn_t2s (row policy per PT). Base image dipin tag@digest (SEC-47), sama dengan ai-ocr-worker.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# G41 (PR #90, Trivy image): apt 3.0.4 (CVE-2026-109518) dan zlib1g 1:1.3.dfsg+really1.3.1-1+deb13u1 (CVE-2026-85091)
# terbit setelah digest base dipin -> dinaikkan eksplisit (hanya paket ber-CVE, pola G26). Hapus saat digest
# python:3.12-slim berikutnya sudah memuatnya.
# checkov:skip=CKV_DOCKER_9: "apt" di baris ini = nama PAKET yang dinaikkan (CVE-2026-109518) lewat apt-get, bukan perintah apt
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
