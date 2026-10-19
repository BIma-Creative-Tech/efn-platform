import { createCipheriv, createHash, createHmac, hkdfSync } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { PII_IV_BYTES, PII_TAG_BYTES, PII_VERSION_V1, PiiCipher } from './crypto';

/**
 * G21 docs/04 §13.2 AC-2 (ADR-036 §0.3, L-39 b): vektor bersama BFF ↔ Odoo. File JSON SAMA yang diuji Odoo
 * (`services/odoo/addons/efn_pii/tests/data/efn_pii_shared_vectors.json`, dibangkitkan `efn_pii/tools/shared_vectors.py`).
 * Kunci & IV uji = SHA-256 label PUBLIK (bukan rahasia; tidak ada kunci di repo).
 *
 * Yang dibuktikan: format blob biner BFF (`PiiCipher`: versi(1) ‖ IV(12) ‖ ciphertext ‖ tag(16), AES-256-GCM, AAD UTF-8)
 * IDENTIK dengan payload base64 setelah prefiks `efnpii1:`/`efnpii2:` di Odoo — BFF bisa mendekripsi blob Odoo, dan blob
 * yang dibangun dengan tata letak BFF + IV vektor sama byte-per-byte dengan `expected_text`. AAD Odoo (`efn:odoo:…`)
 * berbeda ruang nama dengan AAD kolom BFF (`efn:<tabel>.<kolom>:v1`) — sengaja (tidak bisa dipertukarkan antar-sistem).
 *
 * Path: relatif root repo (`EFN_PII_SHARED_VECTORS` bisa menimpa). File tidak ada (mis. container hanya me-mount
 * services/api-gateway) → suite DILEWATI dengan nama yang menjelaskan sebabnya. `expected_*` masih null (generator
 * belum dijalankan) → hanya sifat format/round-trip yang diuji + peringatan.
 */
const DEFAULT_PATH = resolve(__dirname, '../../../../..', 'services/odoo/addons/efn_pii/tests/data/efn_pii_shared_vectors.json');
const VECTORS_PATH = process.env.EFN_PII_SHARED_VECTORS ?? DEFAULT_PATH;

interface AeadVector {
  name: string;
  table: string;
  column: string;
  record_id: number | null;
  aad: string;
  plaintext: string;
  iv_label: string;
  expected_text: string | null;
}
interface SharedVectors {
  labels: { aes_key: string; bidx_ikm: string; name_ikm: string };
  key_version: number;
  aead: AeadVector[];
  bidx: { field: string; info: string; normalized: string; expected_hex: string | null }[];
  name_tokens: { kind: string; info: string; normalized: string; expected: string | null }[];
}

const sha256 = (label: string): Buffer => createHash('sha256').update(label, 'utf8').digest();

/** Tata letak blob BFF (`PiiCipher.encrypt`) dengan IV yang ditentukan (deterministik untuk vektor). */
function bffLayoutBlob(key: Buffer, version: number, iv: Buffer, plaintext: string, aad: string): Buffer {
  const c = createCipheriv('aes-256-gcm', key, iv, { authTagLength: PII_TAG_BYTES });
  c.setAAD(Buffer.from(aad, 'utf8'));
  const ct = Buffer.concat([c.update(plaintext, 'utf8'), c.final()]);
  return Buffer.concat([Buffer.from([version]), iv, ct, c.getAuthTag()]);
}

const present = existsSync(VECTORS_PATH);
const suite = present ? describe : describe.skip;
const title = present
  ? 'AC-2 vektor bersama BFF ↔ Odoo (format blob PII identik)'
  : `AC-2 vektor bersama DILEWATI: file tidak ditemukan di ${VECTORS_PATH} (set EFN_PII_SHARED_VECTORS atau jalankan dari checkout repo lengkap)`;

