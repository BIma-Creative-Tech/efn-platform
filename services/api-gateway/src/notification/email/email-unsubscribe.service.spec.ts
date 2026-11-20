import { randomBytes, randomUUID } from 'node:crypto';
import { RateLimiter } from '../../common/rate-limit/rate-limiter';
import { FakeClock, FakeKvStore, catchApiError, testCipher, testConfig } from '../../common/testing/fakes';
import { ChannelEnv } from './channel-env';
import { ChannelTables } from './channel-testing';
import { EmailConfig } from './email.config';
import { EmailUnsubscribeService, parseUnsubscribeBody } from './email-unsubscribe.service';
import { signUnsubscribeToken } from './unsubscribe-token';

function setup(withKey = true, rate = '10') {
  const app = testConfig({ CONSENT_MARKETING_VERSION: 'marketing@2026-11-01', CONSENT_MARKETING_TEXT_SHA256: 'a'.repeat(64) });
  const cipher = testCipher(app);
  const clock = new FakeClock(Date.UTC(2026, 10, 20, 3, 0, 0));
  const t = new ChannelTables();
  const memberId = randomUUID();
  t.grantMarketing(memberId);
  const keyHex = randomBytes(32).toString('hex');
  const config = new EmailConfig(
    new ChannelEnv(
      { NODE_ENV: 'test', EMAIL_UNSUBSCRIBE_RATE_PER_MIN: rate, ...(withKey ? { EMAIL_UNSUBSCRIBE_KEY_FILE: '/s/k' } : {}) },
      (p) => {
        if (p === '/s/k') return Buffer.from(keyHex, 'utf8');
        throw new Error('ENOENT');
      },
    ),
  );
  const limiter = new RateLimiter(new FakeKvStore(clock.now));
  limiter.now = clock.now;
  const service = new EmailUnsubscribeService(config, app, t.db, cipher, limiter);
  service.now = clock.now;
  const token = signUnsubscribeToken(Buffer.from(keyHex, 'utf8'), memberId, clock.ms, 3600);
  return { t, memberId, service, token, clock, keyHex };
}

describe('POST /v1/email/unsubscribe (EmailUnsubscribeService)', () => {
  it('token valid → consent marketing dicabut (source email_unsubscribe, versi teks aktif, IP terenkripsi)', async () => {
    const ctx = setup();
    expect(await ctx.service.unsubscribe(ctx.token, '203.0.113.9')).toEqual({ status: 'unsubscribed' });
    expect(ctx.t.marketingGranted(ctx.memberId)).toBe(false);
    const last = ctx.t.consents[ctx.t.consents.length - 1];
    expect(last).toMatchObject({ member_id: ctx.memberId, granted: false, source: 'email_unsubscribe', version: 'marketing@2026-11-01', text_sha256: 'a'.repeat(64) });
    expect(Buffer.isBuffer(last.ip_enc)).toBe(true);
    expect(last.ip_enc!.toString('utf8')).not.toContain('203.0.113.9');
  });

  it('token dipakai ulang (replay) → 400 generik, tidak ada baris consent kedua', async () => {
    const ctx = setup();
    await ctx.service.unsubscribe(ctx.token, '203.0.113.9');
    const before = ctx.t.consents.length;
    const err = await catchApiError(() => ctx.service.unsubscribe(ctx.token, '203.0.113.9'));
    expect(err.getStatus()).toBe(400);
    expect(err.body).toEqual({ error: { code: 'unsubscribe_token_invalid', message: expect.any(String) } });
    expect(ctx.t.consents.length).toBe(before);
  });

  it('tanda tangan salah / kedaluwarsa → 400 yang sama; consent tidak berubah', async () => {
    const ctx = setup();
    const forged = signUnsubscribeToken(randomBytes(32), ctx.memberId, ctx.clock.ms, 3600);
    expect((await catchApiError(() => ctx.service.unsubscribe(forged, null))).getStatus()).toBe(400);
    ctx.clock.advanceSeconds(3601);
    expect((await catchApiError(() => ctx.service.unsubscribe(ctx.token, null))).getStatus()).toBe(400);
    expect(ctx.t.marketingGranted(ctx.memberId)).toBe(true);
  });

  it('rate limit per IP → 429 + Retry-After', async () => {
    const ctx = setup(true, '2');
    const bad = 'eu1.x.y';
    await catchApiError(() => ctx.service.unsubscribe(bad, '198.51.100.7'));
    await catchApiError(() => ctx.service.unsubscribe(bad, '198.51.100.7'));
    const err = await catchApiError(() => ctx.service.unsubscribe(ctx.token, '198.51.100.7'));
    expect(err.getStatus()).toBe(429);
    expect(err.headers['Retry-After']).toBeDefined();
  });

  it('kunci belum dikonfigurasi → 503', async () => {
    const ctx = setup(false);
    expect((await catchApiError(() => ctx.service.unsubscribe(ctx.token, null))).getStatus()).toBe(503);
  });

  it('body: hanya { token } string ≤ 512', () => {
    expect(parseUnsubscribeBody({ token: 'eu1.a.b' })).toBe('eu1.a.b');
    expect(() => parseUnsubscribeBody({ token: 'x', member_id: 'y' })).toThrow();
    expect(() => parseUnsubscribeBody({ token: 1 })).toThrow();
    expect(() => parseUnsubscribeBody({ token: 'a'.repeat(513) })).toThrow();
    expect(() => parseUnsubscribeBody(null)).toThrow();
  });
});
