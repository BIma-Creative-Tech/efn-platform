/**
 * Cookie sesi `eis1` (docs/04 §26.5 + §26.13 C2) dan `eis2` (§28.13.5, G47).
 *   nama   = "__Host-eis" (C2: syarat prefix __Host-: Secure, Path=/, tanpa Domain); nama sama untuk eis1/eis2
 *   eis1   = "eis1." + b64(JSON {cids,codes,exp,iat,name,roles,sub}) + "." + sig         (7 kunci, urut abjad)
 *   eis2   = "eis2." + b64(JSON {cats,cids,codes,exp,iat,name,roles,sub,wids}) + "." + sig (9 kunci, urut abjad)
 *   sig    = HMAC-SHA256(insight_session_key [byte ASCII], "<prefix>." + payload_b64)
 * Stateless: tidak ada penyimpanan sesi server; pencabutan darurat = rotasi insight_session_key.
 * `eis1` hanya diterima bila `acceptV1` (INSIGHT_ACCEPT_EIT1 = true); sesi eis1 tidak punya `wids`/persona sehingga
 * halaman supermarket menolaknya (403 ramah "Buka ulang Insight dari Odoo").
 */
import {
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
  cidsMatchRole,
  scopeConsistent,
  type Code,
  type Role,
} from './claims';
import { hmacSha256B64Url, verifyHmacB64Url } from './hmac';
import type { SsoPayload, SsoPayloadV2 } from './token';

export const SESSION_COOKIE = '__Host-eis';
export const SESSION_TTL_SEC = 28800;
export const SESSION_VALUE_RE = /^eis1\.[A-Za-z0-9_-]{16,2048}\.[A-Za-z0-9_-]{43}$/;
export const SESSION_VALUE_RE_V2 = /^eis2\.[A-Za-z0-9_-]{16,3000}\.[A-Za-z0-9_-]{43}$/;
const IAT_SKEW = 30;
const SESSION_KEYS = ['cids', 'codes', 'exp', 'iat', 'name', 'roles', 'sub'] as const;
const SESSION_KEYS_V2 = ['cats', 'cids', 'codes', 'exp', 'iat', 'name', 'roles', 'sub', 'wids'] as const;

export interface Session {
  /** 1 = eis1 (hanya ESB), 2 = eis2 (ESB + supermarket). Diturunkan dari prefix cookie, bukan dari payload. */
  v: 1 | 2;
  cids: number[];
  codes: Code[];
  exp: number;
  iat: number;
  name: string;
  roles: Role[];
  sub: number;
  /** id warehouse yang boleh dilihat (0 = baris tingkat PT). Selalu [] untuk eis1. */
  wids: number[];
  /** kategori yang dikelola (default filter, L-189). Selalu [] untuk eis1. */
  cats: number[];
}

export function createSessionValue(p: SsoPayload | SsoPayloadV2, key: string, nowSec: number): string {
  // Urutan sisipan = urut abjad (kontrak: byte-exact JSON.stringify).
  if (p.v === 2) {
    const payload = {
      cats: p.cats,
      cids: p.cids,
      codes: p.codes,
      exp: nowSec + SESSION_TTL_SEC,
      iat: nowSec,
      name: p.name,
      roles: p.roles,
      sub: p.sub,
      wids: p.wids,
    };
    const b64 = Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url');
    return `eis2.${b64}.${hmacSha256B64Url(key, `eis2.${b64}`)}`;
  }
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

function parseJson(payloadB64: string): Record<string, unknown> | null {
  try {
    const raw: unknown = JSON.parse(Buffer.from(payloadB64, 'base64url').toString('utf8'));
    return isRecord(raw) ? raw : null;
  } catch {
    return null;
  }
}

function timeOk(iat: number, exp: number, nowSec: number): boolean {
  return !(iat > nowSec + IAT_SKEW || nowSec >= exp || exp - iat < 1 || exp - iat > SESSION_TTL_SEC);
}

export function verifySessionValue(value: unknown, key: string, nowSec: number, acceptV1 = true): Session | null {
  if (typeof value !== 'string') return null;
  if (value.startsWith('eis2.')) return verifyV2(value, key, nowSec);
  if (value.startsWith('eis1.')) return acceptV1 ? verifyV1(value, key, nowSec) : null;
  return null;
}

function verifyV1(value: string, key: string, nowSec: number): Session | null {
  if (!SESSION_VALUE_RE.test(value)) return null;
  const [, payloadB64, sigB64] = value.split('.');
  if (!verifyHmacB64Url(key, `eis1.${payloadB64}`, sigB64)) return null;
  const raw = parseJson(payloadB64);
  if (!raw || !hasExactKeys(raw, SESSION_KEYS)) return null;
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
  if (!timeOk(iat, exp, nowSec)) return null;
  return { v: 1, cids, codes, exp, iat, name, roles, sub, wids: [], cats: [] };
}

function verifyV2(value: string, key: string, nowSec: number): Session | null {
  if (!SESSION_VALUE_RE_V2.test(value)) return null;
  const [, payloadB64, sigB64] = value.split('.');
  if (!verifyHmacB64Url(key, `eis2.${payloadB64}`, sigB64)) return null;
  const raw = parseJson(payloadB64);
  if (!raw || !hasExactKeys(raw, SESSION_KEYS_V2)) return null;
  const sub: unknown = raw.sub;
  const iat: unknown = raw.iat;
  const exp: unknown = raw.exp;
  if (!isSub(sub) || !isInt(iat) || !isInt(exp)) return null;
  const name = parseName(raw.name);
  const roles = parseRolesV2(raw.roles);
  const cids = parseCids(raw.cids);
  const codes = parseCodesV2(raw.codes);
  const wids = parseWids(raw.wids);
  const cats = parseCats(raw.cats);
  if (name === null || roles === null || cids === null || codes === null || wids === null || cats === null) return null;
  if (!cidsMatchRole(cids, roles)) return null;
  if (!scopeConsistent({ codes, roles, wids, cats })) return null;
  if (!timeOk(iat, exp, nowSec)) return null;
  return { v: 2, cids, codes, exp, iat, name, roles, sub, wids, cats };
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
