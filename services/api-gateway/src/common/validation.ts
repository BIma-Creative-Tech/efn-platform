import { Errors } from './errors/api-error';

/** Validasi input manual (tanpa class-validator) — semua kegagalan → 400 `invalid_request` generik. */

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS_RE = /[\u0000-\u001f\u007f]/;

export function isUuid(v: unknown): v is string {
  return typeof v === 'string' && UUID_RE.test(v);
}

export function asRecord(body: unknown): Record<string, unknown> {
  if (typeof body !== 'object' || body === null || Array.isArray(body)) throw Errors.invalidRequest();
  return body as Record<string, unknown>;
}

export function requireString(obj: Record<string, unknown>, key: string, maxLength: number): string {
  const v = obj[key];
  if (typeof v !== 'string') throw Errors.invalidRequest();
  const s = v.trim();
  if (s.length === 0 || s.length > maxLength || CONTROL_CHARS_RE.test(s)) throw Errors.invalidRequest();
  return s;
}

export function optionalString(obj: Record<string, unknown>, key: string, maxLength: number): string | null {
  const v = obj[key];
  if (v === undefined || v === null || (typeof v === 'string' && v.trim() === '')) return null;
  return requireString(obj, key, maxLength);
}

export function requireUuid(obj: Record<string, unknown>, key: string): string {
  const v = obj[key];
  if (!isUuid(v)) throw Errors.invalidRequest();
  return v.toLowerCase();
}

export function requireInt(obj: Record<string, unknown>, key: string, min: number, max: number): number {
  const v = obj[key];
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min || v > max) throw Errors.invalidRequest();
  return v;
}

export function requireNumber(obj: Record<string, unknown>, key: string, min: number, max: number): number {
  const v = obj[key];
  if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max) throw Errors.invalidRequest();
  return v;
}
