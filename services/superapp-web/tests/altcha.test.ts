import { describe, expect, it } from 'vitest';
import { ALTCHA_PAYLOAD_MAX_LEN, isPlausibleAltchaPayload, sanitizeAltchaChallenge } from '../lib/altcha';

const HEX64 = 'a'.repeat(64);
const b64 = (o: unknown) => Buffer.from(JSON.stringify(o), 'utf8').toString('base64');

const goodPayload = {
  algorithm: 'SHA-256',
  challenge: HEX64,
  number: 12345,
  salt: '0f1e2d3c4b5a?expires=1791000000',
  signature: 'b'.repeat(64),
  took: 830,
};

describe('isPlausibleAltchaPayload', () => {
  it('menerima payload widget yang wajar', () => {
    expect(isPlausibleAltchaPayload(b64(goodPayload))).toBe(true);
  });

  it.each([
    ['bukan string', 123],
    ['kosong', ''],
    ['bukan base64', '!!!!not-base64!!!!'],
    ['base64 bukan JSON', Buffer.from('hello world, bukan json').toString('base64')],
    ['JSON array', b64([1, 2, 3])],
    ['algoritma asing', b64({ ...goodPayload, algorithm: 'MD5' })],
    ['challenge bukan hex', b64({ ...goodPayload, challenge: 'zz'.repeat(32) })],
    ['number negatif', b64({ ...goodPayload, number: -1 })],
    ['number pecahan', b64({ ...goodPayload, number: 1.5 })],
    ['number string', b64({ ...goodPayload, number: '1' })],
    ['tanpa signature', b64({ ...goodPayload, signature: undefined })],
    ['salt kosong', b64({ ...goodPayload, salt: '' })],
  ])('menolak: %s', (_label, value) => {
    expect(isPlausibleAltchaPayload(value)).toBe(false);
  });

  it('menolak payload terlalu panjang', () => {
    const big = b64({ ...goodPayload, salt: 'x'.repeat(ALTCHA_PAYLOAD_MAX_LEN) });
    expect(big.length).toBeGreaterThan(ALTCHA_PAYLOAD_MAX_LEN);
    expect(isPlausibleAltchaPayload(big)).toBe(false);
  });
});

describe('sanitizeAltchaChallenge', () => {
  const good = { algorithm: 'SHA-256', challenge: HEX64, salt: 'abc?expires=1', signature: 'c'.repeat(64), maxnumber: 100000 };

  it('meneruskan hanya field yang dikenal', () => {
    expect(sanitizeAltchaChallenge({ ...good, extra: '<script>' })).toEqual(good);
  });

  it('maxnumber opsional', () => {
    const noMax = { algorithm: good.algorithm, challenge: good.challenge, salt: good.salt, signature: good.signature };
    expect(sanitizeAltchaChallenge(noMax)).toEqual(noMax);
  });

  it.each([
    null,
    [],
    'string',
    { ...good, algorithm: 'SHA-1' },
    { ...good, challenge: 'xyz' },
    { ...good, signature: '' },
    { ...good, salt: 1 },
    { ...good, maxnumber: -5 },
    { ...good, maxnumber: '1000' },
  ])('menolak %j', (v) => {
    expect(sanitizeAltchaChallenge(v)).toBeNull();
  });
});
