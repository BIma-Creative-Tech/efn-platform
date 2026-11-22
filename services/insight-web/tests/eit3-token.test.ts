/**
 * Token eit3 / sesi eis3 (docs/04 §28.15.4-§28.15.5, G52): vektor D..G, vektor negatif N6..N14, aturan T1..T7, batas ukuran T8,
 * transisi INSIGHT_ACCEPT_EIT2, round-trip cookie eis3, dan konfigurasi flag.
 */
import { createHmac } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { parseBrs, scopeConsistentV3 } from '@/lib/claims';
import { parseConfig } from '@/lib/config';
import { createSessionValue, SESSION_VALUE_RE_V3, verifySessionValue } from '@/lib/session';
import { SSO_TOKEN_RE_V3, verifyAnySsoToken, verifySsoToken, verifySsoTokenV2, verifySsoTokenV3 } from '@/lib/token';
import { VECTOR_TOKEN } from './helpers';
import { TOKEN_A } from './helpers-v2';
import {
  b64u,
  IAT,
  KEY,
  makeTokenV3,
  PAYLOAD_D_JSON,
  PAYLOAD_E_JSON,
  PAYLOAD_F_JSON,
  PAYLOAD_G_JSON,
  signV3,
  TOKEN_D,
  TOKEN_E,
  TOKEN_F,
  TOKEN_G,
} from './helpers-v3';

const NOW = IAT + 10;
const never = () => false;
const bad = (code: string) => ({ ok: false, code });
const SKEY = 'cd'.repeat(32);

describe('vektor eit3 D..G', () => {
  it('semua diterima dengan klaim yang benar', () => {
    const d = verifySsoTokenV3(TOKEN_D, KEY, NOW, never);
    expect(d.ok && d.payload).toMatchObject({ v: 3, sub: 21, codes: ['PB'], cids: [5], roles: ['store', 'user'], brs: ['pb:12', 'pb:7'], wids: [], cats: [] });
    const e = verifySsoTokenV3(TOKEN_E, KEY, NOW, never);
    expect(e.ok && e.payload).toMatchObject({ v: 3, sub: 7, codes: ['BCF', 'PB', 'SMD'], roles: ['exec', 'user'], brs: [], wids: [0, 20, 21, 22, 23, 24, 25] });
    const f = verifySsoTokenV3(TOKEN_F, KEY, NOW, never);
    expect(f.ok && f.payload).toMatchObject({ sub: 30, codes: ['PB', 'WTZ'], roles: ['marketing', 'user'], brs: [] });
    const g = verifySsoTokenV3(TOKEN_G, KEY, NOW, never);
    expect(g.ok && g.payload).toMatchObject({ sub: 31, codes: ['BCR'], cids: [4], brs: ['bacha:15'], name: 'Sinta Toko' });
  });

  it('payload_b64 = base64url(JSON), tepat 14 kunci, token memenuhi regex penerima', () => {
    for (const [json, tok] of [[PAYLOAD_D_JSON, TOKEN_D], [PAYLOAD_E_JSON, TOKEN_E], [PAYLOAD_F_JSON, TOKEN_F], [PAYLOAD_G_JSON, TOKEN_G]] as const) {
      expect(tok.split('.')[1]).toBe(b64u(json));
      expect(SSO_TOKEN_RE_V3.test(tok)).toBe(true);
      const keys = Object.keys(JSON.parse(json));
      expect(keys).toHaveLength(14);
      expect(keys).toEqual([...keys].sort());
    }
  });

  it('sig dari kunci hex-decode berbeda dan DITOLAK', () => {
    const b64 = b64u(PAYLOAD_D_JSON);
    const asHex = createHmac('sha256', Buffer.from(KEY, 'hex')).update(`eit3.${b64}`).digest('base64url');
    expect(asHex).not.toBe(TOKEN_D.split('.')[2]);
    expect(verifySsoTokenV3(`eit3.${b64}.${asHex}`, KEY, NOW, never)).toEqual(bad('bad_sig'));
  });
});

