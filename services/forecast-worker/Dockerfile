# forecast-worker: ClickHouse efn_forecast.* (read-only) -> statsforecast -> Odoo /json/2 (G44, ADR-046 Proposed).
# SEC-47: base image dipin tag@digest (sama dengan services/etl & esb-extract; Dependabot `docker` memperbarui digest).
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NUMBA_CACHE_DIR=/tmp/numba \
    XDG_CACHE_HOME=/tmp/.cache \
    HOME=/tmp

# G41 (PR #90, Trivy image): apt 3.0.4 (CVE-2026-109518) dan zlib1g (CVE-2026-85091) terbit setelah digest base dipin
# -> dinaikkan eksplisit (pola services/etl). Hapus saat digest python:3.12-slim berikutnya sudah memuatnya.
# checkov:skip=CKV_DOCKER_9: "apt" di baris ini = nama PAKET yang dinaikkan (CVE-2026-109518) lewat apt-get, bukan perintah apt
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade apt zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency di site-packages sistem (world-readable) -> bisa dibaca user 65534 dari compose. Wheel manylinux
# (numba/llvmlite/scipy) -> tanpa compiler di image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kode milik root, hanya-baca untuk user runtime; container read_only + tmpfs /tmp (cache JIT numba di /tmp/numba).
COPY efn_forecast ./efn_forecast

USER 65534:65534

# Compose: `command: ["python", "-m", "efn_forecast", "run", "--company", "SMD"]` (loop terjadwal).
CMD ["python", "-m", "efn_forecast", "run", "--company", "SMD"]
