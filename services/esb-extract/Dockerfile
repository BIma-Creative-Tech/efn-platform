# esb-extract: ESB Core (GET saja) -> ClickHouse (G41, docs/04 §25, ADR-040 Proposed).
# SEC-47: base image dipin tag@digest (sama dengan services/etl; Dependabot `docker` memperbarui digest).
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependency di site-packages sistem (world-readable) -> bisa dibaca user 65534 dari compose.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kode milik root, hanya-baca untuk user runtime; container read_only + tmpfs /tmp (heartbeat di /tmp).
COPY efn_esb ./efn_esb

USER 65534:65534

# Tanpa ENTRYPOINT: compose memakai `command: ["python", "-m", "efn_esb", "run"]`; backfill/probe lewat
# `docker compose run --rm esb-extract python -m efn_esb backfill ...`.
CMD ["python", "-m", "efn_esb", "run"]
