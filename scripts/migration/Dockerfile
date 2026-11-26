# esb-migrate: mesin migrasi ESB -> Odoo sisi data (G43, docs/04 §27.2, ADR-042 Accepted 18 Nov 2026).
# Container SEKALI JALAN (compose profil `migrate`, overlay infra/compose/docker-compose.esb.yml):
#   docker compose ... --profile data --profile migrate run --rm esb-migrate python esb_to_odoo.py export ...
# Image sendiri (bukan image esb-extract + mount kode): butuh `cryptography` (AES-GCM/HKDF bundel) yang tidak ada di
# esb-extract, dan image ikut rantai scan/SBOM/sign (build-push.yml) seperti service lain.
# SEC-47: base image dipin tag@digest (sama dengan services/etl dan services/esb-extract; Dependabot `docker`).
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Pola G41 (PR #90, Trivy image): apt 3.0.4 (CVE-2026-109518) dan zlib1g 1:1.3.dfsg+really1.3.1-1+deb13u1 (CVE-2026-85091)
# terbit setelah digest base dipin -> dinaikkan eksplisit (hanya paket ber-CVE). Hapus saat digest python:3.12-slim
# berikutnya sudah memuatnya.
# 2026-11-26 (PR #171, Trivy): libzstd1 1.5.7+dfsg-1+deb13u1 (CVE-2026-124851); digest base terbaru belum memuatnya.
# checkov:skip=CKV_DOCKER_9: "apt" di baris ini = nama PAKET yang dinaikkan (CVE-2026-109518) lewat apt-get, bukan perintah apt
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade apt zlib1g libzstd1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency di site-packages sistem (world-readable) -> bisa dibaca user non-root dari compose (EFN_MIG_UID).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; import clickhouse_connect"

# Kode milik root, hanya-baca untuk user runtime. Test & fixture TIDAK ikut image (.dockerignore + COPY eksplisit).
COPY esb_to_odoo.py validate_import.py ./
COPY efn_mig ./efn_mig

# Default nobody. Compose meng-override ke uid/gid user `odoo` di image Odoo (EFN_MIG_UID/EFN_MIG_GID) agar bundel
# 0600 bisa dibaca importer di direktori pertukaran (runbook migrasi-esb-odoo §2).
USER 65534:65534

# Tanpa ENTRYPOINT: subcommand selalu eksplisit lewat `docker compose run --rm esb-migrate python esb_to_odoo.py ...`.
CMD ["python", "esb_to_odoo.py", "--help"]
