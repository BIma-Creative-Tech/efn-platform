import Fastify, { FastifyInstance } from 'fastify';
import { testConfig } from '../testing/fakes';
import { clientIp } from './client-ip';

/**
 * SEC-03: req.ip memakai X-Forwarded-For HANYA dari hop tepercaya (TRUSTED_PROXIES = subnet docker + Cloudflare).
 * Diuji dengan instance Fastify sungguhan (proxy-addr), opsi trustProxy persis seperti main.ts.
 */
const TRUSTED = '127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7,173.245.48.0/20,2400:cb00::/32';

async function app(): Promise<FastifyInstance> {
  const f = Fastify({ trustProxy: testConfig({ TRUSTED_PROXIES: TRUSTED }).trustProxy });
  f.get('/ip', async (req) => ({ ip: clientIp(req) }));
  await f.ready();
  return f;
}

async function ipOf(f: FastifyInstance, remoteAddress: string, xff?: string): Promise<string | null> {
  const res = await f.inject({
    method: 'GET',
    url: '/ip',
    remoteAddress,
    headers: xff === undefined ? {} : { 'x-forwarded-for': xff },
  });
  return (res.json() as { ip: string | null }).ip;
}

describe('clientIp + TRUSTED_PROXIES (SEC-03)', () => {
  let f: FastifyInstance;
  beforeAll(async () => {
    f = await app();
  });
  afterAll(async () => {
    await f.close();
  });

  it('XFF palsu langsung dari internet (origin tanpa Cloudflare) diabaikan', async () => {
    expect(await ipOf(f, '203.0.113.9', '1.2.3.4')).toBe('203.0.113.9');
  });

  it('klien → Cloudflare → Traefik (docker) → BFF: IP klien asli', async () => {
    // Traefik menambahkan IP edge CF; XFF = "klien, edge-CF"; socket = Traefik (172.18.x)
    expect(await ipOf(f, '172.18.0.5', '198.51.100.20, 173.245.48.10')).toBe('198.51.100.20');
  });

  it('klien memalsukan XFF lewat Cloudflare: entri tambahan di kiri tidak dipercaya', async () => {
    expect(await ipOf(f, '172.18.0.5', '6.6.6.6, 198.51.100.20, 173.245.48.10')).toBe('198.51.100.20');
  });

  it('Next.js (docker) meneruskan XFF dari Traefik: tetap IP klien', async () => {
    expect(await ipOf(f, '172.18.0.7', '198.51.100.20, 173.245.48.10, 172.18.0.5')).toBe('198.51.100.20');
  });

  it('IPv6 via Cloudflare & IPv4-mapped dinormalisasi', async () => {
    expect(await ipOf(f, '172.18.0.5', '2001:db8::1, 2400:cb00::10')).toBe('2001:db8::1');
    expect(await ipOf(f, '::ffff:203.0.113.9')).toBe('203.0.113.9');
  });

  it('XFF dari IP yang mengaku Cloudflare tetapi di luar daftar → diabaikan', async () => {
    expect(await ipOf(f, '104.16.0.1', '1.2.3.4')).toBe('104.16.0.1');
  });
});
