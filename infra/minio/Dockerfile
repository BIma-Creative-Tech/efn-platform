# MinIO server + mc dibangun dari SOURCE tag resmi upstream (github.com/minio/minio, github.com/minio/mc; AGPL-3.0).
# Alasan (gelombang 7, runtime): image minio/minio dan minio/mc sudah TIDAK tersedia di Docker Hub (repository
# dihapus; quay.io/minio meminta autentikasi). Membangun dari tag source yang di-pin = rantai pasok paling bisa diaudit
# tanpa bergantung pada fork pihak ketiga (pgsty, bitnamilegacy, chainguard). Keputusan final (fork komunitas vs
# build sendiri vs pengganti S3 lain) -> lead/architect (catatan di docs/agent-reports/efn-devsecops-engineer.md).
# Satu image dipakai service `minio` (server, healthcheck `mc ready`) dan `minio-init` (mc).
# Tag terakhir upstream community edition: minio RELEASE.2025-10-15T17-29-55Z (fix CVE-2025-62506),
# mc RELEASE.2025-08-13T08-35-41Z.
FROM golang:1.24-alpine AS build
ARG MINIO_TAG=RELEASE.2025-10-15T17-29-55Z
ARG MC_TAG=RELEASE.2025-08-13T08-35-41Z
# MINIO_RELEASE/MC_RELEASE=RELEASE -> versi "RELEASE.<tanggal>" (bukan "DEVELOPMENT.<tanggal>")
ENV CGO_ENABLED=0 GOFLAGS=-trimpath MINIO_RELEASE=RELEASE MC_RELEASE=RELEASE
# hadolint ignore=DL3018
RUN apk add --no-cache git
WORKDIR /src/minio
RUN git clone --depth 1 --branch "${MINIO_TAG}" https://github.com/minio/minio.git . \
    && go build -tags kqueue -ldflags "$(go run buildscripts/gen-ldflags.go)" -o /out/minio .
WORKDIR /src/mc
RUN git clone --depth 1 --branch "${MC_TAG}" https://github.com/minio/mc.git . \
    && go build -tags kqueue -ldflags "$(go run buildscripts/gen-ldflags.go)" -o /out/mc .

FROM alpine:3.20
# hadolint ignore=DL3018
RUN apk add --no-cache ca-certificates \
    && addgroup -S -g 1000 minio && adduser -S -D -H -u 1000 -G minio minio \
    && install -d -o minio -g minio -m 0750 /data
COPY --from=build /out/minio /out/mc /usr/bin/
# Non-root: volume baru mewarisi owner /data (uid 1000) dari image.
ENV HOME=/tmp
USER 1000:1000
EXPOSE 9000 9001
ENTRYPOINT ["/usr/bin/minio"]
CMD ["server", "/data"]
