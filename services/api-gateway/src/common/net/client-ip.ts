import { isIP } from 'node:net';

/**
 * IP klien untuk rate limit & audit. `req.ip` di Fastify sudah memperhitungkan X-Forwarded-For
 * HANYA dari hop yang dipercaya (`trustProxy` = subnet privat docker/Traefik/Next.js, lihat
 * AppConfig.trustProxy). Dengan begitu header XFF palsu dari internet tidak berpengaruh.
 */
export function clientIp(req: { ip?: string }): string | null {
  const raw = req.ip;
  if (!raw) return null;
  const ip = raw.startsWith('::ffff:') ? raw.slice(7) : raw;
  return isIP(ip) ? ip : null;
}
