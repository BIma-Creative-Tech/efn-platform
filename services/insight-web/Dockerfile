# syntax=docker/dockerfile:1.7
# EFN Insight (Next.js standalone), non-root. ADR-041, docs/04 §26.
#
# Enkripsi in transit (docs/04 §7.1): Next.js standalone tidak punya TLS native -> sidecar ghostunnel
# (insight-web-tls, network_mode: service:insight-web) meneruskan TLS ke 127.0.0.1:3002. HOSTNAME=127.0.0.1 agar
# plaintext hanya di loopback. Keluar ke ClickHouse: CH_URL=https://... + NODE_EXTRA_CA_CERTS=/run/secrets/efn_ca_crt
# (diperiksa saat boot oleh instrumentation.ts; konfigurasi tidak aman -> exit 1).
# Compose memakai target `runtime`, read_only, tmpfs /tmp dan /app/.next/cache, dan TIDAK men-set `user:` (USER app di bawah).

# SEC-47: base image dipin tag@digest (sama dengan superapp-web); Dependabot `docker` memperbarui digest.
FROM node:22.23-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false \
    PORT=3002 HOSTNAME=127.0.0.1
COPY package*.json ./

FROM base AS dev
RUN chown node:node /app
USER node
RUN --mount=type=cache,target=/home/node/.npm,uid=1000,gid=1000 \
    if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY --chown=node:node . .
RUN mkdir -p .next/cache
EXPOSE 3002
# `next dev` tidak membaca env HOSTNAME -> diteruskan eksplisit lewat -H.
CMD ["sh", "-c", "exec node_modules/.bin/next dev -p \"${PORT:-3002}\" -H \"${HOSTNAME:-127.0.0.1}\""]

FROM dev AS build
RUN npm run build

FROM node:22.23-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS runtime
WORKDIR /app
ENV NODE_ENV=production PORT=3002 HOSTNAME=127.0.0.1 NEXT_TELEMETRY_DISABLED=1
# Runtime hanya butuh `node`: npm/npx/corepack/yarn dihapus (CVE dependensi bawaan, pola G16).
# musl/musl-utils/zlib dinaikkan eksplisit (pola G41, Trivy); hapus saat digest base berikutnya memuatnya.
# hadolint ignore=DL3018
RUN apk add --no-cache --upgrade musl musl-utils zlib \
    && addgroup -S app && adduser -S app -G app \
    && rm -rf /usr/local/lib/node_modules /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
              /usr/local/bin/yarn /usr/local/bin/yarnpkg /opt/yarn-v*
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3002
# Healthcheck lewat loopback IPv4; /api/health tanpa detail dan tanpa ClickHouse.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3002)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
