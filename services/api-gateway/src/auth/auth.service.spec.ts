import { sign } from 'jsonwebtoken';
import { MemberService } from '../common/member/member.service';
import { MetricsService } from '../common/metrics/metrics.service';
import { PII_AAD } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AccessTokenService } from '../common/security/access-token.service';
import { JwtAuthGuard } from '../common/security/jwt-auth.guard';
import {
  FakeClock,
  FakeDb,
  FakeKvStore,
  FakeMemberTable,
  FakeWhatsAppSender,
  catchApiError,
  httpContext,
  testCipher,
  testConfig,
} from '../common/testing/fakes';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { AltchaService, solveAltcha } from './altcha.service';
import { AuthService } from './auth.service';
import { parseAuthOtpRequest } from './dto/auth.dto';

const PHONE = '6281234567890';
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function setup(overrides: Record<string, string> = {}) {
  const clock = new FakeClock();
  // maxnumber kecil agar solveAltcha cepat di test
  const config = testConfig({ ALTCHA_MAX_NUMBER: '1000', ...overrides });
  const kv = new FakeKvStore(clock.now);
  const members = new FakeMemberTable();
  const db = new FakeDb(
    (text, params) =>
      members.handle(text, params) ?? (text.includes('SUM(delta_points)') ? { rows: [{ balance: 1200 }], rowCount: 1 } : undefined),
  );
  const cipher = testCipher(config);
  const sender = new FakeWhatsAppSender();
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  const otp = new OtpService(config, kv, db, sender, new MetricsService(), limiter, cipher);
  otp.now = clock.now;
  const tokens = new AccessTokenService(config);
  tokens.now = clock.now;
  const loyalty = new LoyaltyService(config, db, cipher);
  loyalty.now = clock.now;
  const altcha = new AltchaService(config, kv);
  altcha.now = clock.now;
  const memberService = new MemberService(config, db, cipher);
  const auth = new AuthService(config, otp, limiter, tokens, loyalty, altcha, memberService);
  const captcha = (ip: string | null = null): string => solveAltcha(altcha.createChallenge(ip));
  const request = (phone: string, ip: string | null) => auth.requestOtp({ phone, altcha: captcha(ip) }, ip);
  return { clock, config, kv, sender, tokens, auth, altcha, members, memberService, cipher, captcha, request };
}

