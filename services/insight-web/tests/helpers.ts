import { createHmac } from 'node:crypto';

/** Vektor uji bersama docs/04 §26.3 (sintetis, bukan secret). */
export const KEY = 'ab'.repeat(32);
export const PAYLOAD_JSON =
  '{"aud":"insight-web","cids":[3,5],"codes":["BCF","PB"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcdef","name":"Budi Uji","roles":["user"],"sub":7,"v":1}';
export const PAYLOAD_B64 =
  'eyJhdWQiOiJpbnNpZ2h0LXdlYiIsImNpZHMiOlszLDVdLCJjb2RlcyI6WyJCQ0YiLCJQQiJdLCJleHAiOjE3OTUwMDAwNjAsImlhdCI6MTc5NTAwMDAwMCwiaXNzIjoib2RvbyIsImp0aSI6IjAxMjM0NTY3ODlhYmNkZWYwMTIzNDU2Nzg5YWJjZGVmIiwibmFtZSI6IkJ1ZGkgVWppIiwicm9sZXMiOlsidXNlciJdLCJzdWIiOjcsInYiOjF9';
export const SIG_OK = 'WSDaF4H2YlaMeKTB73SwsefWHIQi8ywZb8DB2HnGBZc';
export const SIG_HEX_DECODED_KEY = 'i5CARcXxPFdTmGd6vttMWHrYS0VkI0UHJj07imyQJkM';
export const VECTOR_TOKEN = `eit1.${PAYLOAD_B64}.${SIG_OK}`;
export const IAT = 1795000000;

export const BASE_PAYLOAD: Record<string, unknown> = JSON.parse(PAYLOAD_JSON) as Record<string, unknown>;

/** Bangun token eit1 dari payload (kunci diurutkan abjad seperti sort_keys=True); `override` menimpa/menambah kunci. */
export function makeToken(override: Record<string, unknown> = {}, key = KEY, drop: string[] = []): string {
  const merged: Record<string, unknown> = { ...BASE_PAYLOAD, ...override };
  for (const d of drop) delete merged[d];
  const sorted: Record<string, unknown> = {};
  for (const k of Object.keys(merged).sort()) sorted[k] = merged[k];
  // ensure_ascii=True (Python): karakter non-ASCII menjadi \uXXXX.
  const json = JSON.stringify(sorted).replace(/[\u0080-￿]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
  const b64 = Buffer.from(json, 'ascii').toString('base64url');
  const sig = createHmac('sha256', Buffer.from(key, 'ascii')).update(`eit1.${b64}`, 'ascii').digest('base64url');
  return `eit1.${b64}.${sig}`;
}

export function uniqueJti(n: number): string {
  return n.toString(16).padStart(32, '0');
}
