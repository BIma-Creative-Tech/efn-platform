# NestJS BFF - multi-stage, non-root, distroless-style runtime
# syntax=docker/dockerfile:1.7

# SEC-47: base image dipin tag@digest (build reprodusibel); Dependabot `docker` memperbarui digest per tag.
FROM node:26.11-alpine@sha256:143494b1da2945f061539253adc65e4f1569ddf07da2d384c022c791a9d90a4a AS base
WORKDIR /app
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false
COPY package*.json ./

FROM base AS deps
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then npm ci --omit=dev; else npm install --omit=dev; fi

FROM base AS dev
# G16 (semgrep dockerfile.security.missing-user): stage dev/build berjalan sebagai user `node` (uid 1000 bawaan image),
# bukan root. /app milik node agar `start:dev` dan build dapat menulis dist/; runtime tetap user `app`.
RUN chown node:node /app
USER node
RUN --mount=type=cache,target=/home/node/.npm,uid=1000,gid=1000 \
    if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY --chown=node:node . .
EXPOSE 3000
CMD ["npm", "run", "start:dev"]

FROM dev AS build
RUN npm run build && npm prune --omit=dev

FROM node:26.11-alpine@sha256:143494b1da2945f061539253adc65e4f1569ddf07da2d384c022c791a9d90a4a AS runtime
WORKDIR /app
ENV NODE_ENV=production PORT=3000
# G16 (Trivy image gate): runtime hanya butuh `node`. npm/npx/corepack/yarn bawaan image membawa dependensi rentan
# (brace-expansion, picomatch, pacote, ip-address, sigstore - CVE HIGH 2026) dan tidak dipakai saat runtime -> dihapus.
RUN addgroup -S app && adduser -S app -G app \
    && rm -rf /usr/local/lib/node_modules /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
              /usr/local/bin/yarn /usr/local/bin/yarnpkg /opt/yarn-v*
COPY --from=build --chown=app:app /app/dist ./dist
COPY --from=deps  --chown=app:app /app/node_modules ./node_modules
COPY --chown=app:app package.json healthcheck.js ./
USER app
# 3000 = API (HTTPS); 9464 = /metrics (HTTPS mTLS, klien CN/SAN prometheus — docs/04 §8.10)
EXPOSE 3000 9464
# HTTPS ke 127.0.0.1:3000 dengan verifikasi CA internal + servername api-gateway (docs/04 §7.1);
# HTTP hanya bila TLS_CERT_FILE kosong (EFN_ALLOW_PLAINTEXT, non-production).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "healthcheck.js"]
CMD ["node", "dist/main.js"]
