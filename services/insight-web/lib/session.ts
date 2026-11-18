/**
 * Cookie sesi `eis1` (docs/04 §26.5 + §26.13 C2).
 *   nama   = "__Host-eis" (C2: syarat prefix __Host-: Secure, Path=/, tanpa Domain)
 *   nilai  = "eis1." + payload_b64 + "." + sig_b64
 *   payload= UTF-8(JSON.stringify({cids,codes,exp,iat,name,roles,sub})) -- kunci urut abjad
 *   sig    = HMAC-SHA256(insight_session_key [byte ASCII], "eis1." + payload_b64)
 * Stateless: tidak ada penyimpanan sesi server; pencabutan darurat = rotasi insight_session_key.
 */
import {
  hasExactKeys,
  isInt,
  isRecord,
  isSub,
  parseCids,
  parseCodes,
  parseName,
  parseRoles,
  cidsMatchRole,
  type EsbCode,
  type Role,
} from './claims';
import { hmacSha256B64Url, verifyHmacB64Url } from './hmac';
import type { SsoPayload } from './token';

export const SESSION_COOKIE = '__Host-eis';
export const SESSION_TTL_SEC = 28800;
export const SESSION_VALUE_RE = /^eis1\.[A-Za-z0-9_-]{16,2048}\.[A-Za-z0-9_-]{43}$/;
const IAT_SKEW = 30;
const SESSION_KEYS = ['cids', 'codes', 'exp', 'iat', 'name', 'roles', 'sub'] as const;

export interface Session {
  cids: number[];
  codes: EsbCode[];
  exp: number;
  iat: number;
  name: string;
  roles: Role[];
  sub: number;
}

export function createSessionValue(p: SsoPayload, key: string, nowSec: number): string {
  // Urutan sisipan = urut abjad (kontrak: byte-exact JSON.stringify).
  const payload = {
    cids: p.cids,
    codes: p.codes,
    exp: nowSec + SESSION_TTL_SEC,
    iat: nowSec,
    name: p.name,
    roles: p.roles,
    sub: p.sub,
  };
  const b64 = Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url');
  return `eis1.${b64}.${hmacSha256B64Url(key, `eis1.${b64}`)}`;
}

export function verifySessionValue(value: unknown, key: string, nowSec: number): Session | null {
  if (typeof value !== 'string' || !SESSION_VALUE_RE.test(value)) return null;
  const [, payloadB64, sigB64] = value.split('.');
  if (!verifyHmacB64Url(key, `eis1.${payloadB64}`, sigB64)) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(Buffer.from(payloadB64, 'base64url').toString('utf8'));
  } catch {
    return null;
  }
  if (!isRecord(raw) || !hasExactKeys(raw, SESSION_KEYS)) return null;
  const sub: unknown = raw.sub;
  const iat: unknown = raw.iat;
  const exp: unknown = raw.exp;
  if (!isSub(sub) || !isInt(iat) || !isInt(exp)) return null;
  const name = parseName(raw.name);
  const roles = parseRoles(raw.roles);
  const cids = parseCids(raw.cids);
  const codes = parseCodes(raw.codes);
  if (name === null || roles === null || cids === null || codes === null) return null;
  if (!cidsMatchRole(cids, roles)) return null;
  if (iat > nowSec + IAT_SKEW || nowSec >= exp || exp - iat < 1 || exp - iat > SESSION_TTL_SEC) return null;
  return { cids, codes, exp, iat, name, roles, sub };
}

const ATTRS = 'Path=/; HttpOnly; Secure; SameSite=Lax';

export function setCookieHeader(value: string): string {
  return `${SESSION_COOKIE}=${value}; Max-Age=${SESSION_TTL_SEC}; ${ATTRS}`;
}

export function clearCookieHeader(): string {
  return `${SESSION_COOKIE}=; Max-Age=0; ${ATTRS}`;
}

/** Ambil satu cookie dari header `Cookie` mentah (tanpa decode; nilai sesi hanya base64url + titik). */
export function readCookie(header: string | null | undefined, name: string): string | undefined {
  if (!header) return undefined;
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    if (part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return undefined;
}
