import { createHmac } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { parseKey } from '@/lib/hmac';
import { verifySsoToken } from '@/lib/token';
import { IAT, KEY, makeToken, PAYLOAD_B64, PAYLOAD_JSON, SIG_HEX_DECODED_KEY, SIG_OK, VECTOR_TOKEN } from './helpers';

const NOW = IAT + 10;
const never = () => false;

describe('vektor uji bersama docs/04 §26.3', () => {
  it('payload_b64 pada vektor = base64url(payload JSON) (menjaga salah salin)', () => {
    expect(Buffer.from(PAYLOAD_JSON, 'ascii').toString('base64url')).toBe(PAYLOAD_B64);
  });

  it('sig kunci ASCII sama dengan vektor; sig kunci hex-decode sama dengan sig NEGATIF', () => {
    const asAscii = createHmac('sha256', Buffer.from(KEY, 'ascii')).update(`eit1.${PAYLOAD_B64}`).digest('base64url');
    const asHex = createHmac('sha256', Buffer.from(KEY, 'hex')).update(`eit1.${PAYLOAD_B64}`).digest('base64url');
    expect(asAscii).toBe(SIG_OK);
    expect(asHex).toBe(SIG_HEX_DECODED_KEY);
    expect(asHex).not.toBe(asAscii);
  });

  it('token vektor diterima', () => {
    const r = verifySsoToken(VECTOR_TOKEN, KEY, NOW, never);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.payload.sub).toBe(7);
      expect(r.payload.codes).toEqual(['BCF', 'PB']);
      expect(r.payload.cids).toEqual([3, 5]);
      expect(r.payload.roles).toEqual(['user']);
      expect(r.payload.name).toBe('Budi Uji');
    }
  });

  it('sig dari kunci hex-decode DITOLAK', () => {
    const r = verifySsoToken(`eit1.${PAYLOAD_B64}.${SIG_HEX_DECODED_KEY}`, KEY, NOW, never);
    expect(r).toEqual({ ok: false, code: 'bad_sig' });
  });
});

