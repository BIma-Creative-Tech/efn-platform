// Mencetak payload_b64, sig_b64, dan SQL_efn_sig3 untuk vektor uji docs/04 §28.15.5 / §28.15.6 (sintetis, bukan secret).
// Jalankan: node services/insight-web/scripts/print-eit3-vectors.mjs
// Lead membandingkan keluaran ini dengan builder Odoo (Python, prosedur §26.3 dengan prefix "eit3.") dan harness ClickHouse
// (SHA256 KEY|cids|codes|brs), lalu menuliskan hasilnya di docs/04 sebelum merge. Hanya node:crypto; tidak membaca file/secret.
import { createHash, createHmac } from 'node:crypto';
import process from 'node:process';

const KEY = 'ab'.repeat(32); // byte ASCII string hex (BUKAN hex-decode)

const PAYLOADS = {
  D: '{"aud":"insight-web","brs":["pb:12","pb:7"],"cats":[],"cids":[5],"codes":["PB"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcd01","name":"Andi Toko","roles":["store","user"],"sub":21,"v":3,"wids":[]}',
  E: '{"aud":"insight-web","brs":[],"cats":[],"cids":[3,5,11],"codes":["BCF","PB","SMD"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcd02","name":"Budi Uji","roles":["exec","user"],"sub":7,"v":3,"wids":[0,20,21,22,23,24,25]}',
  F: '{"aud":"insight-web","brs":[],"cats":[],"cids":[5,6],"codes":["PB","WTZ"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcd03","name":"Maya Promo","roles":["marketing","user"],"sub":30,"v":3,"wids":[]}',
  G: '{"aud":"insight-web","brs":["bacha:15"],"cats":[],"cids":[4],"codes":["BCR"],"exp":1795000060,"iat":1795000000,"iss":"odoo","jti":"0123456789abcdef0123456789abcd04","name":"Sinta Toko","roles":["store","user"],"sub":31,"v":3,"wids":[]}',
};

const out = (s) => process.stdout.write(`${s}\n`);

for (const [name, json] of Object.entries(PAYLOADS)) {
  const b64 = Buffer.from(json, 'ascii').toString('base64url');
  const sig = createHmac('sha256', Buffer.from(KEY, 'ascii')).update(`eit3.${b64}`, 'ascii').digest('base64url');
  out(`# vektor ${name}`);
  out(`payload_b64 = ${b64}`);
  out(`sig_b64     = ${sig}`);
  out(`token       = eit3.${b64}.${sig}`);
  out('');
}

const sig3 = (cids, codes, brs) => createHash('sha256').update(`${KEY}|${cids}|${codes}|${brs}`, 'utf8').digest('hex');

out('# SQL_efn_sig3 (lower(hex(SHA256(KEY|cids|codes|brs))))');
out(`D: ${sig3('5', 'PB', 'pb:12,pb:7')}   (5|PB|pb:12,pb:7)`);
out(`E: ${sig3('3,5,11', 'BCF,PB', '')}   (3,5,11|BCF,PB|)`);
