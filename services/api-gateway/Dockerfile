# NestJS BFF - multi-stage, non-root, distroless-style runtime
# syntax=docker/dockerfile:1.7

FROM node:22.12-alpine AS base
WORKDIR /app
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false
COPY package*.json ./

FROM base AS deps
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then npm ci --omit=dev; else npm install --omit=dev; fi

FROM base AS dev
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
EXPOSE 3000
CMD ["npm", "run", "start:dev"]

FROM dev AS build
RUN npm run build && npm prune --omit=dev

FROM node:22.12-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production PORT=3000
RUN addgroup -S app && adduser -S app -G app
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
