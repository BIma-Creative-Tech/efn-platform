# Object storage S3 = SILO (fork komunitas MinIO oleh Pigsty, github.com/pgsty/silo, AGPL-3.0-or-later) + klien
# pgsty/mc, keduanya dibangun dari SOURCE tag rilis di pipeline sendiri (bukan menarik image pihak ketiga), sehingga
# SBOM/Trivy/cosign build-push.yml tetap berlaku (R-S08). Keputusan: ADR-030 (status Proposed; rekomendasi jangka
# pendek opsi B diterapkan gelombang 9 karena CVE High aktif di MinIO 2025-10-15; reversible = ganti ARG kembali).
# Kompatibilitas (klaim SILO, diuji gelombang 9 di stack dev): env MINIO_*, format .minio.sys, route /minio/*,
# metrik minio_*. SILO tidak memasang alias biner `minio` -> image ini membuat symlink agar compose/runbook lama jalan.
# Tag di-pin + commit SHA diverifikasi (tag git bisa dipindah; commit tidak).
# Versi Go: go.mod kedua repo menuntut go 1.27.1; builder 1.27.3 (patch stdlib; G16 1.27.2, G22 22 Okt 2026 CVE-2026-111943).
# G17 (Trivy PR #36): bump minimum golang.org/x/net 0.59.0 -> 0.60.0 (CVE-2026-78669, HTTP/2 SETTINGS DoS) sebelum
# build; go.sum diverifikasi proxy/sumdb Go. Hapus `go get` ini bila tag SILO/mc berikutnya sudah memuat >= 0.60.0.
# G30 (Trivy PR #76): SILO juga bump github.com/go-jose/go-jose/v4 4.1.4 -> 4.1.6 (CVE-2026-116587, DoS parsing JWK
# RSA). mc tidak terdampak. Hapus bila tag SILO berikutnya sudah memuat >= 4.1.6.
# SEC-47: base image dipin tag@digest (build reprodusibel); Dependabot `docker` memperbarui digest per tag.
FROM golang:1.27.3-alpine@sha256:d530cf60723abf1ffae0cd16bb546b379ec0550d2f3815e5b16ed69dbc00fba4 AS build
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
    && go get golang.org/x/net@v0.60.0 github.com/go-jose/go-jose/v4@v4.1.6 \
    && go build -tags kqueue -ldflags "$(go run buildscripts/gen-ldflags.go)" -o /out/silo .
WORKDIR /src/mc
# hadolint ignore=DL3062  # bukan `go install`: build dari clone tag + commit yang diverifikasi (hadolint >= 2.13)
RUN git clone --depth 1 --branch "${MC_TAG}" https://github.com/pgsty/mc.git . \
    && test "$(git rev-parse HEAD)" = "${MC_COMMIT}" \
    && go get golang.org/x/net@v0.60.0 \
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
