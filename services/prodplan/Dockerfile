# prodplan: production planning CK di atas ESB (G66, docs/04 §30, ADR-051 Proposed).
# SEC-47: base image dipin tag@digest (sama dengan services/esb-extract; Dependabot `docker` memperbarui digest).
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Sama dengan esb-extract (PR #90, Trivy): apt + zlib1g ber-CVE dinaikkan eksplisit sampai digest base memuatnya.
# checkov:skip=CKV_DOCKER_9: "apt" di baris ini = nama PAKET yang dinaikkan lewat apt-get, bukan perintah apt
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade apt zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency di site-packages sistem (world-readable) -> bisa dibaca user 65534.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kode milik root, hanya-baca untuk user runtime; container read_only + tmpfs /tmp (tidak ada tulis ke disk).
COPY efn_prodplan ./efn_prodplan

USER 65534:65534

# Healthcheck didefinisikan di compose (urllib ke http://127.0.0.1:8080/healthz, docs/04 §30.1).
CMD ["python", "-m", "efn_prodplan", "serve"]
