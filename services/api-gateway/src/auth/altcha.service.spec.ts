import { createHmac } from 'node:crypto';
import { FakeClock, FakeKvStore, testConfig } from '../common/testing/fakes';
import { MetricsService } from '../common/metrics/metrics.service';
import {
  AltchaChallenge,
  AltchaService,
  adaptiveAltchaMaxNumber,
  altchaIpBinding,
  altchaPressureLevel,
  sha256Hex,
  solveAltcha,
} from './altcha.service';

const IP = '203.0.113.7';

function setup(overrides: Record<string, string> = {}) {
  const clock = new FakeClock();
  const config = testConfig({ ALTCHA_MAX_NUMBER: '1000', ...overrides });
  const kv = new FakeKvStore(clock.now);
  const altcha = new AltchaService(config, kv);
  altcha.now = clock.now;
  return { clock, config, kv, altcha };
}

function encode(o: Record<string, unknown>): string {
  return Buffer.from(JSON.stringify(o)).toString('base64');
}

function decode(p: string): Record<string, unknown> {
  return JSON.parse(Buffer.from(p, 'base64').toString('utf8')) as Record<string, unknown>;
}

describe('AltchaService (docs/04 §8.1)', () => {
  it('challenge berbentuk standar {algorithm, challenge, salt, signature, maxnumber}; salt memuat expires & diakhiri &', () => {
    const { altcha, clock, config } = setup();
    const c = altcha.createChallenge(IP);
    expect(Object.keys(c).sort()).toEqual(['algorithm', 'challenge', 'maxnumber', 'salt', 'signature']);
    expect(c.algorithm).toBe('SHA-256');
    expect(c.maxnumber).toBe(1000);
    expect(c.salt).toMatch(/^[0-9a-f]{24}\?expires=\d{10}&$/);
    expect(Number(/expires=(\d+)/.exec(c.salt)![1])).toBe(Math.floor(clock.ms / 1000) + 300);
    // SEC-34: signature = HMAC(domain v2 + challenge + pengikat IP); IP tidak muncul di challenge/salt
    expect(c.signature).toBe(createHmac('sha256', config.altchaHmacKey).update(`efn-altcha:v2\n${c.challenge}\n${IP}`).digest('hex'));
    expect(JSON.stringify(c)).not.toContain(IP);
  });

  it('payload valid diterima SEKALI (single-use di Redis altcha:used:<sig>)', async () => {
    const { altcha, kv } = setup();
    const c = altcha.createChallenge(IP);
    const p = solveAltcha(c);
    expect(await altcha.verify(p, IP)).toBe(true);
    expect(kv.keys()).toContain(`altcha:used:${c.signature}`);
    expect(await altcha.verify(p, IP)).toBe(false);
  });

  it('kedaluwarsa setelah TTL 5 menit → ditolak', async () => {
    const { altcha, clock } = setup();
    const p = solveAltcha(altcha.createChallenge(IP));
    clock.advanceSeconds(301);
    expect(await altcha.verify(p, IP)).toBe(false);
  });

  it('number salah, signature palsu, salt diubah, algoritma lain, bukan base64/JSON → ditolak', async () => {
    const { altcha } = setup();
    const c: AltchaChallenge = altcha.createChallenge(IP);
    const good = decode(solveAltcha(c));
    const n = good.number as number;

    expect(await altcha.verify(encode({ ...good, number: n === 0 ? 1 : n - 1 }), IP)).toBe(false);
    expect(await altcha.verify(encode({ ...good, signature: 'f'.repeat(64) }), IP)).toBe(false);
    expect(await altcha.verify(encode({ ...good, algorithm: 'SHA-512' }), IP)).toBe(false);
    // Penyerang membuat challenge sendiri dengan expires jauh di depan: signature tidak cocok
    const salt = `${'0'.repeat(24)}?expires=9999999999&`;
    const forged = { ...good, salt, challenge: sha256Hex(`${salt}1`), number: 1 };
    expect(await altcha.verify(encode(forged), IP)).toBe(false);
    expect(await altcha.verify('%%%', IP)).toBe(false);
    expect(await altcha.verify(Buffer.from('bukan json').toString('base64'), IP)).toBe(false);
    expect(await altcha.verify(undefined, IP)).toBe(false);
    // salt tanpa '&' penutup (salt splicing) → ditolak meski signature diambil dari challenge asli
    expect(await altcha.verify(encode({ ...good, salt: (good.salt as string).slice(0, -1) }), IP)).toBe(false);
    // payload valid tetap bisa dipakai karena kegagalan di atas tidak menandai used
    expect(await altcha.verify(encode(good), IP)).toBe(true);
  });

  it('number sebagai string angka diterima; dengan nol di depan ditolak', async () => {
    const { altcha } = setup();
    const good = decode(solveAltcha(altcha.createChallenge(IP)));
    expect(await altcha.verify(encode({ ...good, number: `0${String(good.number)}` }), IP)).toBe(false);
    expect(await altcha.verify(encode({ ...good, number: String(good.number) }), IP)).toBe(true);
  });
});

