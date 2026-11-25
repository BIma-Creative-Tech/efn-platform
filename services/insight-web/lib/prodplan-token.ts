/**
 * Asersi aktor `ppa2` insight-web -> prodplan (docs/04 §30.17.7, ADR-051 §8). Menggantikan `ppa1`. Modul murni (tanpa I/O;
 * waktu dan jti disuntikkan) agar bisa diuji byte-exact.
 *
 *   header  = "ppa2." + payload_b64url + "." + sig_b64url
 *   sig     = HMAC-SHA256(key = byte ASCII string hex prodplan_assert_key, msg = "ppa2." + payload_b64url)
 *   payload = JSON tanpa spasi, TEPAT 8 kunci, urutan: aud, bh, exp, iat, jti, m, p, sid
 *
 * Asersi TIDAK membawa identitas/peran/cabang: semuanya hanya dari sesi prodplan (di-lookup lewat `sid` = isi cookie
 * `__Host-epp`). `sid` kosong ("") hanya sah untuk `POST /v1/session` (login) dan `GET /v1/meta` (banner lingkungan).
 * Browser tidak pernah menerima atau mengirim token ini.
 */
import { createHash, randomBytes } from 'node:crypto';
import { hmacSha256B64Url } from './hmac';
import { SID_RE } from './prodplan-session';

export const ACTOR_PREFIX = 'ppa2';
export const ACTOR_TTL_SEC = 60;
export const ACTOR_AUD = 'prodplan';
export const ACTOR_HEADER = 'X-EFN-Actor';
/** Bentuk header (§30.17.7): total <= 2048 byte. */
export const ACTOR_RE = /^ppa2\.[A-Za-z0-9_-]{16,1800}\.[A-Za-z0-9_-]{43}$/;
export const ACTOR_MAX_BYTES = 2048;
const METHODS: ReadonlySet<string> = new Set(['GET', 'POST', 'PUT', 'DELETE', 'PATCH']);

/** Sesi yang dipakai membuat asersi: hanya `sid` ("" untuk login / meta tanpa sesi). */
export interface ActorSession {
  sid: string;
}

export class ActorError extends Error {
  constructor(public readonly reason: 'bad_method' | 'bad_path' | 'bad_key' | 'bad_sid' | 'too_long') {
    super(`actor_${reason}`);
    this.name = 'ActorError';
  }
}

/** base64url (tanpa padding) SHA-256 byte body; body kosong = hash string kosong. */
export function bodyHash(body: string): string {
  return createHash('sha256').update(body, 'utf8').digest('base64url');
}

/** 16 byte acak base64url (22 karakter). Dipakai sebagai `jti` asersi dan `n` body login. */
export function newJti(): string {
  return randomBytes(16).toString('base64url');
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

/** `sid` kosong hanya untuk dua panggilan tanpa sesi (§30.17.7). */
export function sidlessAllowed(method: string, path: string): boolean {
  return (method === 'POST' && path === '/v1/session') || (method === 'GET' && path === '/v1/meta');
}

/** Payload + kunci urut tetap. Melempar ActorError bila method/path/sid tidak sah. */
export function buildActorPayload(i: ActorInput): string {
  const method = i.method.toUpperCase();
  if (!METHODS.has(method)) throw new ActorError('bad_method');
  if (!isValidActorPath(i.path)) throw new ActorError('bad_path');
  if (!Number.isSafeInteger(i.nowSec)) throw new ActorError('bad_path');
  const sid = i.session.sid;
  if (sid === '' ? !sidlessAllowed(method, i.path) : !SID_RE.test(sid)) throw new ActorError('bad_sid');
  const jti = i.jti ?? newJti();
  // Urutan sisipan literal = urutan kontrak (JSON.stringify mempertahankannya). Semua nilai ASCII (sid base64url, path ASCII).
  return JSON.stringify({
    aud: ACTOR_AUD,
    bh: bodyHash(i.body),
    exp: i.nowSec + ACTOR_TTL_SEC,
    iat: i.nowSec,
    jti,
    m: method,
    p: i.path,
    sid,
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
