/**
 * Sesi modul Produksi per pengguna ESB (docs/04 §30.17, ADR-051 §8). Murni (tanpa I/O).
 *
 * - Cookie `__Host-epp=<sid>` (HttpOnly; Secure; SameSite=Strict; Path=/) TERPISAH dari `__Host-eis` (Odoo). Hanya
 *   `/produksi*` dan `/api/prodplan/*` yang boleh membacanya; modul lain (gerbang Odoo) tidak pernah menyentuhnya.
 * - insight-web tidak menyimpan apa pun tentang sesi selain meneruskan cookie; prodplan = otoritas identitas.
 * - `SessionView` (respons `GET/POST /v1/session`) = sumber nama, peran, cabang, dan sisa waktu untuk UI.
 */
import { isPpRole, type PpRole } from './claims';

export const PP_COOKIE = '__Host-epp';
/** `sid` = 32 byte acak base64url (`secrets.token_urlsafe(32)`), 43 karakter (§30.17.2). Tanpa import node:crypto: modul ini juga dipakai komponen klien. */
export const SID_RE = /^[A-Za-z0-9_-]{43}$/;
/** Sesi ESB maksimal 1 jam (§30.17.4). */
export const PP_SESSION_MAX_SEC = 3600;
/** Dipakai bila `expires_at` dari prodplan tidak terbaca (token ESB ~3540 dtk, §30.17.3 langkah 5). */
export const PP_COOKIE_FALLBACK_SEC = 3540;
const ATTRS = 'Path=/; HttpOnly; Secure; SameSite=Strict';

export const isPpSid = (v: unknown): v is string => typeof v === 'string' && SID_RE.test(v);

export function ppSetCookie(sid: string, maxAgeSec: number): string {
  const age = Math.min(PP_SESSION_MAX_SEC, Math.max(1, Math.floor(maxAgeSec)));
  return `${PP_COOKIE}=${sid}; Max-Age=${age}; ${ATTRS}`;
}

export function ppClearCookie(): string {
  return `${PP_COOKIE}=; Max-Age=0; ${ATTRS}`;
}

/** Ambil `sid` sah dari header `Cookie` mentah; undefined bila tidak ada atau bentuknya salah. */
export function ppSidFromHeader(header: string | null | undefined): string | undefined {
  if (!header) return undefined;
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    if (part.slice(0, i).trim() === PP_COOKIE) {
      const v = part.slice(i + 1).trim();
      return isPpSid(v) ? v : undefined;
    }
  }
  return undefined;
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);

export interface SessionView {
  /** `fullName` ESB (maks 64). */
  name: string;
  esb_env: string;
  esb_brand: string;
  company_code: string;
  roles: PpRole[];
  doc_access: { insert: string[]; delete: string[] };
  branches: { branch_id: number; name: string }[];
  /** Detik epoch kedaluwarsa sesi; null bila tidak terbaca. */
  expires_at_sec: number | null;
  /** id aktor (opsional; ASUMSI, tidak ada di kontrak): dipakai hanya untuk petunjuk pemisahan tugas di UI. */
  actor_id: number | null;
}

/** `expires_at`: ISO 8601, atau angka detik/milidetik epoch. Selain itu null. */
export function parseExpiresAt(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v) && v > 0) return Math.floor(v > 1e11 ? v / 1000 : v);
  if (typeof v === 'string' && v !== '') {
    const t = Date.parse(v);
    if (!Number.isNaN(t)) return Math.floor(t / 1000);
  }
  return null;
}

