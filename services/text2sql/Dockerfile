# Service Text-to-SQL "Tanya Data" (G38, docs/04 §23): Odoo -> HTTPS 8443 -> LLM (9router/Ollama) -> guard sqlglot
# -> ClickHouse efn_t2s (row policy per PT). Base image dipin tag@digest (SEC-47), sama dengan ai-ocr-worker.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

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
