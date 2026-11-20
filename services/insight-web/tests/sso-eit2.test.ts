/** POST /sso dengan eit2 dan transisi INSIGHT_ACCEPT_EIT1 (docs/04 §28.13.5). */
import { describe, expect, it } from 'vitest';
import { JtiCache } from '@/lib/jti-cache';
import { RateLimiter } from '@/lib/rate-limit';
import { SESSION_COOKIE, verifySessionValue } from '@/lib/session';
import { handleSso, type SsoDeps } from '@/lib/sso-handler';
import { VECTOR_TOKEN } from './helpers';
import { IAT, KEY, TOKEN_A, TOKEN_B } from './helpers-v2';

const SESSION_KEY = 'cd'.repeat(32);
const ODOO = 'https://erp.test:9443';
const NOW_MS = (IAT + 10) * 1000;

const deps = (over: Partial<SsoDeps> = {}): SsoDeps => ({
  enabled: true,
  odooOrigin: ODOO,
  odooOpenUrl: `${ODOO}/efn/insight/open`,
  ssoKey: KEY,
  sessionKey: SESSION_KEY,
  nowMs: () => NOW_MS,
  jti: new JtiCache(),
  limiter: new RateLimiter(10, 60_000),
  ...over,
});
const post = (token: string): Request =>
  new Request('https://insight.test/sso', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: ODOO },
    body: new URLSearchParams({ t: token }).toString(),
  });
const cookieValue = (res: Response): string => (res.headers.get('set-cookie') ?? '').split(';')[0].slice(`${SESSION_COOKIE}=`.length);

describe('POST /sso eit2', () => {
  it('eit2 -> 303 + cookie eis2 ber-wids/persona; token tidak ada di respons', async () => {
    const res = await handleSso(post(TOKEN_B), deps());
    expect(res.status).toBe(303);
    expect(res.headers.get('location')).toBe('/');
    const cookie = res.headers.get('set-cookie') ?? '';
    expect(cookie.startsWith(`${SESSION_COOKIE}=eis2.`)).toBe(true);
    expect(cookie).not.toContain(TOKEN_B);
    for (const attr of ['HttpOnly', 'Secure', 'SameSite=Lax', 'Path=/', 'Max-Age=28800']) expect(cookie).toContain(attr);
    expect(verifySessionValue(cookieValue(res), SESSION_KEY, IAT + 10)).toMatchObject({ v: 2, sub: 9, wids: [22], roles: ['store', 'user'] });
  });
  it('eit2 replay -> 401 generik', async () => {
    const d = deps();
    expect((await handleSso(post(TOKEN_A), d)).status).toBe(303);
    expect((await handleSso(post(TOKEN_A), d)).status).toBe(401);
  });
  it('eit1 diterima bila flag true/tidak diisi, ditolak 401 generik bila false', async () => {
    expect((await handleSso(post(VECTOR_TOKEN), deps())).status).toBe(303);
    expect((await handleSso(post(VECTOR_TOKEN), deps({ acceptEit1: true }))).status).toBe(303);
    const off = await handleSso(post(VECTOR_TOKEN), deps({ acceptEit1: false }));
    expect(off.status).toBe(401);
    expect(off.headers.get('set-cookie')).toBeNull();
    expect(await off.text()).not.toContain('eit1');
    expect((await handleSso(post(TOKEN_B), deps({ acceptEit1: false }))).status).toBe(303);
  });
});
