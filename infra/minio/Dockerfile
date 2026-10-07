# Object storage S3 = SILO (fork komunitas MinIO oleh Pigsty, github.com/pgsty/silo, AGPL-3.0-or-later) + klien
# pgsty/mc, keduanya dibangun dari SOURCE tag rilis di pipeline sendiri (bukan menarik image pihak ketiga), sehingga
# SBOM/Trivy/cosign build-push.yml tetap berlaku (R-S08). Keputusan: ADR-030 (status Proposed; rekomendasi jangka
# pendek opsi B diterapkan gelombang 9 karena CVE High aktif di MinIO 2025-10-15; reversible = ganti ARG kembali).
# Kompatibilitas (klaim SILO, diuji gelombang 9 di stack dev): env MINIO_*, format .minio.sys, route /minio/*,
# metrik minio_*. SILO tidak memasang alias biner `minio` -> image ini membuat symlink agar compose/runbook lama jalan.
# Tag di-pin + commit SHA diverifikasi (tag git bisa dipindah; commit tidak).
# Versi Go mengikuti go.mod kedua repo (go 1.27.1); digest base = yang dipakai Dockerfile.goreleaser SILO.
# SEC-47: base image dipin tag@digest (build reprodusibel); Dependabot `docker` memperbarui digest per tag.
FROM golang:1.27.1-alpine@sha256:cf6fca6641884b8433441b2b0652976f975e1d0fdd26d177eaaf8596087f3125 AS build
ARG SILO_TAG=RELEASE.2026-09-16T00-00-00Z
ARG SILO_COMMIT=2a4d51406b7ed87af5fe6fe0f801f3290f96eb3c
ARG MC_TAG=RELEASE.2026-09-16T00-00-00Z
ARG MC_COMMIT=e952aa78f10a2b77dd525a2b7e3143bcda0cd377
# MINIO_RELEASE/MC_RELEASE=RELEASE -> versi "RELEASE.<tanggal>" (bukan "DEVELOPMENT.<tanggal>")
ENV CGO_ENABLED=0 GOFLAGS=-trimpath MINIO_RELEASE=RELEASE MC_RELEASE=RELEASE
# hadolint ignore=DL3018
RUN apk add --no-cache git
WORKDIR /src/silo
# hadolint ignore=DL3062  # bukan `go install`: build dari clone tag + commit yang diverifikasi (hadolint >= 2.13)
RUN git clone --depth 1 --branch "${SILO_TAG}" https://github.com/pgsty/silo.git . \
    && test "$(git rev-parse HEAD)" = "${SILO_COMMIT}" \
    && go build -tags kqueue -ldflags "$(go run buildscripts/gen-ldflags.go)" -o /out/silo .
WORKDIR /src/mc
# hadolint ignore=DL3062  # bukan `go install`: build dari clone tag + commit yang diverifikasi (hadolint >= 2.13)
RUN git clone --depth 1 --branch "${MC_TAG}" https://github.com/pgsty/mc.git . \
    && test "$(git rev-parse HEAD)" = "${MC_COMMIT}" \
    && go build -tags kqueue -ldflags "$(go run buildscripts/gen-ldflags.go)" -o /out/mc .

FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
# hadolint ignore=DL3018
RUN apk add --no-cache ca-certificates \
    && addgroup -S -g 1000 minio && adduser -S -D -H -u 1000 -G minio minio \
    && install -d -o minio -g minio -m 0750 /data
COPY --from=build /out/silo /out/mc /usr/bin/
# Alias kompatibilitas: `minio` (compose entrypoint, runbook) dan `mcli` (nama klien di dokumentasi SILO).
RUN ln -s /usr/bin/silo /usr/bin/minio && ln -s /usr/bin/mc /usr/bin/mcli
# Non-root: volume baru mewarisi owner /data (uid 1000) dari image.
ENV HOME=/tmp
USER 1000:1000
EXPOSE 9000 9001
ENTRYPOINT ["/usr/bin/silo"]
CMD ["server", "/data"]
