import { createHash, timingSafeEqual } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { E164_RE } from './contract';
import { Log, errMsg } from './log';
import type { Metrics } from './metrics';
import { contractStatus, WaClient } from './wa-client';

/**
 * Endpoint admin (docs/04 §28.9). Di depan service: Traefik entrypoint `wg-https` + middleware `internal-ops`, mTLS CN
 * operator (devsecops). Di service: token admin `Authorization: Bearer <token>` (`/run/secrets/wa_unofficial_admin_token`,
 * dibandingkan sebagai digest SHA-256 dengan `timingSafeEqual`). Gagal autentikasi dibatasi 10/menit (global) → 429.
 *
 * | Metode & path | Auth | Respons |
 * |---|---|---|
 * | `GET /health` | — | `{ status: "ok" \| "idle" }` (healthcheck Docker; tanpa info sesi) |
 * | `GET /v1/admin/session` | admin | `{ status, detail, self, enabled, sent_today, max_per_day }` — nomor sendiri di-mask |
 * | `POST /v1/admin/session/pair` | admin | body `{ "method": "qr" }` → `{ qr }` atau `{ "method": "code", "phone": "62…" }` → `{ code }` |
 * | `POST /v1/admin/session/logout` | admin | `{ status }` — keluar + hapus material sesi |
 * | `GET /metrics` | token metrik (opsional) | teks Prometheus; 404 bila token metrik tidak dikonfigurasi |
 *
 * QR/kode pairing = kredensial pengambilalihan akun: tidak pernah di-log, respons `Cache-Control: no-store`.
 */
export interface AdminDeps {
  mode: 'idle' | 'run';
  client: WaClient | null;
  adminTokenDigest: Buffer | null;
  metricsTokenDigest: Buffer | null;
  metrics: Metrics;
  log: Log;
  sentToday?: () => number;
  maxPerDay?: number;
  now?: () => number;
}

const MAX_BODY = 1024;
const AUTH_FAIL_LIMIT = 10;

function bearerDigest(req: IncomingMessage): Buffer | null {
  const h = req.headers.authorization;
  if (typeof h !== 'string' || !h.startsWith('Bearer ')) return null;
  const t = h.slice(7).trim();
  if (t.length === 0 || t.length > 512) return null;
  return createHash('sha256').update(t, 'utf8').digest();
}

function send(res: ServerResponse, status: number, body: unknown, contentType = 'application/json'): void {
  const text = contentType === 'application/json' ? JSON.stringify(body) : String(body);
  res.writeHead(status, {
    'Content-Type': `${contentType}; charset=utf-8`,
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Content-Length': Buffer.byteLength(text),
  });
  res.end(text);
}

function readJson(req: IncomingMessage): Promise<Record<string, unknown> | null> {
  return new Promise((resolve) => {
    const chunks: Buffer[] = [];
    let size = 0;
    let done = false;
    req.on('data', (c: Buffer) => {
      size += c.length;
      if (size > MAX_BODY && !done) {
        done = true;
        resolve(null);
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      if (done) return;
      done = true;
      if (size === 0) return resolve({});
      try {
        const v = JSON.parse(Buffer.concat(chunks).toString('utf8')) as unknown;
        resolve(typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
      } catch {
        resolve(null);
      }
    });
    req.on('error', () => {
      if (!done) {
        done = true;
        resolve(null);
      }
    });
  });
}

export function createAdminHandler(d: AdminDeps): (req: IncomingMessage, res: ServerResponse) => void {
  const now = d.now ?? (() => Date.now());
  let failWindow = 0;
  let failCount = 0;

  const authorized = (req: IncomingMessage, digest: Buffer | null): boolean | 'limited' => {
    const minute = Math.floor(now() / 60_000);
    if (minute !== failWindow) {
      failWindow = minute;
      failCount = 0;
    }
    if (failCount >= AUTH_FAIL_LIMIT) return 'limited';
    const given = bearerDigest(req);
    const ok = digest !== null && given !== null && timingSafeEqual(given, digest);
    if (!ok) failCount += 1;
    return ok;
  };

  const route = async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    const url = (req.url ?? '/').split('?')[0];
    const method = req.method ?? 'GET';
    if (url === '/health' && method === 'GET') return send(res, 200, { status: d.mode === 'idle' ? 'idle' : 'ok' });

    if (url === '/metrics' && method === 'GET') {
      if (!d.metricsTokenDigest) return send(res, 404, { error: 'not_found' });
      const a = authorized(req, d.metricsTokenDigest);
      if (a === 'limited') return send(res, 429, { error: 'rate_limited' });
      if (!a) return send(res, 401, { error: 'unauthorized' });
      return send(res, 200, d.metrics.render(), 'text/plain; version=0.0.4');
    }

    if (!url.startsWith('/v1/admin/')) return send(res, 404, { error: 'not_found' });
    const a = authorized(req, d.adminTokenDigest);
    if (a === 'limited') return send(res, 429, { error: 'rate_limited' });
    if (!a) {
      d.log.warn('akses admin ditolak', { path: url });
      return send(res, 401, { error: 'unauthorized' });
    }
    if (d.mode === 'idle' || !d.client) return send(res, 503, { error: 'disabled' });
    const client = d.client;

    if (url === '/v1/admin/session' && method === 'GET') {
      const s = client.state();
      return send(res, 200, {
        status: contractStatus(s),
        detail: s,
        self: client.selfMasked(),
        enabled: true,
        sent_today: d.sentToday ? d.sentToday() : null,
        max_per_day: d.maxPerDay ?? null,
      });
    }
    if (url === '/v1/admin/session/pair' && method === 'POST') {
      const body = await readJson(req);
      if (!body) return send(res, 400, { error: 'invalid_request' });
      const m = body.method;
      if (m !== 'qr' && m !== 'code') return send(res, 400, { error: 'invalid_request' });
      let phone: string | undefined;
      if (m === 'code') {
        if (typeof body.phone !== 'string' || !E164_RE.test(body.phone)) return send(res, 400, { error: 'invalid_request' });
        phone = body.phone;
      }
      if (client.state() === 'connected') return send(res, 409, { error: 'already_paired' });
      if (client.state() === 'banned') return send(res, 409, { error: 'banned' });
      try {
        const r = await client.pair(m, phone);
        d.log.info('pairing diminta', { method: m });
        return send(res, 200, r);
      } catch (err) {
        d.log.warn('pairing gagal', { method: m, err: errMsg(err) });
        return send(res, 502, { error: 'pair_failed' });
      }
    }
    if (url === '/v1/admin/session/logout' && method === 'POST') {
      try {
        await client.logout();
      } catch (err) {
        d.log.warn('logout gagal', { err: errMsg(err) });
        return send(res, 502, { error: 'logout_failed' });
      }
      d.log.warn('sesi WhatsApp di-logout oleh operator');
      return send(res, 200, { status: contractStatus(client.state()) });
    }
    return send(res, 404, { error: 'not_found' });
  };

  return (req, res) => {
    route(req, res).catch((err: unknown) => {
      d.log.error('handler admin gagal', { err: errMsg(err) });
      if (!res.headersSent) send(res, 500, { error: 'internal' });
    });
  };
}
