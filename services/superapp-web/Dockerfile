# syntax=docker/dockerfile:1.7
# Next.js Super-App (standalone output), non-root
# (Directive `syntax` harus di baris pertama agar berlaku.)
#
# Enkripsi in transit (docs/04 §7.1):
# - Inbound: Next.js standalone tidak punya TLS native -> sidecar ghostunnel (network_mode: service:superapp-web)
#   meneruskan TLS ke 127.0.0.1:${PORT}. Set HOSTNAME=127.0.0.1 di compose agar plaintext hanya di loopback.
#   Default 0.0.0.0 agar dev tanpa sidecar tetap jalan.
# - Outbound ke BFF: BFF_INTERNAL_URL=https://api-gateway:3000 + NODE_EXTRA_CA_CERTS=/run/secrets/efn_ca_crt
#   (divalidasi saat boot oleh instrumentation.ts; konfigurasi tidak aman -> exit 1).
# Catatan: Docker men-set HOSTNAME = id container secara default; ENV di bawah menimpanya.

FROM node:22.12-alpine AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false \
    PORT=3001 HOSTNAME=0.0.0.0
COPY package*.json ./

FROM base AS dev
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
EXPOSE 3001
# `next dev` tidak membaca env HOSTNAME -> diteruskan eksplisit lewat -H.
CMD ["sh", "-c", "exec node_modules/.bin/next dev -p \"${PORT:-3001}\" -H \"${HOSTNAME:-0.0.0.0}\""]

FROM dev AS build
# Browser tidak memanggil BFF langsung (connect-src 'self'); URL BFF hanya runtime server-side (BFF_INTERNAL_URL).
RUN npm run build

FROM node:22.12-alpine AS runtime
WORKDIR /app
# server.js standalone membaca PORT & HOSTNAME saat start.
ENV NODE_ENV=production PORT=3001 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S app && adduser -S app -G app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3001
# Healthcheck lewat loopback IPv4 (bukan `localhost` yang bisa resolve ke ::1): berhasil baik saat
# HOSTNAME=0.0.0.0 maupun 127.0.0.1 (sidecar). Jangan set HOSTNAME ke IP non-loopback spesifik.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3001)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
