import { Errors } from '../../common/errors/api-error';
import { normalizeMsisdn } from '../../common/pii/pii';
import { asRecord, requireUuid } from '../../common/validation';

/** DTO kontrak §3.2 + §8.1 (ALTCHA). */
export interface AuthOtpRequestDto {
  phone: string;
  /** Payload base64 widget ALTCHA (docs/04 §8.1). */
  altcha: string;
}

export interface AuthOtpVerifyDto {
  challengeId: string;
  code: string;
  /**
   * W-19 (Q-P3): pernyataan pelanggan berusia minimal `MEMBER_MIN_AGE` (17) tahun — checkbox di layar masuk/daftar
   * (login = daftar). Wajib `true`; dicek AuthService SEBELUM OTP diverifikasi agar percobaan tidak hangus.
   */
  ageConfirmed: boolean;
  /** G27 §18.3: consent pemasaran opsional saat daftar/masuk (`{ granted: true, version }`). Tidak ada = perilaku lama. */
  marketingConsent?: { version: string };
  /** G27 §18.3a: persetujuan Kebijakan Privasi (`{ granted: true, version }`); wajib bila PRIVACY_CONSENT_ENFORCED. */
  privacyConsent?: { version: string };
}

const MAX_ALTCHA_LENGTH = 2048;

/**
 * Urutan validasi: `altcha` tidak ada / bukan string → 400 `captcha_required`; nomor bukan Indonesia
 * `^62[0-9]{8,13}$` → 400 `invalid_phone` (SEC-06). Verifikasi kriptografis ALTCHA dilakukan di AuthService
 * (nomor yang tidak valid tidak menghanguskan solusi captcha).
 */
export function parseAuthOtpRequest(body: unknown): AuthOtpRequestDto {
  const o = asRecord(body);
  const altcha = o.altcha;
  if (typeof altcha !== 'string' || altcha.length === 0 || altcha.length > MAX_ALTCHA_LENGTH) throw Errors.captchaRequired();
  const phone = normalizeMsisdn(o.phone);
  if (!phone) throw Errors.invalidPhone();
  return { phone, altcha };
}

/** `{ challenge_id, code, age_confirmed }` — `age_confirmed` tidak ada = false; selain boolean → 400 `invalid_request`. */
export function parseAuthOtpVerify(body: unknown): AuthOtpVerifyDto {
  const o = asRecord(body);
  const code = o.code;
  if (typeof code !== 'string' || code.length > 16) throw Errors.invalidRequest();
  const age = o.age_confirmed;
  if (age !== undefined && age !== null && typeof age !== 'boolean') throw Errors.invalidRequest();
  const marketingConsent = parseGrantedConsent(o.marketing_consent);
  const privacyConsent = parseGrantedConsent(o.privacy_consent);
  return {
    challengeId: requireUuid(o, 'challenge_id'),
    code: code.trim(),
    ageConfirmed: age === true,
    ...(marketingConsent ? { marketingConsent } : {}),
    ...(privacyConsent ? { privacyConsent } : {}),
  };
}

/** `{ granted: true, version }` → `{ version }`; tidak ada → undefined; bentuk lain → 400. */
function parseGrantedConsent(v: unknown): { version: string } | undefined {
  if (v === undefined || v === null) return undefined;
  const m = typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  if (!m || m.granted !== true || typeof m.version !== 'string' || !/^[A-Za-z0-9._@-]{1,32}$/.test(m.version)) throw Errors.invalidRequest();
  return { version: m.version };
}
