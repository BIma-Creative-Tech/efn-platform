# syntax=docker/dockerfile:1.7
# wa-unofficial — WhatsApp tidak resmi (Baileys) HANYA promo & notify-me supermarket (ADR-044 Proposed, K18-01).
# (Direktif syntax WAJIB baris pertama; di bawah komentar ia diabaikan - devsecops G50.)
#
# Container terisolasi: non-root, dirancang untuk read-only rootfs (`read_only: true` + tmpfs /tmp di compose);
# satu-satunya path tulis = volume sesi terenkripsi /var/lib/wa-unofficial. Tanpa npm/npx saat runtime (tidak ada egress
# ke registry). Lockfile WAJIB (ADR-044 §3.11: versi Baileys di-pin exact + lockfile; build gagal tanpa lockfile).

# SEC-47: base dipin tag@digest, SAMA dengan api-gateway (Dependabot `docker` memperbarui keduanya).
FROM node:22.23-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS base
WORKDIR /app
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=false

FROM base AS deps
# Baileys 6.x menarik `libsignal` dari GitHub (dependensi git) → git hanya di stage build, tidak di runtime.
# hadolint ignore=DL3018
RUN apk add --no-cache git
COPY package.json package-lock.json ./
# --ignore-scripts: tidak menjalankan install script pihak ketiga (R-S21). Bila build/test gagal karena modul butuh
# script (belum terverifikasi untuk versi yang di-pin), catat di README sebelum mencabut flag ini.
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev --ignore-scripts

FROM base AS build
# hadolint ignore=DL3018
RUN apk add --no-cache git && chown node:node /app
USER node
COPY --chown=node:node package.json package-lock.json ./
RUN --mount=type=cache,target=/home/node/.npm,uid=1000,gid=1000 npm ci --ignore-scripts
COPY --chown=node:node tsconfig.json tsconfig.build.json ./
COPY --chown=node:node src ./src
RUN npm run build

FROM node:22.23-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS runtime
WORKDIR /app
ENV NODE_ENV=production PORT=8443 WA_UNOFFICIAL_STATE_DIR=/var/lib/wa-unofficial
# hadolint ignore=DL3018
RUN apk add --no-cache --upgrade musl musl-utils zlib \
    && addgroup -S app && adduser -S app -G app \
    && mkdir -p /var/lib/wa-unofficial && chown app:app /var/lib/wa-unofficial && chmod 0700 /var/lib/wa-unofficial \
    && rm -rf /usr/local/lib/node_modules /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
              /usr/local/bin/yarn /usr/local/bin/yarnpkg /opt/yarn-v*
COPY --from=deps  --chown=app:app /app/node_modules ./node_modules
COPY --from=build --chown=app:app /app/dist ./dist
COPY --chown=app:app package.json ./
USER app
VOLUME ["/var/lib/wa-unofficial"]
# 8443 = admin HTTPS (/health, /v1/admin/*, /metrics) — hanya lewat Traefik wg-https + internal-ops (mTLS operator)
EXPOSE 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "dist/healthcheck.js"]
CMD ["node", "dist/main.js"]
