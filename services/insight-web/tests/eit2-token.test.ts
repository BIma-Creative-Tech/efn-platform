/**
 * Token eit2 / sesi eis2 (docs/04 §28.13.4-§28.13.6, G47): vektor A/B/C, vektor negatif N1..N5, aturan konsistensi,
 * batas ukuran, transisi INSIGHT_ACCEPT_EIT1, round-trip cookie eis2, dan setting cakupan SQL_efn_sig2.
 */
import { createHmac } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { scopeSettingsSm } from '@/lib/scope';
import { createSessionValue, verifySessionValue } from '@/lib/session';
import { verifyAnySsoToken, verifySsoToken, verifySsoTokenV2, SSO_TOKEN_RE_V2 } from '@/lib/token';
import { VECTOR_TOKEN } from './helpers';
import {
  b64u,
  IAT,
  KEY,
  makeTokenV2,
  PAYLOAD_A_JSON,
  PAYLOAD_B_JSON,
  PAYLOAD_C_JSON,
  sig2Of,
  signV2,
  TOKEN_A,
  TOKEN_B,
  TOKEN_C,
} from './helpers-v2';

const NOW = IAT + 10;
const never = () => false;
const bad = (code: string) => ({ ok: false, code });

describe('vektor eit2 A/B/C', () => {
  it('semua diterima dengan klaim yang benar', () => {
    const a = verifySsoTokenV2(TOKEN_A, KEY, NOW, never);
    expect(a.ok && a.payload).toMatchObject({ v: 2, sub: 7, codes: ['BCF', 'PB', 'SMD'], cids: [3, 5, 11], roles: ['exec', 'store', 'user'], wids: [0, 20, 21, 22, 23, 24, 25], cats: [] });
    const b = verifySsoTokenV2(TOKEN_B, KEY, NOW, never);
    expect(b.ok && b.payload).toMatchObject({ sub: 9, wids: [22], roles: ['store', 'user'], name: 'Sari Toko' });
    const c = verifySsoTokenV2(TOKEN_C, KEY, NOW, never);
    expect(c.ok && c.payload).toMatchObject({ sub: 12, cats: [4, 9], roles: ['category', 'user'] });
  });

  it('payload_b64 = base64url(JSON) dan token memenuhi regex penerima', () => {
    for (const [json, tok] of [[PAYLOAD_A_JSON, TOKEN_A], [PAYLOAD_B_JSON, TOKEN_B], [PAYLOAD_C_JSON, TOKEN_C]] as const) {
      expect(tok.split('.')[1]).toBe(b64u(json));
      expect(SSO_TOKEN_RE_V2.test(tok)).toBe(true);
      expect(Object.keys(JSON.parse(json))).toHaveLength(13);
    }
  });

  it('N1: sig dari kunci hex-decode berbeda dan DITOLAK', () => {
    const b64 = b64u(PAYLOAD_A_JSON);
    const asHex = createHmac('sha256', Buffer.from(KEY, 'hex')).update(`eit2.${b64}`).digest('base64url');
    const asAscii = TOKEN_A.split('.')[2];
    expect(asHex).not.toBe(asAscii);
    expect(verifySsoTokenV2(`eit2.${b64}.${asHex}`, KEY, NOW, never)).toEqual(bad('bad_sig'));
  });

  it('N2: exec tanpa 0 di wids -> bad_claims', () => {
    expect(verifySsoTokenV2(signV2(PAYLOAD_A_JSON.replace('[0,20,21,22,23,24,25]', '[20,21,22,23,24,25]')), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N3: store saja dengan 0 di wids -> bad_claims', () => {
    expect(verifySsoTokenV2(signV2(PAYLOAD_B_JSON.replace('"wids":[22]', '"wids":[0,22]')), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N4: v=1 dengan prefix eit2 -> bad_claims', () => {
    expect(verifySsoTokenV2(signV2(PAYLOAD_B_JSON.replace('"v":2', '"v":1')), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N5: codes SMD dengan wids kosong -> bad_claims', () => {
    expect(verifySsoTokenV2(signV2(PAYLOAD_B_JSON.replace('"wids":[22]', '"wids":[]')), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
});

describe('aturan konsistensi dan bentuk klaim eit2', () => {
  it.each([
    ['persona tanpa kode SM', { codes: ['PB'], cids: [3] }],
    ['kode SM tanpa persona', { roles: ['user'] }],
    ['cats tanpa persona category', { cats: [4] }],
    ['roles tidak terurut', { roles: ['user', 'store'] }],
    ['roles tanpa user/all_brands', { roles: ['store'] }],
    ['roles memuat user dan all_brands', { roles: ['all_brands', 'store', 'user'] }],
    ['persona asing', { roles: ['admin', 'user'] }],
    ['wids tidak terurut', { wids: [22, 21] }],
    ['wids duplikat', { wids: [22, 22] }],
    ['wids negatif', { wids: [-1, 22] }],
    ['wids > 200 butir', { wids: Array.from({ length: 201 }, (_, i) => i + 1) }],
    ['cats > 50 butir', { roles: ['category', 'user'], wids: [0, 22], cats: Array.from({ length: 51 }, (_, i) => i + 1) }],
    ['cats berisi 0', { roles: ['category', 'user'], wids: [0, 22], cats: [0, 4] }],
    ['codes asing', { codes: ['XXX'] }],
    ['codes > 7 butir', { codes: ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'] }],
    ['cids kosong untuk user dengan persona', { cids: [] }],
  ])('%s -> bad_claims', (_n, o) => {
    expect(verifySsoTokenV2(makeTokenV2(o), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });

  it('tepat 13 kunci: kunci ekstra atau kurang ditolak', () => {
    expect(verifySsoTokenV2(makeTokenV2({ email: 'x@y.z' }), KEY, NOW, never)).toEqual(bad('bad_claims'));
    expect(verifySsoTokenV2(makeTokenV2({}, KEY, ['cats']), KEY, NOW, never)).toEqual(bad('bad_claims'));
    expect(verifySsoTokenV2(makeTokenV2({}, KEY, ['wids']), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });

  it('all_brands tanpa persona dan tanpa cids diterima; dengan persona wajib cids', () => {
    const r = verifySsoTokenV2(
      makeTokenV2({ roles: ['all_brands'], cids: [], codes: ['BCF', 'PB'], wids: [] }),
      KEY,
      NOW,
      never,
    );
    expect(r.ok).toBe(true);
    expect(verifySsoTokenV2(makeTokenV2({ roles: ['all_brands', 'store'], cids: [], wids: [22] }), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });

  it('semua lima persona + kombinasi valid diterima (0 hanya untuk exec/category/dc/marketing)', () => {
    const r = verifySsoTokenV2(
      makeTokenV2({ roles: ['category', 'dc', 'exec', 'marketing', 'store', 'user'], wids: [0, 22], cats: [4] }),
      KEY,
      NOW,
      never,
    );
    expect(r.ok).toBe(true);
  });

  it('batas ukuran: payload_b64 > 3000 karakter ditolak sebagai bad_format; 3000 diterima regex', () => {
    const long = `eit2.${'a'.repeat(3001)}.${'a'.repeat(43)}`;
    expect(verifySsoTokenV2(long, KEY, NOW, never)).toEqual(bad('bad_format'));
    expect(SSO_TOKEN_RE_V2.test(`eit2.${'a'.repeat(3000)}.${'a'.repeat(43)}`)).toBe(true);
  });

  it('waktu, replay, nama, dan sub mengikuti aturan eit1', () => {
    expect(verifySsoTokenV2(TOKEN_B, KEY, IAT + 60, never)).toEqual(bad('expired'));
    expect(verifySsoTokenV2(TOKEN_B, KEY, NOW, () => true)).toEqual(bad('replay'));
    expect(verifySsoTokenV2(TOKEN_B, KEY, IAT + 100, () => true)).toEqual(bad('expired'));
    expect(verifySsoTokenV2(makeTokenV2({ sub: 0 }), KEY, NOW, never)).toEqual(bad('bad_claims'));
    expect(verifySsoTokenV2(makeTokenV2({ name: 'a'.repeat(65) }), KEY, NOW, never)).toEqual(bad('bad_claims'));
    expect(verifySsoTokenV2(makeTokenV2({}, 'cd'.repeat(32)), KEY, NOW, never)).toEqual(bad('bad_sig'));
  });

  it('eit1 tidak diterima oleh verifier v2 dan sebaliknya', () => {
    expect(verifySsoTokenV2(VECTOR_TOKEN, KEY, NOW, never)).toEqual(bad('bad_format'));
    expect(verifySsoToken(TOKEN_B, KEY, NOW, never)).toEqual(bad('bad_format'));
  });
});

describe('transisi INSIGHT_ACCEPT_EIT1 (verifyAnySsoToken)', () => {
  it('eit2 diterima apa pun flag-nya', () => {
    expect(verifyAnySsoToken(TOKEN_B, KEY, NOW, never, false).ok).toBe(true);
    expect(verifyAnySsoToken(TOKEN_B, KEY, NOW, never, true).ok).toBe(true);
  });
  it('eit1 diterima hanya bila flag true', () => {
    expect(verifyAnySsoToken(VECTOR_TOKEN, KEY, NOW, never, true).ok).toBe(true);
    expect(verifyAnySsoToken(VECTOR_TOKEN, KEY, NOW, never, false)).toEqual(bad('eit1_disabled'));
  });
  it('prefix lain / bukan string -> bad_format', () => {
    expect(verifyAnySsoToken('eit3.x.y', KEY, NOW, never, true)).toEqual(bad('bad_format'));
    expect(verifyAnySsoToken(null, KEY, NOW, never, true)).toEqual(bad('bad_format'));
  });
});

describe('sesi eis2', () => {
  const SKEY = 'cd'.repeat(32);
  const payloadOf = (t: string) => {
    const r = verifySsoTokenV2(t, KEY, NOW, never);
    if (!r.ok) throw new Error('token uji tidak sah');
    return r.payload;
  };

  it('round-trip: 9 kunci urut abjad, UTF-8, cookie < 4096 byte', () => {
    const v = createSessionValue({ ...payloadOf(TOKEN_A), name: 'Siti Ünal ñ' }, SKEY, NOW);
    expect(v.startsWith('eis2.')).toBe(true);
    expect(Object.keys(JSON.parse(Buffer.from(v.split('.')[1], 'base64url').toString('utf8')) as object)).toEqual(['cats', 'cids', 'codes', 'exp', 'iat', 'name', 'roles', 'sub', 'wids']);
    expect(Buffer.byteLength(v)).toBeLessThan(4096);
    const s = verifySessionValue(v, SKEY, NOW + 5);
    expect(s).toMatchObject({ v: 2, sub: 7, name: 'Siti Ünal ñ', roles: ['exec', 'store', 'user'], wids: [0, 20, 21, 22, 23, 24, 25] });
  });

  it('payload terbesar (cats 50, wids 200) tetap < 4096 byte sebagai cookie', () => {
    const p = payloadOf(TOKEN_C);
    const big = { ...p, cats: Array.from({ length: 50 }, (_, i) => 1000 + i), wids: [0, ...Array.from({ length: 150 }, (_, i) => 100000 + i)] };
    const v = createSessionValue(big, SKEY, NOW);
    expect(Buffer.byteLength(v)).toBeLessThan(4096);
    expect(verifySessionValue(v, SKEY, NOW + 1)).not.toBeNull();
  });

  it('kunci SSO tidak bisa memverifikasi cookie; kunci salah / sig diubah / payload diubah ditolak', () => {
    const v = createSessionValue(payloadOf(TOKEN_B), SKEY, NOW);
    expect(verifySessionValue(v, KEY, NOW + 1)).toBeNull();
    const [h, p, s] = v.split('.');
    const forged = Buffer.from(Buffer.from(p, 'base64url').toString('utf8').replace('[22]', '[21]'), 'utf8').toString('base64url');
    expect(verifySessionValue(`${h}.${forged}.${s}`, SKEY, NOW + 1)).toBeNull();
  });

  it('cookie eis2 dengan wids tidak konsisten (ditandatangani kunci sesi) tetap ditolak', () => {
    const payload = { cats: [], cids: [11], codes: ['SMD'], exp: NOW + 100, iat: NOW, name: 'X', roles: ['store', 'user'], sub: 9, wids: [0, 22] };
    const b64 = Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url');
    const sig = createHmac('sha256', Buffer.from(SKEY, 'ascii')).update(`eis2.${b64}`, 'ascii').digest('base64url');
    expect(verifySessionValue(`eis2.${b64}.${sig}`, SKEY, NOW + 1)).toBeNull();
  });

  it('eis1 hanya diterima bila acceptV1; sesi v1 tanpa wids/persona', () => {
    const t1 = verifySsoToken(VECTOR_TOKEN, KEY, NOW, never);
    if (!t1.ok) throw new Error('vektor eit1');
    const v = createSessionValue(t1.payload, SKEY, NOW);
    expect(v.startsWith('eis1.')).toBe(true);
    expect(verifySessionValue(v, SKEY, NOW + 1, true)).toMatchObject({ v: 1, wids: [], cats: [] });
    expect(verifySessionValue(v, SKEY, NOW + 1, false)).toBeNull();
    // eis2 tidak terpengaruh flag
    expect(verifySessionValue(createSessionValue(payloadOf(TOKEN_B), SKEY, NOW), SKEY, NOW + 1, false)).not.toBeNull();
  });
});

describe('SQL_efn_sig2 dan setting cakupan SM (§28.13.6)', () => {
  const sessA = { cids: [3, 5, 11], codes: ['BCF', 'PB', 'SMD'] as ('BCF' | 'PB' | 'SMD')[], roles: ['exec', 'store', 'user'] as ('exec' | 'store' | 'user')[], wids: [0, 20, 21, 22, 23, 24, 25], cats: [] as number[] };
  const sessC = { cids: [11], codes: ['SMD'] as 'SMD'[], roles: ['category', 'user'] as ('category' | 'user')[], wids: [0, 20, 21, 22, 23, 24, 25], cats: [4, 9] };

  it('vektor A: string input persis kontrak (cats kosong -> diakhiri "|")', () => {
    const s = scopeSettingsSm(sessA, KEY);
    expect(s.SQL_efn_cids).toBe('3,5,11');
    expect(s.SQL_efn_codes).toBe('SMD'); // codes ∩ SM_CODES
    expect(s.SQL_efn_wids).toBe('0,20,21,22,23,24,25');
    expect(s.SQL_efn_personas).toBe('exec,store'); // user bukan persona
    expect(s.SQL_efn_cats).toBe('');
    expect(s.SQL_efn_sig2).toBe(sig2Of(KEY, '3,5,11', 'SMD', '0,20,21,22,23,24,25', 'exec,store', ''));
    expect(s.SQL_efn_sig2).toMatch(/^[0-9a-f]{64}$/);
  });

  it('vektor C: cats ikut ditandatangani', () => {
    const s = scopeSettingsSm(sessC, KEY);
    expect(s.SQL_efn_sig2).toBe(sig2Of(KEY, '11', 'SMD', '0,20,21,22,23,24,25', 'category', '4,9'));
    expect(scopeSettingsSm({ ...sessC, cats: [4] }, KEY).SQL_efn_sig2).not.toBe(s.SQL_efn_sig2);
  });

  it('mengubah wids/persona/kunci mengubah tanda tangan; sig v1 (3 bagian) tidak sama dengan sig2', () => {
    const base = scopeSettingsSm(sessA, KEY).SQL_efn_sig2;
    expect(scopeSettingsSm({ ...sessA, wids: [0, 20] }, KEY).SQL_efn_sig2).not.toBe(base);
    expect(scopeSettingsSm({ ...sessA, roles: ['exec', 'user'] }, KEY).SQL_efn_sig2).not.toBe(base);
    expect(scopeSettingsSm(sessA, 'cd'.repeat(32)).SQL_efn_sig2).not.toBe(base);
    expect(sig2Of(KEY, '11', 'SMD', '', '', '')).not.toBe(createHmac('sha256', KEY).update('x').digest('hex'));
  });

  it('menolak sesi tanpa kode SM / persona / wids (fail closed)', () => {
    expect(() => scopeSettingsSm({ ...sessA, codes: ['PB'] }, KEY)).toThrow();
    expect(() => scopeSettingsSm({ ...sessA, roles: ['user'] }, KEY)).toThrow();
    expect(() => scopeSettingsSm({ ...sessA, wids: [] }, KEY)).toThrow();
  });
});
