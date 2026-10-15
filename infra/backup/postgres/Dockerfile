# PostgreSQL 16 + WAL-G untuk WAL archiving & base backup ke MinIO (R-T08, RPO 15 menit / RTO 4 jam).
# WAL-G: path-style S3, enkripsi client-side libsodium, kompresi zstd, restore (backup-fetch / wal-fetch) dengan
# satu binary. Sejak gelombang 3 MinIO hanya HTTPS (CA internal via WALG_S3_CA_CERT_FILE, docs/04 §7.1).
# TLS server PostgreSQL dikonfigurasi di compose (ssl=on, hba_file). Lihat docs/runbooks/backup-restore.md.
#
# Basis Debian (bookworm), bukan alpine: WAL-G (cgo + libsodium) di-link ke glibc. Konsekuensi: volume data dari image
# alpine lama (uid 70, collation musl) TIDAK kompatibel -> pindahkan via dump/restore (runbook bagian "Migrasi").
# SEC-47: base image dipin tag@digest (build reprodusibel); Dependabot `docker` memperbarui digest per tag.
#
# G17 (Trivy image gate PR #36): WAL-G DIBANGUN DARI SOURCE (tag v3.0.9 + commit diverifikasi), bukan binary rilis.
# Binary rilis v3.0.9 dibangun Go 1.25.11 + golang.org/x/net 0.58.0 + grpc 1.83.0 -> CVE HIGH stdlib/x/net/grpc
# (CVE-2026-78667/78669/97031 baru 10 Okt + daftar G16 di .trivyignore.yaml) tanpa rilis perbaikan upstream.
# Build ini: Go 1.27.2 (stdlib terbaru) + bump minimum x/net 0.60.0, grpc 1.83.2 (go.sum diverifikasi proxy Go).
# Build tag hanya `libsodium` (dipakai WALG_LIBSODIUM_KEY); brotli/lzo tidak dipakai (kompresi zstd, efn-walg.sh).
FROM golang:1.27.2-bookworm@sha256:5cf287a799e6b94384bad13d16b14904c531f51ba65792237e122ce42b392f61 AS walg
ARG WALG_VERSION=v3.0.9
ARG WALG_COMMIT=3e493188db28335fb93a45bd680c6ff8fc3028c1
ENV CGO_ENABLED=1 GOTOOLCHAIN=local GOFLAGS=-trimpath
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsodium-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src/wal-g
# hadolint ignore=DL3062  # bukan `go install`: build dari clone tag + commit yang diverifikasi (hadolint >= 2.13)
RUN git clone --depth 1 --branch "${WALG_VERSION}" https://github.com/wal-g/wal-g.git . \
    && test "$(git rev-parse HEAD)" = "${WALG_COMMIT}" \
    && go get golang.org/x/net@v0.60.0 google.golang.org/grpc@v1.83.2 \
    && go build -mod=mod -tags libsodium -o /out/wal-g \
         -ldflags "-s -w -X github.com/wal-g/wal-g/cmd/pg.walgVersion=${WALG_VERSION} -X github.com/wal-g/wal-g/cmd/pg.gitRevision=${WALG_COMMIT}" \
         ./main/pg

FROM postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# hadolint ignore=DL3008
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates libsodium23; \
    rm -rf /var/lib/apt/lists/*; \
    # G16 (Trivy image gate): gosu image resmi dibangun dengan Go lama (CVE stdlib HIGH/CRITICAL) dan hanya dipakai
    # entrypoint bila container start sebagai root; image ini selalu USER postgres -> dihapus (start sebagai root akan
    # gagal eksplisit). Sertifikat snakeoil paket ssl-cert tidak dipakai (TLS dari /run/secrets) -> kunci privat dihapus.
    rm -f /usr/local/bin/gosu /etc/ssl/private/ssl-cert-snakeoil.key /etc/ssl/certs/ssl-cert-snakeoil.pem

COPY --from=walg --chmod=0755 /out/wal-g /usr/local/bin/wal-g
RUN wal-g --version
COPY --chmod=0755 efn-walg.sh /usr/local/bin/efn-walg
COPY --chmod=0755 efn-restore.sh /usr/local/bin/efn-restore

# Non-root sejak awal: entrypoint resmi mendukung berjalan sebagai user postgres (volume baru mewarisi owner dari image).
USER postgres

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=10 \
  CMD pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" || exit 1
