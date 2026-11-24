/**
 * Asersi aktor `ppa1` insight-web -> prodplan (docs/04 §30.7, ADR-051 §2.3). Modul murni (tanpa I/O; waktu dan jti
 * disuntikkan) agar bisa diuji byte-exact.
 *
 *   header = "ppa1." + payload_b64url + "." + sig_b64url
 *   sig    = HMAC-SHA256(key = byte ASCII string hex prodplan_assert_key, msg = "ppa1." + payload_b64url)
 *   payload = JSON tanpa spasi, TEPAT 11 kunci, urutan: aud, bh, codes, exp, iat, jti, m, name, p, roles, sub
 *
 * Dibuat HANYA dari sesi yang sudah diverifikasi (lib/auth.ts) dan hanya bila sesi memuat peran PP. Browser tidak pernah
 * menerima atau mengirim token ini.
 */
import { createHash, randomBytes } from 'node:crypto';
import { esbCodesOf, ppRolesOf } from './claims';
import { hmacSha256B64Url } from './hmac';

export const ACTOR_PREFIX = 'ppa1';
export const ACTOR_TTL_SEC = 60;
export const ACTOR_AUD = 'prodplan';
export const ACTOR_HEADER = 'X-EFN-Actor';
/** Bentuk header (§30.7): total <= 2048 byte. */
export const ACTOR_RE = /^ppa1\.[A-Za-z0-9_-]{16,1800}\.[A-Za-z0-9_-]{43}$/;
export const ACTOR_MAX_BYTES = 2048;
const NAME_MAX = 64;
const METHODS: ReadonlySet<string> = new Set(['GET', 'POST', 'PUT', 'DELETE', 'PATCH']);

/** Subset sesi terverifikasi yang dipakai membuat asersi. */
export interface ActorSession {
  sub: number;
  name: string;
  codes: readonly string[];
  roles: readonly string[];
}

export class ActorError extends Error {
  constructor(public readonly reason: 'no_pp_role' | 'no_esb_code' | 'bad_method' | 'bad_path' | 'bad_key' | 'bad_session' | 'too_long') {
    super(`actor_${reason}`);
    this.name = 'ActorError';
  }
}

/** base64url (tanpa padding) SHA-256 byte body; body kosong = hash string kosong. */
export function bodyHash(body: string): string {
  return createHash('sha256').update(body, 'utf8').digest('base64url');
}

/** 16 byte acak base64url (22 karakter). */
export function newJti(): string {
  return randomBytes(16).toString('base64url');
}

/** Python `json.dumps(ensure_ascii=True)`: karakter non-ASCII -> \uXXXX (selaras dengan penanda tangan/pemverifikasi Python). */
function asciiJson(v: unknown): string {
  return JSON.stringify(v).replace(/[\u0080-￿]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

export interface ActorInput {
  session: ActorSession;
  method: string;
  /** Path + query PERSIS seperti yang dikirim ke prodplan, diawali `/v1/`. */
  path: string;
  /** String body persis yang dikirim ('' untuk tanpa body). */
  body: string;
  key: string;
  nowSec: number;
  /** Hanya untuk test; produksi memakai newJti(). */
  jti?: string;
}

/** Path sah: awalan `/v1/`, ASCII tercetak tanpa spasi/kontrol/fragment. */
export function isValidActorPath(path: string): boolean {
  return /^\/v1\/[\x21-\x7e]*$/.test(path) && !path.includes('#') && !path.includes('..');
}

/** Payload + kunci urut tetap. Melempar ActorError bila sesi tidak berhak (bukan PP / tanpa kode ESB). */
export function buildActorPayload(i: ActorInput): string {
  const method = i.method.toUpperCase();
  if (!METHODS.has(method)) throw new ActorError('bad_method');
  if (!isValidActorPath(i.path)) throw new ActorError('bad_path');
  if (!Number.isSafeInteger(i.session.sub) || i.session.sub < 1 || !Number.isSafeInteger(i.nowSec)) throw new ActorError('bad_session');
  const roles = ppRolesOf(i.session.roles).slice().sort();
  if (roles.length < 1 || roles.length > 2) throw new ActorError('no_pp_role');
  const codes = esbCodesOf(i.session.codes).slice().sort();
  if (codes.length < 1 || codes.length > 6) throw new ActorError('no_esb_code');
  const name = Array.from(i.session.name).slice(0, NAME_MAX).join('');
  const jti = i.jti ?? newJti();
  // Urutan sisipan literal = urutan kontrak (JSON.stringify mempertahankannya).
  return asciiJson({
    aud: ACTOR_AUD,
    bh: bodyHash(i.body),
    codes,
    exp: i.nowSec + ACTOR_TTL_SEC,
    iat: i.nowSec,
    jti,
    m: method,
    name,
    p: i.path,
    roles,
    sub: i.session.sub,
  });
}

/** Nilai header `X-EFN-Actor`. Melempar ActorError bila kunci tidak sah atau hasil melebihi batas. */
export function signActor(i: ActorInput): string {
  if (!/^[0-9a-fA-F]{64,}$/.test(i.key)) throw new ActorError('bad_key');
  const payloadB64 = Buffer.from(buildActorPayload(i), 'utf8').toString('base64url');
  const msg = `${ACTOR_PREFIX}.${payloadB64}`;
  const header = `${msg}.${hmacSha256B64Url(i.key, msg)}`;
  if (Buffer.byteLength(header, 'ascii') > ACTOR_MAX_BYTES || !ACTOR_RE.test(header)) throw new ActorError('too_long');
  return header;
}