describe('verifySsoToken - penolakan', () => {
  it('kunci salah -> bad_sig', () => {
    expect(verifySsoToken(VECTOR_TOKEN, 'cd'.repeat(32), NOW, never)).toEqual({ ok: false, code: 'bad_sig' });
  });

  it('payload diubah -> bad_sig', () => {
    const [, , sig] = VECTOR_TOKEN.split('.');
    const forged = Buffer.from(PAYLOAD_JSON.replace('"sub":7', '"sub":8'), 'ascii').toString('base64url');
    expect(verifySsoToken(`eit1.${forged}.${sig}`, KEY, NOW, never)).toEqual({ ok: false, code: 'bad_sig' });
  });

  it('sig tidak kanonik (bit sisa base64url) -> bad_sig', () => {
    const last = SIG_OK[SIG_OK.length - 1];
    const alt = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
    // ganti karakter terakhir dengan karakter lain yang mendekode ke 32 byte yang sama (hanya 2 bit sisa berbeda)
    const idx = alt.indexOf(last);
    const sibling = alt[idx ^ 1];
    expect(verifySsoToken(`eit1.${PAYLOAD_B64}.${SIG_OK.slice(0, -1)}${sibling}`, KEY, NOW, never).ok).toBe(false);
  });

  it.each(['', 'abc', 'eit2.aaaaaaaaaaaaaaaa.' + 'a'.repeat(43), `eit1.short.${SIG_OK}`, `${VECTOR_TOKEN}x`, 'eit1..'])(
    'format salah -> bad_format (%s)',
    (t) => {
      expect(verifySsoToken(t, KEY, NOW, never)).toEqual({ ok: false, code: 'bad_format' });
    },
  );

  it('bukan string -> bad_format', () => {
    expect(verifySsoToken(null, KEY, NOW, never)).toEqual({ ok: false, code: 'bad_format' });
    expect(verifySsoToken(123, KEY, NOW, never)).toEqual({ ok: false, code: 'bad_format' });
  });

  it('kedaluwarsa (now >= exp) -> expired', () => {
    expect(verifySsoToken(VECTOR_TOKEN, KEY, IAT + 60, never)).toEqual({ ok: false, code: 'expired' });
    expect(verifySsoToken(VECTOR_TOKEN, KEY, IAT + 59, never).ok).toBe(true);
  });

  it('iat terlalu jauh di masa depan (> now + 30) -> expired', () => {
    expect(verifySsoToken(VECTOR_TOKEN, KEY, IAT - 31, never)).toEqual({ ok: false, code: 'expired' });
    expect(verifySsoToken(VECTOR_TOKEN, KEY, IAT - 30, never).ok).toBe(true);
  });

  it('umur token (exp - iat) > 60 atau < 1 -> expired', () => {
    expect(verifySsoToken(makeToken({ exp: IAT + 61 }), KEY, NOW, never)).toEqual({ ok: false, code: 'expired' });
    expect(verifySsoToken(makeToken({ exp: IAT }), KEY, NOW, never)).toEqual({ ok: false, code: 'expired' });
  });

  it('aud / iss / v salah -> bad_claims', () => {
    expect(verifySsoToken(makeToken({ aud: 'superapp-web' }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
    expect(verifySsoToken(makeToken({ iss: 'evil' }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
    expect(verifySsoToken(makeToken({ v: 2 }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
  });

  it('kunci ekstra atau kurang -> bad_claims', () => {
    expect(verifySsoToken(makeToken({ email: 'x@y.z' }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
    expect(verifySsoToken(makeToken({}, KEY, ['name']), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
  });

  it('jti ulang -> replay', () => {
    expect(verifySsoToken(VECTOR_TOKEN, KEY, NOW, (jti) => jti === '0123456789abcdef0123456789abcdef')).toEqual({
      ok: false,
      code: 'replay',
    });
  });

  it('jti bukan 32 hex kecil -> bad_claims', () => {
    expect(verifySsoToken(makeToken({ jti: 'ABCDEF0123456789ABCDEF0123456789' }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
  });

  it('codes asing / tak urut / duplikat / kosong -> bad_claims', () => {
    for (const codes of [['XX'], ['PB', 'BCF'], ['PB', 'PB'], [], ['pb']]) {
      expect(verifySsoToken(makeToken({ codes }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
    }
  });

  it('cids tak urut / nol / negatif / > 32 butir -> bad_claims', () => {
    for (const cids of [[5, 3], [0], [-1], Array.from({ length: 33 }, (_, i) => i + 1)]) {
      expect(verifySsoToken(makeToken({ cids }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
    }
  });

  it('roles user tanpa cids -> bad_claims; all_brands tanpa cids diterima', () => {
    expect(verifySsoToken(makeToken({ cids: [] }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
    const r = verifySsoToken(makeToken({ cids: [], roles: ['all_brands'], codes: ['BCF', 'BCR', 'CUP', 'IMD', 'PB', 'WTZ'] }), KEY, NOW, never);
    expect(r.ok).toBe(true);
  });

  it('roles salah / ganda -> bad_claims', () => {
    expect(verifySsoToken(makeToken({ roles: ['admin'] }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
    expect(verifySsoToken(makeToken({ roles: ['user', 'all_brands'] }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
  });

  it('sub di luar 1..2^31-1 atau bukan integer -> bad_claims', () => {
    for (const sub of [0, 2 ** 31, 1.5, '7']) {
      expect(verifySsoToken(makeToken({ sub }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
    }
  });

  it('name > 64 code point -> bad_claims; karakter kontrol dibuang', () => {
    expect(verifySsoToken(makeToken({ name: 'a'.repeat(65) }), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_claims' });
    const r = verifySsoToken(makeToken({ name: 'Bu\u0007di​ Uji' }), KEY, NOW, never);
    expect(r.ok && r.payload.name).toBe('Budi Uji');
  });

  it('urutan: sig salah menang atas payload rusak; replay hanya dicek setelah waktu', () => {
    expect(verifySsoToken(makeToken({ aud: 'x' }, 'cd'.repeat(32)), KEY, NOW, never)).toEqual({ ok: false, code: 'bad_sig' });
    expect(verifySsoToken(VECTOR_TOKEN, KEY, IAT + 100, () => true)).toEqual({ ok: false, code: 'expired' });
  });
});

describe('parseKey', () => {
  it('menerima hex >= 64 (strip), menolak lainnya', () => {
    expect(parseKey(`${KEY}\n`)).toBe(KEY);
    expect(parseKey('ab'.repeat(31))).toBeNull();
    expect(parseKey('zz'.repeat(32))).toBeNull();
    expect(parseKey('CHANGE_ME')).toBeNull();
    expect(parseKey(null)).toBeNull();
    expect(parseKey('')).toBeNull();
  });
});
