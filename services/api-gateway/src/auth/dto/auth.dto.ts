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

export function parseAuthOtpVerify(body: unknown): AuthOtpVerifyDto {
  const o = asRecord(body);
  const code = o.code;
  if (typeof code !== 'string' || code.length > 16) throw Errors.invalidRequest();
  return { challengeId: requireUuid(o, 'challenge_id'), code: code.trim() };
}
