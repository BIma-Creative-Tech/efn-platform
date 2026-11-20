/**
 * Verifikasi token SSO `eit1` (docs/04 §26.3-§26.4, ADR-041) dan `eit2` (§28.13.4, G47). Urutan validasi (gagal di mana
 * pun = ditolak):
 *   1 format regex -> 2 HMAC (timingSafeEqual) -> 3 payload (eit1: tepat 11 kunci; eit2: tepat 13 kunci; tipe, v/aud/iss)
 *   -> 4 waktu -> 5 jti belum dipakai -> 6 codes/cids sesuai aturan roles (+ eit2: konsistensi wids/persona/cats).
 * HMAC dihitung atas `payload_b64` yang DITERIMA (tidak diserialisasi ulang).
 * Alasan kegagalan hanya berupa kode untuk log; tidak pernah dikirim ke klien.
 */
import {
  cidsMatchRole,
  hasExactKeys,
  isInt,
  isRecord,
  isSub,
  parseCats,
  parseCids,
  parseCodes,
  parseCodesV2,
  parseName,
  parseRoles,
  parseRolesV2,
  parseWids,
  scopeConsistent,
  type Code,
  type EsbCode,
  type Role,
} from './claims';
import { verifyHmacB64Url } from './hmac';

export const SSO_TOKEN_RE = /^eit1\.[A-Za-z0-9_-]{16,2048}\.[A-Za-z0-9_-]{43}$/;
export const SSO_MAX_TTL = 60;
export const SSO_IAT_SKEW = 30;
const JTI_RE = /^[0-9a-f]{32}$/;

const PAYLOAD_KEYS = ['aud', 'cids', 'codes', 'exp', 'iat', 'iss', 'jti', 'name', 'roles', 'sub', 'v'] as const;

export interface SsoPayload {
  aud: 'insight-web';
  cids: number[];
  codes: EsbCode[];
  exp: number;
  iat: number;
  iss: 'odoo';
  jti: string;
  name: string;
  roles: Role[];
  sub: number;
  v: 1;
}

export type SsoFailCode = 'bad_format' | 'bad_sig' | 'bad_claims' | 'expired' | 'replay' | 'eit1_disabled';
export type SsoVerifyResult = { ok: true; payload: SsoPayload } | { ok: false; code: SsoFailCode };

// ---------------------------------------------------------------- eit2 (docs/04 §28.13.4)
/** Batas 3000 menjamin cookie `eis2` < 4096 byte (§28.13.4). */
export const SSO_TOKEN_RE_V2 = /^eit2\.[A-Za-z0-9_-]{16,3000}\.[A-Za-z0-9_-]{43}$/;
const PAYLOAD_KEYS_V2 = ['aud', 'cats', 'cids', 'codes', 'exp', 'iat', 'iss', 'jti', 'name', 'roles', 'sub', 'v', 'wids'] as const;

export interface SsoPayloadV2 {
  aud: 'insight-web';
  cats: number[];
  cids: number[];
  codes: Code[];
  exp: number;
  iat: number;
  iss: 'odoo';
  jti: string;
  name: string;
  roles: Role[];
  sub: number;
  v: 2;
  wids: number[];
}
export type SsoVerifyResultV2 = { ok: true; payload: SsoPayloadV2 } | { ok: false; code: SsoFailCode };
export type SsoVerifyResultAny = { ok: true; payload: SsoPayload | SsoPayloadV2 } | { ok: false; code: SsoFailCode };