describe('AltchaService — SEC-34 pengikatan IP & biaya adaptif', () => {
  it('payload dari IP lain / IP tidak diketahui ditolak; IP asli tetap bisa memakainya', async () => {
    const { altcha } = setup();
    const p = solveAltcha(altcha.createChallenge(IP));
    expect(await altcha.verify(p, '198.51.100.66')).toBe(false);
    expect(await altcha.verify(p, null)).toBe(false);
    expect(await altcha.verify(p, IP)).toBe(true);
  });

  it('IPv6: alamat lain dalam /64 yang sama diterima (alamat privasi), /64 lain ditolak', async () => {
    const { altcha } = setup();
    const p = solveAltcha(altcha.createChallenge('2001:db8:aa:bb::1'));
    expect(await altcha.verify(p, '2001:db8:aa:cc::1')).toBe(false);
    expect(await altcha.verify(p, '2001:0db8:00aa:00bb:1234:5678:9abc:def0')).toBe(true);
  });

  it('altchaIpBinding: IPv4 utuh, IPv6 /64 kanonik (termasuk ::, IPv4 tertanam), sampah → unknown', () => {
    expect(altchaIpBinding('203.0.113.7')).toBe('203.0.113.7');
    expect(altchaIpBinding('2001:db8::1')).toBe('2001:db8:0:0::/64');
    expect(altchaIpBinding('2001:DB8:0:0:ffff::')).toBe('2001:db8:0:0::/64');
    expect(altchaIpBinding('::1')).toBe('0:0:0:0::/64');
    expect(altchaIpBinding('64:ff9b::192.0.2.1')).toBe('64:ff9b:0:0::/64');
    expect(altchaIpBinding(null)).toBe('unknown');
    expect(altchaIpBinding('bukan-ip')).toBe('unknown');
  });

  it('createChallenge memakai maxnumber adaptif (dipotong ke 1000..cap); verify menerima number s.d. cap', async () => {
    const { altcha } = setup({ ALTCHA_MAX_NUMBER_CAP: '4000' });
    expect(altcha.createChallenge(IP, 2000).maxnumber).toBe(2000);
    expect(altcha.createChallenge(IP, 99_999_999).maxnumber).toBe(4000);
    expect(altcha.createChallenge(IP, 1).maxnumber).toBe(1000);
    const c = altcha.createChallenge(IP, 4000);
    expect(await altcha.verify(solveAltcha(c), IP)).toBe(true);
  });

  it('altchaPressureLevel: ambang per IP 25/50/75 %, global 50/75/90 %, ambil maksimum', () => {
    const base = { ipChallenges: 0, ipChallengeLimit: 120, ipOtpRequests: 0, ipOtpLimit: 20, globalOtpRequests: 0, globalOtpBudget: 1000 };
    expect(altchaPressureLevel(base)).toBe(0);
    expect(altchaPressureLevel({ ...base, ipChallenges: 29 })).toBe(0);
    expect(altchaPressureLevel({ ...base, ipChallenges: 30 })).toBe(1);
    expect(altchaPressureLevel({ ...base, ipOtpRequests: 10 })).toBe(2);
    expect(altchaPressureLevel({ ...base, ipOtpRequests: 20 })).toBe(3);
    expect(altchaPressureLevel({ ...base, globalOtpRequests: 499 })).toBe(0);
    expect(altchaPressureLevel({ ...base, globalOtpRequests: 750 })).toBe(2);
    expect(altchaPressureLevel({ ...base, globalOtpRequests: 950, ipOtpRequests: 5 })).toBe(3);
    expect(adaptiveAltchaMaxNumber(500_000, 4_000_000, 0)).toBe(500_000);
    expect(adaptiveAltchaMaxNumber(500_000, 4_000_000, 2)).toBe(2_000_000);
    expect(adaptiveAltchaMaxNumber(500_000, 3_000_000, 3)).toBe(3_000_000);
    expect(adaptiveAltchaMaxNumber(500_000, 4_000_000, 9)).toBe(4_000_000);
  });

  it('metrik efn_altcha_challenge_total{level} tanpa IP', async () => {
    const clock = new FakeClock();
    const metrics = new MetricsService();
    const altcha = new AltchaService(testConfig({ ALTCHA_MAX_NUMBER: '1000' }), new FakeKvStore(clock.now), metrics);
    altcha.createChallenge(IP, 2000, 1);
    const text = await metrics.render();
    expect(text).toContain('efn_altcha_challenge_total{level="1"} 1');
    expect(text).not.toContain(IP);
  });
});
