import { randomBytes, randomUUID } from 'node:crypto';
import { signUnsubscribeToken, verifyUnsubscribeToken } from './unsubscribe-token';

const NOW = Date.UTC(2026, 10, 20, 3, 0, 0);

describe('token berhenti berlangganan (HMAC, sekali pakai di service)', () => {
  const key = randomBytes(32);
  const member = randomUUID();

  it('round-trip: klaim member + jti + exp', () => {
    const t = signUnsubscribeToken(key, member, NOW, 3600);
    expect(t.startsWith('eu1.')).toBe(true);
    const c = verifyUnsubscribeToken(key, t, NOW + 1000);
    expect(c).toMatchObject({ memberId: member, exp: Math.floor(NOW / 1000) + 3600 });
  });

  it('kunci lain / payload diubah / tanda tangan dipotong → null', () => {
    const t = signUnsubscribeToken(key, member, NOW, 3600);
    expect(verifyUnsubscribeToken(randomBytes(32), t, NOW)).toBeNull();
    const [p, payload, sig] = t.split('.');
    const forged = Buffer.from(JSON.stringify({ v: 1, m: randomUUID(), j: randomUUID(), e: 9_999_999_999 })).toString('base64url');
    expect(verifyUnsubscribeToken(key, `${p}.${forged}.${sig}`, NOW)).toBeNull();
    expect(verifyUnsubscribeToken(key, `${p}.${payload}.${sig.slice(0, 20)}`, NOW)).toBeNull();
    expect(verifyUnsubscribeToken(key, `eu2.${payload}.${sig}`, NOW)).toBeNull();
  });

  it('kedaluwarsa → null; input bukan string / terlalu panjang → null', () => {
    const t = signUnsubscribeToken(key, member, NOW, 60);
    expect(verifyUnsubscribeToken(key, t, NOW + 61_000)).toBeNull();
    expect(verifyUnsubscribeToken(key, 123, NOW)).toBeNull();
    expect(verifyUnsubscribeToken(key, 'eu1.' + 'a'.repeat(600), NOW)).toBeNull();
  });
});
