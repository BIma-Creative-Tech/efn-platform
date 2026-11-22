# syntax=docker/dockerfile:1.7
# EFN login-gateway (Next.js standalone), non-root. ADR-050 (port pola EAL, ADR-049).
#
# Enkripsi in transit (docs/04 §7.1): Next.js standalone tidak punya TLS native -> sidecar ghostunnel
# (login-gateway-tls, network_mode: service:login-gateway) meneruskan TLS ke 127.0.0.1:3005. HOSTNAME=127.0.0.1 agar
# plaintext hanya di loopback. Service ini tidak memegang secret, cookie, atau kredensial; tidak ada koneksi keluar.
# Compose memakai target `runtime`, read_only, tmpfs /tmp dan /app/.next/cache, dan TIDAK men-set `user:` (USER app di bawah).

# Base image dipin tag@digest, sama dengan insight-web; Dependabot `docker` memperbarui digest.
FROM node:22.23-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false \
    PORT=3005 HOSTNAME=127.0.0.1
COPY package*.json ./

FROM base AS dev
RUN chown node:node /app
USER node
RUN --mount=type=cache,target=/home/node/.npm,uid=1000,gid=1000 \
    if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY --chown=node:node . .
RUN mkdir -p .next/cache
EXPOSE 3005
# `next dev` tidak membaca env HOSTNAME -> diteruskan eksplisit lewat -H.
CMD ["sh", "-c", "exec node_modules/.bin/next dev -p \"${PORT:-3005}\" -H \"${HOSTNAME:-127.0.0.1}\""]

FROM dev AS build
RUN npm run build

FROM node:22.23-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS runtime
WORKDIR /app
ENV NODE_ENV=production PORT=3005 HOSTNAME=127.0.0.1 NEXT_TELEMETRY_DISABLED=1
# Runtime hanya butuh `node`: npm/npx/corepack/yarn dihapus (pola G16). musl/zlib dinaikkan eksplisit (pola G41, Trivy).
# hadolint ignore=DL3018
RUN apk add --no-cache --upgrade musl musl-utils zlib \
    && addgroup -S app && adduser -S app -G app \
    && rm -rf /usr/local/lib/node_modules /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
              /usr/local/bin/yarn /usr/local/bin/yarnpkg /opt/yarn-v*
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3005
# Healthcheck lewat loopback IPv4; /healthz tanpa detail.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3005)+'/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