describe('AuthService', () => {
  it('OTP login → member opak (sub = UUID, bukan HP) → token lolos JwtAuthGuard; /me ter-mask', async () => {
    const ctx = setup();
    const req = await ctx.request(PHONE, '203.0.113.7');
    const res = await ctx.auth.verifyOtp({ challengeId: req.challenge_id, code: ctx.sender.lastCode() }, '203.0.113.7');
    expect(res.token_type).toBe('Bearer');
    expect(res.expires_in).toBe(3600);

    const payload = JSON.parse(Buffer.from(res.access_token.split('.')[1], 'base64url').toString('utf8')) as { sub: string };
    expect(payload.sub).toMatch(UUID_RE);
    expect(res.access_token).not.toContain(PHONE);
    expect(JSON.stringify(payload)).not.toContain(PHONE);

    const row = ctx.members.rows.get(payload.sub)!;
    expect(row.member_hash).toBe(memberHash(ctx.config.memberHashPepper, PHONE));
    expect(ctx.cipher.decrypt(row.member_ref_enc, PII_AAD.MEMBER_REF)).toBe(PHONE);

    const guard = new JwtAuthGuard(ctx.tokens, ctx.memberService);
    const httpReq: { headers: Record<string, string>; user?: unknown } = { headers: { authorization: `Bearer ${res.access_token}` } };
    expect(await guard.canActivate(httpContext(httpReq))).toBe(true);
    expect(httpReq.user).toEqual({ memberId: payload.sub, memberHash: row.member_hash });

    expect(await ctx.auth.me({ memberId: payload.sub, memberHash: row.member_hash })).toEqual({
      member_ref_masked: '62812****890',
      points_balance: 1200,
    });
  });

  it('login kedua dengan nomor sama → member yang sama (upsert)', async () => {
    const ctx = setup();
    const ids: string[] = [];
    for (let i = 0; i < 2; i += 1) {
      const req = await ctx.request(PHONE, '203.0.113.7');
      const res = await ctx.auth.verifyOtp({ challengeId: req.challenge_id, code: ctx.sender.lastCode() }, '203.0.113.7');
      ids.push((ctx.tokens.verify(res.access_token) as { memberId: string }).memberId);
    }
    expect(ids[0]).toBe(ids[1]);
    expect(ctx.members.rows.size).toBe(1);
  });

  it('member sedang dihapus (deleted_at) → verify 409 account_deleting dan token lama ditolak guard', async () => {
    const ctx = setup();
    const req = await ctx.request(PHONE, null);
    const res = await ctx.auth.verifyOtp({ challengeId: req.challenge_id, code: ctx.sender.lastCode() }, null);
    const id = ctx.tokens.verify(res.access_token)!.memberId;
    ctx.members.rows.get(id)!.deleted_at = new Date();

    const guard = new JwtAuthGuard(ctx.tokens, ctx.memberService);
    const err = await catchApiError(() => guard.canActivate(httpContext({ headers: { authorization: `Bearer ${res.access_token}` } })));
    expect(err.getStatus()).toBe(401);

    const req2 = await ctx.request(PHONE, null);
    const err2 = await catchApiError(() => ctx.auth.verifyOtp({ challengeId: req2.challenge_id, code: ctx.sender.lastCode() }, null));
    expect(err2.getStatus()).toBe(409);
    expect(err2.body).toEqual({ error: { code: 'account_deleting', message: expect.any(String) } });
  });

  it('kode salah → 400 { ok:false, reason, attempts_left }', async () => {
    const ctx = setup();
    const req = await ctx.request(PHONE, null);
    const bad = ctx.sender.lastCode() === '000000' ? '111111' : '000000';
    const err = await catchApiError(() => ctx.auth.verifyOtp({ challengeId: req.challenge_id, code: bad }, null));
    expect(err.getStatus()).toBe(400);
    expect(err.body).toEqual({ ok: false, reason: 'invalid', attempts_left: 2 });
  });

  it('rate limit per IP klien: 20 / 15 menit untuk nomor berbeda', async () => {
    const ctx = setup();
    for (let i = 0; i < 20; i += 1) {
      await ctx.request(`62812000000${String(i).padStart(2, '0')}`, '198.51.100.9');
    }
    const err = await catchApiError(() => ctx.request('6289999999999', '198.51.100.9'));
    expect(err.getStatus()).toBe(429);
    await expect(ctx.request('6289999999999', '198.51.100.10')).resolves.toBeDefined();
  });

  it('SEC-24: nama key Redis tidak memuat IP maupun nomor HP mentah', async () => {
    const ctx = setup();
    await ctx.request(PHONE, '198.51.100.9');
    await ctx.auth.challenge('198.51.100.9');
    const keys = ctx.kv.keys();
    expect(keys.some((k) => k.startsWith('rl:otp:login:ip:'))).toBe(true);
    for (const k of keys) {
      expect(k).not.toContain('198.51.100.9');
      expect(k).not.toContain(PHONE);
    }
    for (const k of keys.filter((x) => x.startsWith('otp:'))) expect(JSON.stringify(ctx.kv.raw(k))).not.toContain(PHONE);
  });

  it('nomor format lokal 08xx dinormalisasi; nomor tidak valid → 400 invalid_phone; altcha hilang → 400 captcha_required', async () => {
    expect(parseAuthOtpRequest({ phone: '0812-3456-7890', altcha: 'x' })).toEqual({ phone: '6281234567890', altcha: 'x' });
    let err = await catchApiError(() => parseAuthOtpRequest({ phone: '12', altcha: 'x' }));
    expect(err.body).toEqual({ error: { code: 'invalid_phone', message: expect.any(String) } });
    err = await catchApiError(() => parseAuthOtpRequest({ phone: PHONE }));
    expect(err.getStatus()).toBe(400);
    expect(err.body).toEqual({ error: { code: 'captcha_required', message: expect.any(String) } });
  });

  it.each([['+1 415 555 0100'], ['+44 7700 900123'], ['+65 8123 4567']])(
    'SEC-06: nomor internasional %s → 400 invalid_phone (tidak ada OTP terkirim)',
    async (phone) => {
      const err = await catchApiError(() => parseAuthOtpRequest({ phone, altcha: 'x' }));
      expect(err.body).toEqual({ error: { code: 'invalid_phone', message: expect.any(String) } });
    },
  );

  it('§8.1: captcha tidak valid / dipakai ulang → 400 captcha_required, OTP tidak terkirim, kuota tidak terpakai', async () => {
    const ctx = setup();
    const err = await catchApiError(() => ctx.auth.requestOtp({ phone: PHONE, altcha: 'bm90LWpzb24=' }, '203.0.113.7'));
    expect(err.body).toEqual({ error: { code: 'captcha_required', message: expect.any(String) } });
    const solved = ctx.captcha('203.0.113.7');
    await ctx.auth.requestOtp({ phone: PHONE, altcha: solved }, '203.0.113.7');
    const replay = await catchApiError(() => ctx.auth.requestOtp({ phone: PHONE, altcha: solved }, '203.0.113.7'));
    expect(replay.body).toEqual({ error: { code: 'captcha_required', message: expect.any(String) } });
    expect(ctx.sender.otps).toHaveLength(1);
    expect(ctx.kv.keys().filter((k) => k.startsWith('rl:otp:login:ip:')).map((k) => ctx.kv.raw(k))).toEqual(['1']);
  });

  it('SEC-06 lockout tertarget: penyerang dari IP lain tidak menghabiskan kuota korban', async () => {
    const ctx = setup();
    const ATTACKER = '198.51.100.66';
    const VICTIM_IP = '203.0.113.7';
    const victim = await ctx.request(PHONE, VICTIM_IP);
    const victimCode = ctx.sender.lastCode();
    for (let i = 0; i < 3; i += 1) await ctx.request(PHONE, ATTACKER);
    const err = await catchApiError(() => ctx.request(PHONE, ATTACKER));
    expect(err.getStatus()).toBe(429);
    await expect(ctx.request(PHONE, VICTIM_IP)).resolves.toBeDefined();
    const res = await ctx.auth.verifyOtp({ challengeId: victim.challenge_id, code: victimCode }, VICTIM_IP);
    expect(res.token_type).toBe('Bearer');
  });

  it('SEC-06 plafon per nomor lintas IP (anti spam WA): 10/jam', async () => {
    const ctx = setup();
    for (let i = 0; i < 10; i += 1) await ctx.request(PHONE, `198.51.100.${i + 1}`);
    const err = await catchApiError(() => ctx.request(PHONE, '198.51.100.200'));
    expect(err.getStatus()).toBe(429);
    expect(ctx.sender.otps).toHaveLength(10);
  });
});

