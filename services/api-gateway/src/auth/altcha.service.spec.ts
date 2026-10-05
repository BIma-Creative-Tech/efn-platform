import { createHmac } from 'node:crypto';
import { FakeClock, FakeKvStore, testConfig } from '../common/testing/fakes';
import { AltchaChallenge, AltchaService, sha256Hex, solveAltcha } from './altcha.service';

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
    const c = altcha.createChallenge();
    expect(Object.keys(c).sort()).toEqual(['algorithm', 'challenge', 'maxnumber', 'salt', 'signature']);
    expect(c.algorithm).toBe('SHA-256');
    expect(c.maxnumber).toBe(1000);
    expect(c.salt).toMatch(/^[0-9a-f]{24}\?expires=\d{10}&$/);
    expect(Number(/expires=(\d+)/.exec(c.salt)![1])).toBe(Math.floor(clock.ms / 1000) + 300);
    expect(c.signature).toBe(createHmac('sha256', config.altchaHmacKey).update(c.challenge).digest('hex'));
  });

  it('payload valid diterima SEKALI (single-use di Redis altcha:used:<sig>)', async () => {
    const { altcha, kv } = setup();
    const c = altcha.createChallenge();
    const p = solveAltcha(c);
    expect(await altcha.verify(p)).toBe(true);
    expect(kv.keys()).toContain(`altcha:used:${c.signature}`);
    expect(await altcha.verify(p)).toBe(false);
  });

  it('kedaluwarsa setelah TTL 5 menit → ditolak', async () => {
    const { altcha, clock } = setup();
    const p = solveAltcha(altcha.createChallenge());
    clock.advanceSeconds(301);
    expect(await altcha.verify(p)).toBe(false);
  });

  it('number salah, signature palsu, salt diubah, algoritma lain, bukan base64/JSON → ditolak', async () => {
    const { altcha } = setup();
    const c: AltchaChallenge = altcha.createChallenge();
    const good = decode(solveAltcha(c));
    const n = good.number as number;

    expect(await altcha.verify(encode({ ...good, number: n === 0 ? 1 : n - 1 }))).toBe(false);
    expect(await altcha.verify(encode({ ...good, signature: 'f'.repeat(64) }))).toBe(false);
    expect(await altcha.verify(encode({ ...good, algorithm: 'SHA-512' }))).toBe(false);
    // Penyerang membuat challenge sendiri dengan expires jauh di depan: signature tidak cocok
    const salt = `${'0'.repeat(24)}?expires=9999999999&`;
    const forged = { ...good, salt, challenge: sha256Hex(`${salt}1`), number: 1 };
    expect(await altcha.verify(encode(forged))).toBe(false);
    expect(await altcha.verify('%%%')).toBe(false);
    expect(await altcha.verify(Buffer.from('bukan json').toString('base64'))).toBe(false);
    expect(await altcha.verify(undefined)).toBe(false);
    // salt tanpa '&' penutup (salt splicing) → ditolak meski signature diambil dari challenge asli
    expect(await altcha.verify(encode({ ...good, salt: (good.salt as string).slice(0, -1) }))).toBe(false);
    // payload valid tetap bisa dipakai karena kegagalan di atas tidak menandai used
    expect(await altcha.verify(encode(good))).toBe(true);
  });

  it('number sebagai string angka diterima; dengan nol di depan ditolak', async () => {
    const { altcha } = setup();
    const good = decode(solveAltcha(altcha.createChallenge()));
    expect(await altcha.verify(encode({ ...good, number: `0${String(good.number)}` }))).toBe(false);
    expect(await altcha.verify(encode({ ...good, number: String(good.number) }))).toBe(true);
  });
});