suite(title, () => {
  const data = present ? (JSON.parse(readFileSync(VECTORS_PATH, 'utf8')) as SharedVectors) : (null as unknown as SharedVectors);
  const key = present ? sha256(data.labels.aes_key) : Buffer.alloc(32);
  const cipher = new PiiCipher(new Map([[data?.key_version ?? 1, key]]), data?.key_version ?? 1);
  const pending = present ? data.aead.filter((v) => v.expected_text === null).map((v) => v.name) : [];

  it('versi kunci & panjang header sama dengan konstanta BFF', () => {
    expect(data.key_version).toBe(PII_VERSION_V1);
    expect(PII_IV_BYTES).toBe(12);
    expect(PII_TAG_BYTES).toBe(16);
    if (pending.length > 0) {
      console.warn(`AC-2: expected_text masih null untuk ${pending.join(', ')} — jalankan generator efn_pii/tools/shared_vectors.py (lead)`);
    }
  });

  const rows: [string, AeadVector][] = present ? data.aead.map((v): [string, AeadVector] => [v.name, v]) : [['(kosong)', null as unknown as AeadVector]];
  it.each<[string, AeadVector]>(rows)(
    'AEAD %s: AAD sesuai format, tata letak BFF round-trip, dan (bila ada) identik dengan expected_text Odoo',
    (_name, v) => {
      const expectedAad = v.record_id === null ? `efn:odoo:${v.table}.${v.column}:v1` : `efn:odoo:${v.table}.${v.column}:${v.record_id}:v2`;
      expect(v.aad).toBe(expectedAad);
      const iv = sha256(v.iv_label).subarray(0, PII_IV_BYTES);
      const blob = bffLayoutBlob(key, data.key_version, iv, v.plaintext, v.aad);
      expect(blob.length).toBe(1 + PII_IV_BYTES + Buffer.byteLength(v.plaintext, 'utf8') + PII_TAG_BYTES);
      expect(blob[0]).toBe(data.key_version);
      expect(cipher.decrypt(blob, v.aad)).toBe(v.plaintext);
      // AAD lain (mis. record id berbeda / AAD kolom BFF) → gagal autentikasi
      expect(() => cipher.decrypt(blob, `${v.aad}x`)).toThrow();
      if (v.expected_text === null) return;
      const prefix = v.record_id === null ? 'efnpii1:' : 'efnpii2:';
      expect(v.expected_text.startsWith(prefix)).toBe(true);
      const odooBlob = Buffer.from(v.expected_text.slice(prefix.length), 'base64');
      expect(odooBlob.toString('base64')).toBe(v.expected_text.slice(prefix.length)); // base64 standar kanonik
      expect(cipher.decrypt(odooBlob, v.aad)).toBe(v.plaintext);
      expect(odooBlob.equals(blob)).toBe(true);
    },
  );

  it('blind index & token nama (bila expected tersedia) = HKDF-SHA256 + HMAC-SHA256 seperti dokumentasi generator', () => {
    const bidxIkm = sha256(data.labels.bidx_ikm);
    for (const b of data.bidx) {
      const sub = Buffer.from(hkdfSync('sha256', bidxIkm, Buffer.alloc(0), b.info, 32));
      const hex = createHmac('sha256', sub).update(b.normalized, 'utf8').digest('hex');
      expect(hex).toMatch(/^[0-9a-f]{64}$/);
      if (b.expected_hex !== null) expect(hex).toBe(b.expected_hex);
    }
    const nameIkm = sha256(data.labels.name_ikm);
    for (const t of data.name_tokens) {
      const sub = Buffer.from(hkdfSync('sha256', nameIkm, Buffer.alloc(0), t.info, 32));
      const tok = createHmac('sha256', sub).update(t.normalized, 'utf8').digest().subarray(0, 16).toString('base64url');
      expect(tok).toMatch(/^[A-Za-z0-9_-]{22}$/);
      if (t.expected !== null) expect(tok).toBe(t.expected);
    }
  });
});
