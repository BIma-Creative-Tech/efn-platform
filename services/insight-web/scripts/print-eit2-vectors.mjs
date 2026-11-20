// Mencetak payload_b64, sig_b64, dan SQL_efn_sig2 untuk vektor uji docs/04 §28.13.4 / §28.13.6 (sintetis, bukan secret).
// Jalankan: node services/insight-web/scripts/print-eit2-vectors.mjs
// Lead membandingkan keluaran ini dengan builder Odoo (Python, prosedur §26.3 dengan prefix "eit2.") dan menuliskan hasilnya
// di docs/04 sebelum merge. Hanya node:crypto; tidak membaca file/secret.
import { createHash, createHmac } from 'node:crypto';
import process from 'node:process';

const KEY = 'ab'.repeat(32); // byte ASCII string hex (BUKAN hex-decode)

const PAYLOADS = {
  A: '{"aud":"insight-web","cats":[],"cids":[3,5,11],"codes":["BCF","PB","SMD"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcdef","name":"Budi Uji","roles":["exec","store","user"],"sub":7,"v":2,"wids":[0,20,21,22,23,24,25]}',
  B: '{"aud":"insight-web","cats":[],"cids":[11],"codes":["SMD"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"fedcba9876543210fedcba9876543210","name":"Sari Toko","roles":["store","user"],"sub":9,"v":2,"wids":[22]}',
  C: '{"aud":"insight-web","cats":[4,9],"cids":[11],"codes":["SMD"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"00112233445566778899aabbccddeeff","name":"Rina Buyer","roles":["category","user"],"sub":12,"v":2,"wids":[0,20,21,22,23,24,25]}',
};

const out = (s) => process.stdout.write(`${s}\n`);

for (const [name, json] of Object.entries(PAYLOADS)) {
  const b64 = Buffer.from(json, 'ascii').toString('base64url');
  const sig = createHmac('sha256', Buffer.from(KEY, 'ascii')).update(`eit2.${b64}`, 'ascii').digest('base64url');
  out(`# vektor ${name}`);
  out(`payload_b64 = ${b64}`);
  out(`sig_b64     = ${sig}`);
  out(`token       = eit2.${b64}.${sig}`);
  out('');
}

const sig2 = (cids, codes, wids, personas, cats) =>
  createHash('sha256').update(`${KEY}|${cids}|${codes}|${wids}|${personas}|${cats}`, 'utf8').digest('hex');

out('# SQL_efn_sig2 (lower(hex(SHA256(KEY|cids|codes|wids|personas|cats))))');
out(`A: ${sig2('3,5,11', 'SMD', '0,20,21,22,23,24,25', 'exec,store', '')}   (cids|codes|wids|personas|cats = 3,5,11|SMD|0,20,21,22,23,24,25|exec,store|)`);
out(`C: ${sig2('11', 'SMD', '0,20,21,22,23,24,25', 'category', '4,9')}   (11|SMD|0,20,21,22,23,24,25|category|4,9)`);
