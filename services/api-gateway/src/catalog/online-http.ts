import { RawBodyRequest } from '@nestjs/common';
import { FastifyReply, FastifyRequest } from 'fastify';
import { AppConfig, OnlineRateLimitName } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { clientIp } from '../common/net/client-ip';
import { redisKeyHash } from '../common/pii/pii';
import { RateLimitResult, RateLimiter } from '../common/rate-limit/rate-limiter';
import { OnlineErrors } from '../odoo-rpc/online-errors';

/** Batas body endpoint pelanggan online (§10.3.1). */
export const ONLINE_BODY_LIMIT = 16 * 1024;

/** Kill switch §10.1 (`ONLINE_ENABLED=false` → 503 `online_unavailable`). */
export function requireOnline(config: AppConfig): void {
  if (!config.onlineEnabled) throw OnlineErrors.unavailable();
}
export function requireCatalog(config: AppConfig): void {
  if (!config.onlineCatalogEnabled) throw OnlineErrors.unavailable();
}

/** Body ≤ 16 KB (diukur dari raw body; `rawBody: true` di main.ts). */
export function assertBodySize(req: RawBodyRequest<FastifyRequest>): void {
  const raw = req.rawBody;
  const len = raw ? raw.length : Number(req.headers['content-length'] ?? 0);
  if (len > ONLINE_BODY_LIMIT) throw Errors.payloadTooLarge();
}

/** Rate limit per IP (nama key di-HMAC, SEC-24/§8.16). */
export async function limitByIp(limiter: RateLimiter, config: AppConfig, name: OnlineRateLimitName, req: FastifyRequest): Promise<void> {
  const rl = config.onlineRateLimit(name);
  const ip = redisKeyHash(config.redisKeyPepper, clientIp(req) ?? 'unknown');
  await limiter.enforce(`online:${name.toLowerCase()}:ip:${ip}`, rl.limit, rl.windowSeconds);
}

/** Rate limit per member (UUID opak, bukan PII). */
export async function limitByMember(limiter: RateLimiter, config: AppConfig, name: OnlineRateLimitName, memberId: string): Promise<void> {
  const rl = config.onlineRateLimit(name);
  await limiter.enforce(`online:${name.toLowerCase()}:m:${memberId}`, rl.limit, rl.windowSeconds);
}

/**
 * SEC-134 (L-45): kuota promo BERBOBOT — satu quote dengan n kode mengonsumsi n unit di limiter member DAN IP
 * (`PROMO_MEMBER` 10 / `PROMO_IP` 30 per 15 menit = jumlah KODE, bukan jumlah quote). Permintaan yang ditolak tidak
 * memakan kuota (kompensasi `RateLimiter.consume`); bila IP menolak setelah member lolos, unit member dikembalikan agar
 * kedua counter konsisten. Redis gagal → error dilempar (fail closed). Key sama dengan versi per-quote (`online:promo_*`).
 */
export async function consumePromoCodeQuota(
  limiter: RateLimiter,
  config: AppConfig,
  memberId: string,
  req: FastifyRequest,
  codeCount: number,
): Promise<void> {
  if (codeCount <= 0) return;
  const m = config.onlineRateLimit('PROMO_MEMBER');
  const memberKey = `online:promo_member:m:${memberId}`;
  const byMember = await limiter.consume(memberKey, codeCount, m.limit, m.windowSeconds);
  if (!byMember.allowed) throw Errors.rateLimited(byMember.retryAfterSeconds);
  const i = config.onlineRateLimit('PROMO_IP');
  const ip = redisKeyHash(config.redisKeyPepper, clientIp(req) ?? 'unknown');
  let byIp: RateLimitResult;
  try {
    byIp = await limiter.consume(`online:promo_ip:ip:${ip}`, codeCount, i.limit, i.windowSeconds);
  } catch (err) {
    await limiter.release(memberKey, codeCount, m.windowSeconds).catch(() => undefined);
    throw err;
  }
  if (!byIp.allowed) {
    await limiter.release(memberKey, codeCount, m.windowSeconds);
    throw Errors.rateLimited(byIp.retryAfterSeconds);
  }
}

export function noStore(reply: FastifyReply): void {
  void reply.header('Cache-Control', 'no-store');
}