export function parseSessionView(raw: unknown): SessionView | null {
  if (!isObj(raw)) return null;
  const name = typeof raw.name === 'string' ? raw.name.slice(0, 64) : '';
  if (name === '') return null;
  const da = isObj(raw.doc_access) ? raw.doc_access : {};
  const branches = (Array.isArray(raw.branches) ? raw.branches : [])
    .filter(isObj)
    .map((b) => ({ branch_id: typeof b.branch_id === 'number' ? b.branch_id : 0, name: typeof b.name === 'string' ? b.name : '' }))
    .filter((b) => b.branch_id > 0)
    .slice(0, 200);
  return {
    name,
    esb_env: typeof raw.esb_env === 'string' ? raw.esb_env : 'unknown',
    esb_brand: typeof raw.esb_brand === 'string' ? raw.esb_brand : '',
    company_code: typeof raw.company_code === 'string' ? raw.company_code : '',
    roles: strs(raw.roles).filter(isPpRole),
    doc_access: { insert: strs(da.insert), delete: strs(da.delete) },
    branches,
    expires_at_sec: parseExpiresAt(raw.expires_at),
    actor_id: typeof raw.actor_id === 'number' && Number.isSafeInteger(raw.actor_id) && raw.actor_id > 0 ? raw.actor_id : null,
  };
}

/** Respons 201 login: `{sid, expires_at, session}`. Null bila sid/sesi tidak sah. */
export function parseLoginResult(raw: unknown): { sid: string; expiresAtSec: number | null; session: SessionView } | null {
  if (!isObj(raw) || !isPpSid(raw.sid)) return null;
  const session = parseSessionView(raw.session);
  if (!session) return null;
  return { sid: raw.sid, expiresAtSec: parseExpiresAt(raw.expires_at) ?? session.expires_at_sec, session };
}

// ---------------------------------------------------------------- login: kode galat -> `?e=` + pesan (§30.17.5)

export const LOGIN_ERRORS = ['invalid', 'locked', 'no_access', 'unavailable', 'rate', 'expired'] as const;
export type LoginError = (typeof LOGIN_ERRORS)[number];

export const LOGIN_ERROR_MESSAGES: Readonly<Record<LoginError, string>> = {
  invalid: 'Username atau password ESB salah.',
  locked: 'Terlalu banyak percobaan masuk. Coba lagi dalam 15 menit.',
  no_access: 'Akun ESB ini tidak memiliki izin modul Produksi.',
  unavailable: 'ESB sedang tidak dapat dihubungi. Coba beberapa saat lagi.',
  rate: 'Terlalu banyak percobaan masuk. Coba lagi dalam 15 menit.',
  expired: 'Sesi ESB berakhir (maksimal 1 jam). Silakan masuk lagi.',
};

/** Nilai `?e=` -> kode sah, atau null (nilai lain diabaikan). */
export function parseLoginError(v: string | undefined): LoginError | null {
  return LOGIN_ERRORS.find((e) => e === v) ?? null;
}

/** Kode galat prodplan -> `e`. Tidak membedakan "user tidak ada" vs "password salah". Tak dikenal = `unavailable`. */
export function loginErrorFor(code: string | undefined, status: number): LoginError {
  switch (code) {
    case 'invalid_credentials':
    case 'invalid_request':
      return 'invalid';
    case 'login_locked':
      return 'locked';
    case 'no_pp_access':
    case 'esb_company_not_allowed':
      return 'no_access';
    case 'session_expired':
      return 'expired';
    default:
      return status === 429 ? 'locked' : 'unavailable';
  }
}

/** Bentuk username (§30.17.2): trim lalu `^[A-Za-z0-9._@-]{1,64}$`. Null bila tidak sah. */
export function normalizeUsername(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  const t = v.trim();
  return /^[A-Za-z0-9._@-]{1,64}$/.test(t) ? t : null;
}

/** Password: 1..128 karakter, TIDAK dipangkas/diubah. */
export function validPassword(v: unknown): v is string {
  return typeof v === 'string' && v.length >= 1 && Array.from(v).length <= 128;
}

/** Sisa waktu sesi (detik) -> "42 menit", "5 menit", "kurang dari 1 menit", "berakhir". */
export function remainingLabel(expiresAtSec: number | null, nowSec: number): string {
  if (expiresAtSec === null) return '-';
  const left = expiresAtSec - nowSec;
  if (left <= 0) return 'berakhir';
  if (left < 60) return 'kurang dari 1 menit';
  return `${Math.floor(left / 60)} menit`;
}