describe('vektor negatif N6..N14 (docs/04 §28.15.5)', () => {
  it('N6: D dengan roles [marketing, store, user] -> bad_claims (T5)', () => {
    expect(verifySsoTokenV3(makeTokenV3({ roles: ['marketing', 'store', 'user'] }), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N7: D dengan brs=[] -> bad_claims (T6: store-only wajib ber-cabang)', () => {
    expect(verifySsoTokenV3(makeTokenV3({ brs: [] }), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N8: F (marketing) dengan brs [pb:7] -> bad_claims (T6)', () => {
    expect(verifySsoTokenV3(makeTokenV3({ brs: ['pb:7'] }, PAYLOAD_F_JSON), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N9: brs [pb:07] (nol di depan) -> bad_claims', () => {
    expect(verifySsoTokenV3(makeTokenV3({ brs: ['pb:07'] }), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N10: brs [PB:7] (huruf besar) -> bad_claims', () => {
    expect(verifySsoTokenV3(makeTokenV3({ brs: ['PB:7'] }), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N11: brs [pb:7, pb:12] (bukan urut ASCII) -> bad_claims', () => {
    expect(verifySsoTokenV3(makeTokenV3({ brs: ['pb:7', 'pb:12'] }), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N12: roles [dc, user], codes [PB], wids [] -> bad_claims (T3)', () => {
    expect(verifySsoTokenV3(makeTokenV3({ roles: ['dc', 'user'], codes: ['PB'], wids: [], brs: [] }), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N13: prefix eit2 dengan "v":3 -> bad_claims di verifier eit2 (dan eit3 tidak terpicu)', () => {
    // payload D apa adanya (14 kunci) dan versi tanpa `brs` (13 kunci, v=3): keduanya ditolak verifier eit2.
    expect(verifySsoTokenV2(signV3(PAYLOAD_D_JSON, KEY, 'eit2'), KEY, NOW, never)).toEqual(bad('bad_claims'));
    expect(verifySsoTokenV2(signV3(PAYLOAD_D_JSON.replace('"brs":["pb:12","pb:7"],', ''), KEY, 'eit2'), KEY, NOW, never)).toEqual(bad('bad_claims'));
    // prefix eit3 membawa payload v=2 -> bad_claims juga.
    expect(verifySsoTokenV3(makeTokenV3({ v: 2 }), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
  it('N14: E (exec) dengan brs [pb:7] -> bad_claims (T6: exec menutupi cabang)', () => {
    expect(verifySsoTokenV3(makeTokenV3({ brs: ['pb:7'] }, PAYLOAD_E_JSON), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });
});

describe('bentuk klaim dan aturan T1..T8', () => {
  it('kunci kurang/lebih dari 14 ditolak; sub/jti/aud/iss salah ditolak', () => {
    expect(verifySsoTokenV3(makeTokenV3({}, PAYLOAD_D_JSON, ['brs']), KEY, NOW, never)).toEqual(bad('bad_claims'));
    expect(verifySsoTokenV3(makeTokenV3({ extra: 1 }), KEY, NOW, never)).toEqual(bad('bad_claims'));
    expect(verifySsoTokenV3(makeTokenV3({ aud: 'x' }), KEY, NOW, never)).toEqual(bad('bad_claims'));
    expect(verifySsoTokenV3(makeTokenV3({ iss: 'x' }), KEY, NOW, never)).toEqual(bad('bad_claims'));
    expect(verifySsoTokenV3(makeTokenV3({ jti: 'XYZ' }), KEY, NOW, never)).toEqual(bad('bad_claims'));
    expect(verifySsoTokenV3(makeTokenV3({ sub: 0 }), KEY, NOW, never)).toEqual(bad('bad_claims'));
  });

  it('brs: batas 50 butir, branch_id <= 4294967295, brand harus dikenal, bukan array/butir bukan string', () => {
    const fifty = Array.from({ length: 50 }, (_, i) => `pb:${i + 100}`).sort();
    expect(parseBrs(fifty)).toEqual(fifty);
    expect(parseBrs([...fifty, 'pb:999'].sort())).toBeNull();
    expect(parseBrs(['pb:4294967295'])).toEqual(['pb:4294967295']);
    expect(parseBrs(['pb:4294967296'])).toBeNull();
    expect(parseBrs(['pb:99999999999'])).toBeNull();
    expect(parseBrs(['xx:1'])).toBeNull();
    expect(parseBrs(['pb:0'])).toBeNull();
    expect(parseBrs(['pb:1', 'pb:1'])).toBeNull();
    expect(parseBrs(['bacha:2', 'pb:1'])).toEqual(['bacha:2', 'pb:1']);
    expect(parseBrs('pb:1')).toBeNull();
    expect(parseBrs([7])).toBeNull();
    expect(parseBrs([])).toEqual([]);
  });

  it('brs urut string ASCII, bukan numerik: pb:12 < pb:7', () => {
    expect(parseBrs(['pb:12', 'pb:7'])).toEqual(['pb:12', 'pb:7']);
    expect(parseBrs(['pb:7', 'pb:12'])).toBeNull();
  });

  const base = { codes: ['PB'], roles: ['store', 'user'], wids: [] as number[], cats: [] as number[], brs: ['pb:7'] };
  it('T1..T7 langsung pada scopeConsistentV3', () => {
    expect(scopeConsistentV3(base)).toBe(true);
    // T1: wids tanpa kode SM, atau SM tanpa persona
    expect(scopeConsistentV3({ ...base, wids: [5] })).toBe(false);
    expect(scopeConsistentV3({ codes: ['PB', 'SMD'], roles: ['user'], wids: [0], cats: [], brs: [] })).toBe(false);
    // T2: exec tanpa 0 di wids; store-only dengan 0
    expect(scopeConsistentV3({ codes: ['SMD'], roles: ['exec', 'user'], wids: [20], cats: [], brs: [] })).toBe(false);
    expect(scopeConsistentV3({ codes: ['SMD'], roles: ['store', 'user'], wids: [0, 20], cats: [], brs: [] })).toBe(false);
    expect(scopeConsistentV3({ codes: ['SMD'], roles: ['store', 'user'], wids: [20], cats: [], brs: [] })).toBe(true);
    // T3: category/dc tanpa SM
    expect(scopeConsistentV3({ codes: ['PB'], roles: ['category', 'user'], wids: [], cats: [], brs: [] })).toBe(false);
    // T4: persona tanpa cakupan apa pun (tanpa kode ESB maupun SM) ditolak; exec SMD-saja sah
    expect(scopeConsistentV3({ codes: [], roles: ['exec', 'user'], wids: [], cats: [], brs: [] })).toBe(false);
    expect(scopeConsistentV3({ codes: ['SMD'], roles: ['exec', 'user'], wids: [0, 20], cats: [], brs: [] })).toBe(true);
    // T5: store hanya bersama exec
    expect(scopeConsistentV3({ ...base, roles: ['marketing', 'store', 'user'], brs: [] })).toBe(false);
    expect(scopeConsistentV3({ ...base, roles: ['exec', 'store', 'user'], brs: [] })).toBe(true);
    // T6: all_brands menutupi cabang; store+all_brands tanpa brs sah
    expect(scopeConsistentV3({ ...base, roles: ['all_brands', 'store'], brs: [] })).toBe(true);
    expect(scopeConsistentV3({ ...base, roles: ['all_brands', 'store'] })).toBe(false);
    // T6: store-only tanpa kode ESB tidak ber-cabang (SM store tetap v2 semantik)
    expect(scopeConsistentV3({ codes: ['SMD'], roles: ['store', 'user'], wids: [20], cats: [], brs: ['pb:7'] })).toBe(false);
    // T7: cats hanya untuk category
    expect(scopeConsistentV3({ codes: ['SMD'], roles: ['exec', 'user'], wids: [0, 20], cats: [4], brs: [] })).toBe(false);
  });

  it('T8: batas 3000 karakter payload (regex penerima), 3001 ditolak', () => {
    expect(SSO_TOKEN_RE_V3.test(`eit3.${'a'.repeat(3000)}.${'a'.repeat(43)}`)).toBe(true);
    expect(SSO_TOKEN_RE_V3.test(`eit3.${'a'.repeat(3001)}.${'a'.repeat(43)}`)).toBe(false);
    expect(verifySsoTokenV3(`eit3.${'a'.repeat(3001)}.${'a'.repeat(43)}`, KEY, NOW, never)).toEqual(bad('bad_format'));
  });

  it('waktu: kedaluwarsa, TTL > 60 s, iat di masa depan; replay jti', () => {
    expect(verifySsoTokenV3(TOKEN_D, KEY, IAT + 60, never)).toEqual(bad('expired'));
    expect(verifySsoTokenV3(makeTokenV3({ exp: IAT + 61 }), KEY, NOW, never)).toEqual(bad('expired'));
    expect(verifySsoTokenV3(TOKEN_D, KEY, IAT - 31, never)).toEqual(bad('expired'));
    expect(verifySsoTokenV3(TOKEN_D, KEY, NOW, () => true)).toEqual(bad('replay'));
  });
});

describe('transisi INSIGHT_ACCEPT_EIT2 (verifyAnySsoToken)', () => {
  it('eit3 diterima apa pun flag-nya', () => {
    for (const eit1 of [true, false]) for (const eit2 of [true, false]) expect(verifyAnySsoToken(TOKEN_D, KEY, NOW, never, eit1, eit2).ok).toBe(true);
  });
  it('eit2 hanya bila acceptEit2 (default true); eit1 tetap diatur flag sendiri', () => {
    expect(verifyAnySsoToken(TOKEN_A, KEY, NOW, never, true).ok).toBe(true); // acceptEit2 bawaan = true
    expect(verifyAnySsoToken(TOKEN_A, KEY, NOW, never, true, true).ok).toBe(true);
    expect(verifyAnySsoToken(TOKEN_A, KEY, NOW, never, true, false)).toEqual(bad('eit2_disabled'));
    expect(verifyAnySsoToken(VECTOR_TOKEN, KEY, NOW, never, true, false)).toMatchObject({ ok: true });
    expect(verifyAnySsoToken(VECTOR_TOKEN, KEY, NOW, never, false, true)).toEqual(bad('eit1_disabled'));
  });
  it('prefix lain -> bad_format; eit1 dan eit2 tidak lolos verifier eit3', () => {
    expect(verifyAnySsoToken('eit9.aaaaaaaaaaaaaaaa.' + 'a'.repeat(43), KEY, NOW, never, true)).toEqual(bad('bad_format'));
    expect(verifySsoTokenV3(TOKEN_A, KEY, NOW, never)).toEqual(bad('bad_format'));
    expect(verifySsoToken(TOKEN_D, KEY, NOW, never)).toEqual(bad('bad_format'));
  });
});

describe('sesi eis3', () => {
  const payloadOf = (t: string) => {
    const r = verifySsoTokenV3(t, KEY, NOW, never);
    if (!r.ok) throw new Error('token uji');
    return r.payload;
  };

  it('round-trip: cookie eis3 tepat 10 kunci berurutan brs,cats,cids,codes,exp,iat,name,roles,sub,wids; sesi membawa brs', () => {
    const v = createSessionValue(payloadOf(TOKEN_D), SKEY, NOW);
    expect(v.startsWith('eis3.')).toBe(true);
    expect(SESSION_VALUE_RE_V3.test(v)).toBe(true);
    const json = Buffer.from(v.split('.')[1], 'base64url').toString('utf8');
    expect(Object.keys(JSON.parse(json))).toEqual(['brs', 'cats', 'cids', 'codes', 'exp', 'iat', 'name', 'roles', 'sub', 'wids']);
    expect(json.startsWith('{"brs":["pb:12","pb:7"],"cats":[],')).toBe(true);
    const s = verifySessionValue(v, SKEY, NOW + 1);
    expect(s).toMatchObject({ v: 3, sub: 21, brs: ['pb:12', 'pb:7'], codes: ['PB'], roles: ['store', 'user'], wids: [], cats: [] });
  });

  it('semua vektor D..G round-trip', () => {
    for (const t of [TOKEN_D, TOKEN_E, TOKEN_F, TOKEN_G]) {
      const p = payloadOf(t);
      expect(verifySessionValue(createSessionValue(p, SKEY, NOW), SKEY, NOW + 1)).toMatchObject({ v: 3, sub: p.sub, brs: p.brs });
    }
  });

  it('cookie eis3 dengan brs/roles tidak konsisten (ditandatangani kunci sesi) tetap ditolak', () => {
    const mk = (over: Record<string, unknown>) => {
      const payload = { brs: [], cats: [], cids: [5], codes: ['PB'], exp: NOW + 100, iat: NOW, name: 'X', roles: ['store', 'user'], sub: 9, wids: [], ...over };
      const b64 = Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url');
      const sig = createHmac('sha256', Buffer.from(SKEY, 'ascii')).update(`eis3.${b64}`, 'ascii').digest('base64url');
      return `eis3.${b64}.${sig}`;
    };
    expect(verifySessionValue(mk({}), SKEY, NOW + 1)).toBeNull(); // T6: store-only tanpa brs
    expect(verifySessionValue(mk({ brs: ['pb:7'] }), SKEY, NOW + 1)).toMatchObject({ v: 3, brs: ['pb:7'] });
    expect(verifySessionValue(mk({ brs: ['pb:7'], roles: ['marketing', 'store', 'user'] }), SKEY, NOW + 1)).toBeNull(); // T5
    expect(verifySessionValue(mk({ brs: ['pb:7', 'pb:12'] }), SKEY, NOW + 1)).toBeNull(); // urutan
  });

  it('cookie eis3 salah kunci / bit sisa / kunci berlebih ditolak', () => {
    const v = createSessionValue(payloadOf(TOKEN_D), SKEY, NOW);
    expect(verifySessionValue(v, 'ef'.repeat(32), NOW + 1)).toBeNull();
    const [p, b, s] = v.split('.');
    const extra = Buffer.from(JSON.stringify({ ...JSON.parse(Buffer.from(b, 'base64url').toString('utf8')), z: 1 }), 'utf8').toString('base64url');
    const sig = createHmac('sha256', Buffer.from(SKEY, 'ascii')).update(`eis3.${extra}`, 'ascii').digest('base64url');
    expect(verifySessionValue(`${p}.${extra}.${sig}`, SKEY, NOW + 1)).toBeNull();
    expect(verifySessionValue(`${p}.${b}.${s.slice(0, -1)}`, SKEY, NOW + 1)).toBeNull();
  });

  it('eis2 diterima bila acceptV2 (default) dan membawa brs=[]; ditolak bila INSIGHT_ACCEPT_EIT2=false; eis3 tidak terpengaruh', () => {
    const r2 = verifyAnySsoToken(TOKEN_A, KEY, NOW, never, true, true);
    if (!r2.ok) throw new Error('token uji');
    const v2 = createSessionValue(r2.payload, SKEY, NOW);
    expect(v2.startsWith('eis2.')).toBe(true);
    expect(verifySessionValue(v2, SKEY, NOW + 1)).toMatchObject({ v: 2, brs: [] });
    expect(verifySessionValue(v2, SKEY, NOW + 1, true, false)).toBeNull();
    const v3 = createSessionValue(payloadOf(TOKEN_E), SKEY, NOW);
    expect(verifySessionValue(v3, SKEY, NOW + 1, false, false)).toMatchObject({ v: 3 });
  });
});

describe('konfigurasi flag G52', () => {
  it('INSIGHT_ACCEPT_EIT2: default true; bila diset hanya "true" persis', () => {
    expect(parseConfig({}).acceptEit2).toBe(true);
    expect(parseConfig({ INSIGHT_ACCEPT_EIT2: '' }).acceptEit2).toBe(true);
    expect(parseConfig({ INSIGHT_ACCEPT_EIT2: 'true' }).acceptEit2).toBe(true);
    for (const v of ['false', 'False', '0', 'ture', 'TRUE']) expect(parseConfig({ INSIGHT_ACCEPT_EIT2: v }).acceptEit2, v).toBe(false);
  });
  it('INSIGHT_FNB_PERSONA_ENABLED: default false; hanya "true" persis; tidak bergantung INSIGHT_SM_ENABLED', () => {
    expect(parseConfig({}).fnbEnabled).toBe(false);
    expect(parseConfig({ INSIGHT_FNB_PERSONA_ENABLED: 'true' }).fnbEnabled).toBe(true);
    for (const v of ['', 'false', '1', 'TRUE', 'True', ' true']) expect(parseConfig({ INSIGHT_FNB_PERSONA_ENABLED: v }).fnbEnabled, v).toBe(false);
    expect(parseConfig({ INSIGHT_SM_ENABLED: 'true' }).fnbEnabled).toBe(false);
    expect(parseConfig({ INSIGHT_FNB_PERSONA_ENABLED: 'true', INSIGHT_SM_ENABLED: 'false' })).toMatchObject({ fnbEnabled: true, smEnabled: false });
  });
});
