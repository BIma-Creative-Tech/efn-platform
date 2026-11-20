# segment-worker: ClickHouse efn_segment.* (read-only) -> segmen -> BFF /internal/v1/segments + Odoo /json/2
# (G50, ADR-047 Proposed). Container & kredensial TERPISAH dari forecast-worker (docs/04 §28.1).
# SEC-47: base image dipin tag@digest (sama dengan services/etl & forecast-worker; Dependabot `docker` memperbarui).
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp

# Pola services/forecast-worker (PR #90, Trivy image): apt (CVE-2026-109518) dan zlib1g (CVE-2026-85091) terbit setelah
# digest base dipin -> dinaikkan eksplisit. Hapus saat digest python:3.12-slim berikutnya sudah memuatnya.
# checkov:skip=CKV_DOCKER_9: "apt" di baris ini = nama PAKET yang dinaikkan lewat apt-get, bukan perintah apt
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade apt zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# SEC-214: lock penuh ber-hash (termasuk transitif). requirements.lock dibuat dari requirements.txt (lihat header file).
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Kode milik root, hanya-baca untuk user runtime; container read_only + tmpfs /tmp.
COPY efn_segment ./efn_segment

USER 65534:65534

# Compose: `command: ["python", "-m", "efn_segment", "run"]` (loop terjadwal, semua PT di SEGMENT_COMPANIES).
CMD ["python", "-m", "efn_segment", "run"]
