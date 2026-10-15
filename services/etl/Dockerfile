# ETL incremental PostgreSQL -> ClickHouse (ADR-003, batch 15 menit).
# SEC-47: base image dipin tag@digest (build reprodusibel); Dependabot `docker` memperbarui digest per tag.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --system --uid 10002 --home-dir /app --shell /usr/sbin/nologin etl

# Kode dimiliki root (read-only untuk user etl); container berjalan read_only + tmpfs /tmp di compose.
COPY efn_etl ./efn_etl

USER 10002

CMD ["python", "-m", "efn_etl"]
