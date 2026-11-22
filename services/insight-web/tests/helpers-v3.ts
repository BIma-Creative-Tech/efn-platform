import { createHash, createHmac } from 'node:crypto';
import { IAT, KEY } from './helpers';

/**
 * Vektor uji eit3 sintetis D..G dari docs/04 §28.15.5 (bukan secret), salinan persis dari kontrak. payload_b64/sig_b64 BELUM
 * dicatat di kontrak (prosedur §26.3: dicatat lead setelah builder Python dan Node cocok); nilai dihitung di sini (Node).
 * Bila `services/odoo/addons/efn_insight/tests/vectors_g52.json` muncul, bandingkan keluarannya dengan nilai di sini.
 */
export const PAYLOAD_D_JSON =
  '{"aud":"insight-web","brs":["pb:12","pb:7"],"cats":[],"cids":[5],"codes":["PB"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcd01","name":"Andi Toko","roles":["store","user"],"sub":21,"v":3,"wids":[]}';
export const PAYLOAD_E_JSON =
  '{"aud":"insight-web","brs":[],"cats":[],"cids":[3,5,11],"codes":["BCF","PB","SMD"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcd02","name":"Budi Uji","roles":["exec","user"],"sub":7,"v":3,"wids":[0,20,21,22,23,24,25]}';
export const PAYLOAD_F_JSON =
  '{"aud":"insight-web","brs":[],"cats":[],"cids":[5,6],"codes":["PB","WTZ"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcd03","name":"Maya Promo","roles":["marketing","user"],"sub":30,"v":3,"wids":[]}';
export const PAYLOAD_G_JSON =
  '{"aud":"insight-web","brs":["bacha:15"],"cats":[],"cids":[4],"codes":["BCR"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcd04","name":"Sinta Toko","roles":["store","user"],"sub":31,"v":3,"wids":[]}';

export { IAT, KEY };

export const b64u = (s: string): string => Buffer.from(s, 'ascii').toString('base64url');

export function signV3(json: string, key: string = KEY, prefix = 'eit3'): string {
  const b64 = b64u(json);
  return `${prefix}.${b64}.${createHmac('sha256', Buffer.from(key, 'ascii')).update(`${prefix}.${b64}`, 'ascii').digest('base64url')}`;
}

export const TOKEN_D = signV3(PAYLOAD_D_JSON);
export const TOKEN_E = signV3(PAYLOAD_E_JSON);
export const TOKEN_F = signV3(PAYLOAD_F_JSON);
export const TOKEN_G = signV3(PAYLOAD_G_JSON);

/** Token eit3 dari payload `base` dengan `override` (kunci diurutkan abjad seperti sort_keys=True). `drop` menghapus kunci. */
export function makeTokenV3(override: Record<string, unknown> = {}, base: string = PAYLOAD_D_JSON, drop: string[] = [], key: string = KEY): string {
  const merged: Record<string, unknown> = { ...(JSON.parse(base) as Record<string, unknown>), ...override };
  for (const d of drop) delete merged[d];
  const sorted: Record<string, unknown> = {};
  for (const k of Object.keys(merged).sort()) sorted[k] = merged[k];
  const json = JSON.stringify(sorted).replace(/[\u0080-￿]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
  return signV3(json, key);
}

/** Hitung-ulang independen SHA256 hex (Node crypto) untuk uji sig3 lintas implementasi. */
export const sha256Of = (s: string): string => createHash('sha256').update(s, 'utf8').digest('hex');
