import { createHash, createHmac } from 'node:crypto';
import { IAT, KEY } from './helpers';

/**
 * Vektor uji eit2 sintetis dari docs/04 §28.13.4 (bukan secret). payload_b64/sig_b64 BELUM dicatat di kontrak: nilai dihitung
 * oleh kode di sini (Node) dan harus dibandingkan lead dengan builder Odoo (`node scripts/print-eit2-vectors.mjs`).
 */
export const PAYLOAD_A_JSON =
  '{"aud":"insight-web","cats":[],"cids":[3,5,11],"codes":["BCF","PB","SMD"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcdef","name":"Budi Uji","roles":["exec","store","user"],"sub":7,"v":2,"wids":[0,20,21,22,23,24,25]}';
export const PAYLOAD_B_JSON =
  '{"aud":"insight-web","cats":[],"cids":[11],"codes":["SMD"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"fedcba9876543210fedcba9876543210","name":"Sari Toko","roles":["store","user"],"sub":9,"v":2,"wids":[22]}';
export const PAYLOAD_C_JSON =
  '{"aud":"insight-web","cats":[4,9],"cids":[11],"codes":["SMD"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"00112233445566778899aabbccddeeff","name":"Rina Buyer","roles":["category","user"],"sub":12,"v":2,"wids":[0,20,21,22,23,24,25]}';

export { IAT, KEY };

export const b64u = (s: string): string => Buffer.from(s, 'ascii').toString('base64url');

export function signV2(json: string, key: string = KEY): string {
  const b64 = b64u(json);
  return `eit2.${b64}.${createHmac('sha256', Buffer.from(key, 'ascii')).update(`eit2.${b64}`, 'ascii').digest('base64url')}`;
}

export const TOKEN_A = signV2(PAYLOAD_A_JSON);
export const TOKEN_B = signV2(PAYLOAD_B_JSON);
export const TOKEN_C = signV2(PAYLOAD_C_JSON);

/** Token eit2 dari payload B dengan `override` (kunci diurutkan abjad seperti sort_keys=True). `drop` menghapus kunci. */
export function makeTokenV2(override: Record<string, unknown> = {}, key: string = KEY, drop: string[] = [], base: string = PAYLOAD_B_JSON): string {
  const merged: Record<string, unknown> = { ...(JSON.parse(base) as Record<string, unknown>), ...override };
  for (const d of drop) delete merged[d];
  const sorted: Record<string, unknown> = {};
  for (const k of Object.keys(merged).sort()) sorted[k] = merged[k];
  const json = JSON.stringify(sorted).replace(/[\u0080-￿]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
  return signV2(json, key);
}

/** SQL_efn_sig2 hitung-ulang independen (Node crypto) untuk uji lintas implementasi. */
export const sig2Of = (key: string, cids: string, codes: string, wids: string, personas: string, cats: string): string =>
  createHash('sha256').update(`${key}|${cids}|${codes}|${wids}|${personas}|${cats}`, 'utf8').digest('hex');