export function verifySsoTokenV2(
  token: unknown,
  key: string,
  nowSec: number,
  isReplay: (jti: string) => boolean,
): SsoVerifyResultV2 {
  // 1. format
  if (typeof token !== 'string' || !SSO_TOKEN_RE_V2.test(token)) return { ok: false, code: 'bad_format' };
  const [, payloadB64, sigB64] = token.split('.');

  // 2. HMAC
  if (!verifyHmacB64Url(key, `eit2.${payloadB64}`, sigB64)) return { ok: false, code: 'bad_sig' };

  // 3. payload
  let raw: unknown;
  try {
    raw = JSON.parse(Buffer.from(payloadB64, 'base64url').toString('utf8'));
  } catch {
    return { ok: false, code: 'bad_claims' };
  }
  if (!isRecord(raw) || !hasExactKeys(raw, PAYLOAD_KEYS_V2)) return { ok: false, code: 'bad_claims' };
  if (raw.v !== 2 || raw.aud !== 'insight-web' || raw.iss !== 'odoo') return { ok: false, code: 'bad_claims' };
  const sub: unknown = raw.sub;
  const iat: unknown = raw.iat;
  const exp: unknown = raw.exp;
  const jti: unknown = raw.jti;
  if (!isSub(sub) || !isInt(iat) || !isInt(exp)) return { ok: false, code: 'bad_claims' };
  if (typeof jti !== 'string' || !JTI_RE.test(jti)) return { ok: false, code: 'bad_claims' };
  const name = parseName(raw.name);
  const roles = parseRolesV2(raw.roles);
  const cids = parseCids(raw.cids);
  const codes = parseCodesV2(raw.codes);
  const wids = parseWids(raw.wids);
  const cats = parseCats(raw.cats);
  if (name === null || roles === null || cids === null || codes === null || wids === null || cats === null) {
    return { ok: false, code: 'bad_claims' };
  }

  // 4. waktu
  if (iat > nowSec + SSO_IAT_SKEW || nowSec >= exp || exp - iat < 1 || exp - iat > SSO_MAX_TTL) {
    return { ok: false, code: 'expired' };
  }

  // 5. jti
  if (isReplay(jti)) return { ok: false, code: 'replay' };

  // 6. cids sesuai roles + konsistensi wids/persona/codes/cats
  if (!cidsMatchRole(cids, roles)) return { ok: false, code: 'bad_claims' };
  if (!scopeConsistent({ codes, roles, wids, cats })) return { ok: false, code: 'bad_claims' };

  return { ok: true, payload: { aud: 'insight-web', cats, cids, codes, exp, iat, iss: 'odoo', jti, name, roles, sub, v: 2, wids } };
}

/**
 * Pintu masuk `/sso`: `eit2` selalu; `eit1` hanya bila `acceptEit1` (INSIGHT_ACCEPT_EIT1, §28.13.5). Prefix dipilih dari
 * string yang diterima; semua pemeriksaan sesungguhnya dilakukan verifier masing-masing.
 */
export function verifyAnySsoToken(
  token: unknown,
  key: string,
  nowSec: number,
  isReplay: (jti: string) => boolean,
  acceptEit1: boolean,
): SsoVerifyResultAny {
  if (typeof token === 'string' && token.startsWith('eit2.')) return verifySsoTokenV2(token, key, nowSec, isReplay);
  if (typeof token === 'string' && token.startsWith('eit1.')) {
    return acceptEit1 ? verifySsoToken(token, key, nowSec, isReplay) : { ok: false, code: 'eit1_disabled' };
  }
  return { ok: false, code: 'bad_format' };
}

export function verifySsoToken(
  token: unknown,
  key: string,
  nowSec: number,
  isReplay: (jti: string) => boolean,
): SsoVerifyResult {
  // 1. format
  if (typeof token !== 'string' || !SSO_TOKEN_RE.test(token)) return { ok: false, code: 'bad_format' };
  const [, payloadB64, sigB64] = token.split('.');

  // 2. HMAC
  if (!verifyHmacB64Url(key, `eit1.${payloadB64}`, sigB64)) return { ok: false, code: 'bad_sig' };

  // 3. payload
  let raw: unknown;
  try {
    raw = JSON.parse(Buffer.from(payloadB64, 'base64url').toString('utf8'));
  } catch {
    return { ok: false, code: 'bad_claims' };
  }
  if (!isRecord(raw) || !hasExactKeys(raw, PAYLOAD_KEYS)) return { ok: false, code: 'bad_claims' };
  if (raw.v !== 1 || raw.aud !== 'insight-web' || raw.iss !== 'odoo') return { ok: false, code: 'bad_claims' };
  const sub: unknown = raw.sub;
  const iat: unknown = raw.iat;
  const exp: unknown = raw.exp;
  const jti: unknown = raw.jti;
  if (!isSub(sub) || !isInt(iat) || !isInt(exp)) return { ok: false, code: 'bad_claims' };
  if (typeof jti !== 'string' || !JTI_RE.test(jti)) return { ok: false, code: 'bad_claims' };
  const name = parseName(raw.name);
  const roles = parseRoles(raw.roles);
  const cids = parseCids(raw.cids);
  const codes = parseCodes(raw.codes);
  if (name === null || roles === null || cids === null || codes === null) return { ok: false, code: 'bad_claims' };

  // 4. waktu
  if (iat > nowSec + SSO_IAT_SKEW || nowSec >= exp || exp - iat < 1 || exp - iat > SSO_MAX_TTL) {
    return { ok: false, code: 'expired' };
  }

  // 5. jti (pemanggil menyimpan jti HANYA setelah seluruh validasi lolos)
  if (isReplay(jti)) return { ok: false, code: 'replay' };

  // 6. codes/cids sesuai roles
  if (!cidsMatchRole(cids, roles)) return { ok: false, code: 'bad_claims' };

  return { ok: true, payload: { aud: 'insight-web', cids, codes, exp, iat, iss: 'odoo', jti, name, roles, sub, v: 1 } };
}