describe('JwtAuthGuard / AccessTokenService', () => {
  it('menolak token kedaluwarsa, salah secret, alg none, sub nomor HP (format lama), step-up sebagai access, header tanpa Bearer', async () => {
    const ctx = setup();
    const id = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
    ctx.members.rows.set(id, { id, member_hash: 'a'.repeat(64), member_ref_enc: Buffer.alloc(40), created_at: new Date(), deleted_at: null });
    const guard = new JwtAuthGuard(ctx.tokens, ctx.memberService);
    const { accessToken } = ctx.tokens.issue(id);
    const now = Math.floor(ctx.clock.ms / 1000);
    const opts = { algorithm: 'HS256' as const, issuer: 'efn-bff', audience: 'efn-superapp' };
    const claims = { sub: id, typ: 'access', iat: now, exp: now + 3600 };
    const wrongSecret = sign(claims, 'secret-lain-yang-tidak-sah-0123456789', opts);
    const none = `${Buffer.from('{"alg":"none","typ":"JWT"}').toString('base64url')}.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.`;
    const phoneSub = sign({ ...claims, sub: PHONE }, ctx.config.jwtSecret, opts);
    const stepUp = ctx.tokens.issueStepUp(id).token;

    const ok = { headers: { authorization: `Bearer ${accessToken}` } };
    expect(await guard.canActivate(httpContext(ok))).toBe(true);

    for (const authorization of [`Bearer ${wrongSecret}`, `Bearer ${none}`, `Bearer ${phoneSub}`, `Bearer ${stepUp}`, accessToken, 'Basic abc', '']) {
      const err = await catchApiError(() => guard.canActivate(httpContext({ headers: { authorization } })));
      expect(err.getStatus()).toBe(401);
    }

    expect(ctx.tokens.verifyStepUp(stepUp, id)).toBe(true);
    expect(ctx.tokens.verifyStepUp(stepUp, '00000000-0000-4000-8000-000000000000')).toBe(false);
    expect(ctx.tokens.verifyStepUp(accessToken, id)).toBe(false);

    ctx.clock.advanceSeconds(3601);
    const expired = await catchApiError(() => guard.canActivate(httpContext(ok)));
    expect(expired.getStatus()).toBe(401);
  });

  it('SEC-34: solusi ALTCHA terikat IP — dipakai dari IP lain → 400 captcha_required, OTP tidak terkirim', async () => {
    const ctx = setup();
    const solved = ctx.captcha('203.0.113.7');
    const err = await catchApiError(() => ctx.auth.requestOtp({ phone: PHONE, altcha: solved }, '198.51.100.66'));
    expect(err.body).toEqual({ error: { code: 'captcha_required', message: expect.any(String) } });
    expect(ctx.sender.otps).toHaveLength(0);
    // IP asli tetap bisa memakai solusinya (kegagalan IP lain tidak menandai used)
    await expect(ctx.auth.requestOtp({ phone: PHONE, altcha: solved }, '203.0.113.7')).resolves.toBeDefined();
  });

  it('SEC-34: challenge lewat AuthService terikat IP pemanggil dan bisa dipakai IP yang sama', async () => {
    const ctx = setup();
    const ch = await ctx.auth.challenge('203.0.113.7');
    expect(ch.maxnumber).toBe(1000);
    await expect(ctx.auth.requestOtp({ phone: PHONE, altcha: solveAltcha(ch) }, '203.0.113.7')).resolves.toBeDefined();
  });

  it('SEC-34: biaya adaptif naik bila OTP per IP tinggi; IP lain tetap biaya dasar; dibatasi cap', async () => {
    const ctx = setup({ ALTCHA_MAX_NUMBER_CAP: '4000' });
    const IP = '198.51.100.9';
    expect((await ctx.auth.challenge(IP)).maxnumber).toBe(1000);
    // 5/20 OTP per IP = 25% → level 1
    for (let i = 0; i < 5; i += 1) await ctx.request(`62812000000${String(i).padStart(2, '0')}`, IP);
    expect((await ctx.auth.challenge(IP)).maxnumber).toBe(2000);
    // 15/20 = 75% → level 3 → 8000 dipotong cap 4000
    for (let i = 5; i < 15; i += 1) await ctx.request(`62812000000${String(i).padStart(2, '0')}`, IP);
    expect((await ctx.auth.challenge(IP)).maxnumber).toBe(4000);
    expect((await ctx.auth.challenge('203.0.113.50')).maxnumber).toBe(1000);
    // window berikutnya → kembali ke dasar
    ctx.clock.advanceSeconds(900);
    expect((await ctx.auth.challenge(IP)).maxnumber).toBe(1000);
  });

  it('SEC-34: tekanan global (budget OTP login) menaikkan biaya untuk semua IP; ALTCHA_ADAPTIVE=false mematikan', async () => {
    const ctx = setup({ OTP_GLOBAL_HOURLY_BUDGET: '10' });
    for (let i = 0; i < 5; i += 1) await ctx.request(`62812000001${String(i).padStart(2, '0')}`, `203.0.113.${10 + i}`);
    expect((await ctx.auth.challenge('192.0.2.1')).maxnumber).toBe(2000); // 50% budget → level 1
    const off = setup({ OTP_GLOBAL_HOURLY_BUDGET: '10', ALTCHA_ADAPTIVE: 'false' });
    for (let i = 0; i < 9; i += 1) await off.request(`62812000001${String(i).padStart(2, '0')}`, `203.0.113.${10 + i}`);
    expect((await off.auth.challenge('192.0.2.1')).maxnumber).toBe(1000);
  });
});
