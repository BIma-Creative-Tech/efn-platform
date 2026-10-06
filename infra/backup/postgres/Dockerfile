# PostgreSQL 16 + WAL-G untuk WAL archiving & base backup ke MinIO (R-T08, RPO 15 menit / RTO 4 jam).
# WAL-G: path-style S3, enkripsi client-side libsodium, kompresi zstd, restore (backup-fetch / wal-fetch) dengan
# satu binary. Sejak gelombang 3 MinIO hanya HTTPS (CA internal via WALG_S3_CA_CERT_FILE, docs/04 §7.1).
# TLS server PostgreSQL dikonfigurasi di compose (ssl=on, hba_file). Lihat docs/runbooks/backup-restore.md.
#
# Basis Debian (bookworm), bukan alpine: binary rilis WAL-G di-link ke glibc. Konsekuensi: volume data dari image
# alpine lama (uid 70, collation musl) TIDAK kompatibel -> pindahkan via dump/restore (runbook bagian "Migrasi").
# SEC-47: base image dipin tag@digest (build reprodusibel); Dependabot `docker` memperbarui digest per tag.
FROM postgres:16.6-bookworm@sha256:557fea37a744d5f4c8faab304b0a90858b53ab119735a88c131fd19dab802f36

ARG WALG_VERSION=v3.0.9
# Isi dengan sha256 resmi saat bump versi (supply chain). Bila kosong, hanya diverifikasi dengan file .sha256 rilis.
ARG WALG_SHA256=""

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# hadolint ignore=DL3008
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl libsodium23; \
    asset="wal-g-pg-22.04-amd64"; \
    base="https://github.com/wal-g/wal-g/releases/download/${WALG_VERSION}"; \
    curl -fsSLo "/tmp/${asset}" "${base}/${asset}"; \
    curl -fsSLo "/tmp/${asset}.sha256" "${base}/${asset}.sha256"; \
    echo "$(cut -d' ' -f1 "/tmp/${asset}.sha256")  /tmp/${asset}" | sha256sum -c -; \
    if [ -n "${WALG_SHA256}" ]; then echo "${WALG_SHA256}  /tmp/${asset}" | sha256sum -c -; fi; \
    install -m 0755 "/tmp/${asset}" /usr/local/bin/wal-g; \
    rm -f "/tmp/${asset}" "/tmp/${asset}.sha256"; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*; \
    wal-g --version

COPY --chmod=0755 efn-walg.sh /usr/local/bin/efn-walg
COPY --chmod=0755 efn-restore.sh /usr/local/bin/efn-restore

# Non-root sejak awal: entrypoint resmi mendukung berjalan sebagai user postgres (volume baru mewarisi owner dari image).
USER postgres

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=10 \
  CMD pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" || exit 1
