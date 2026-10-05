import { randomBytes } from 'node:crypto';
import { testConfig } from '../testing/fakes';
import { PII_AAD, PII_IV_BYTES, PII_TAG_BYTES, PiiCipher, PiiDecryptError, piiAad } from './crypto';

const AAD = PII_AAD.PRICE_HUNTER_REPORT_USER;
const MEMBER = '6281234567890';

describe('PiiCipher (AES-256-GCM, docs/04 §7.2)', () => {
  const cipher = new PiiCipher(randomBytes(32));

  it('round-trip; format = 0x01 ‖ IV 12 ‖ ciphertext ‖ tag 16', () => {
    const blob = cipher.encrypt(MEMBER, AAD);
    expect(blob[0]).toBe(0x01);
    expect(blob.length).toBe(1 + PII_IV_BYTES + Buffer.byteLength(MEMBER) + PII_TAG_BYTES);
    expect(blob.includes(Buffer.from(MEMBER))).toBe(false);
    expect(cipher.decrypt(blob, AAD)).toBe(MEMBER);
  });

  it('IV acak: plaintext sama → ciphertext berbeda', () => {
    expect(cipher.encrypt(MEMBER, AAD).equals(cipher.encrypt(MEMBER, AAD))).toBe(false);
  });

  it('AAD sesuai kontrak: efn:<tabel>.<kolom>:v1', () => {
    expect(PII_AAD.PRICE_HUNTER_REPORT_USER).toBe('efn:price_hunter_report.user_enc:v1');
    expect(PII_AAD.LOYALTY_LEDGER_MEMBER_REF).toBe('efn:loyalty_ledger.member_ref_enc:v1');
    expect(PII_AAD.OTP_AUDIT_IP).toBe('efn:otp_audit.ip_enc:v1');
    expect(() => piiAad('Platform.x', 'y')).toThrow();
  });

  it('AAD salah (nilai disalin ke kolom lain) → gagal', () => {
    const blob = cipher.encrypt(MEMBER, AAD);
    expect(() => cipher.decrypt(blob, PII_AAD.LOYALTY_LEDGER_MEMBER_REF)).toThrow(PiiDecryptError);
  });

  it.each([
    ['IV', 1],
    ['ciphertext', 1 + PII_IV_BYTES],
    ['tag', -1],
  ])('byte %s dimodifikasi → gagal', (_part, offset) => {
    const blob = cipher.encrypt(MEMBER, AAD);
    const i = offset < 0 ? blob.length + offset : offset;
    blob[i] ^= 0x01;
    expect(() => cipher.decrypt(blob, AAD)).toThrow(PiiDecryptError);
  });

  it('versi tidak dikenal / terlalu pendek → gagal', () => {
    const blob = cipher.encrypt(MEMBER, AAD);
    blob[0] = 0x02;
    expect(() => cipher.decrypt(blob, AAD)).toThrow('versi ciphertext PII tidak dikenal');
    expect(() => cipher.decrypt(Buffer.alloc(10), AAD)).toThrow(PiiDecryptError);
  });

  it('kunci lain → gagal; pesan error tidak memuat plaintext', () => {
    const blob = cipher.encrypt(MEMBER, AAD);
    let msg = '';
    try {
      new PiiCipher(randomBytes(32)).decrypt(blob, AAD);
    } catch (err) {
      msg = (err as Error).message;
    }
    expect(msg).not.toBe('');
    expect(msg).not.toContain(MEMBER);
  });

  it('key ring: data v1 tetap terbaca setelah versi aktif = 2; enkripsi baru memakai byte versi 2', () => {
    const k1 = randomBytes(32).toString('base64');
    const k2 = randomBytes(32).toString('base64');
    const v1Only = PiiCipher.fromConfig(testConfig({ PII_ENCRYPTION_KEY: k1 }));
    const oldBlob = v1Only.encrypt(MEMBER, AAD);
    expect(oldBlob[0]).toBe(0x01);

    const rotated = PiiCipher.fromConfig(
      testConfig({ PII_ENCRYPTION_KEY: k1, PII_ENCRYPTION_KEY_V2: k2, PII_ACTIVE_KEY_VERSION: '2' }),
    );
    expect(rotated.decrypt(oldBlob, AAD)).toBe(MEMBER);
    const newBlob = rotated.encrypt(MEMBER, AAD);
    expect(newBlob[0]).toBe(0x02);
    expect(rotated.decrypt(newBlob, AAD)).toBe(MEMBER);
    // cipher tanpa kunci v2 tidak bisa membaca data v2 (versi tanpa kunci → error)
    expect(() => v1Only.decrypt(newBlob, AAD)).toThrow('versi ciphertext PII tidak dikenal');
  });

  it('PII_ACTIVE_KEY_VERSION tanpa kunci → ConfigError', () => {
    expect(() => testConfig({ PII_ACTIVE_KEY_VERSION: '2' }).piiActiveKeyVersion).toThrow('PII_ACTIVE_KEY_VERSION=2');
  });

  it('kunci bukan 32 byte ditolak; fromConfig membaca PII_ENCRYPTION_KEY base64', () => {
    expect(() => new PiiCipher(randomBytes(16))).toThrow();
    const key = randomBytes(32);
    const c = PiiCipher.fromConfig(testConfig({ PII_ENCRYPTION_KEY: key.toString('base64') }));
    expect(new PiiCipher(key).decrypt(c.encrypt('x', AAD), AAD)).toBe('x');
    expect(() => PiiCipher.fromConfig(testConfig({ PII_ENCRYPTION_KEY: randomBytes(16).toString('base64') }))).toThrow(
      'PII_ENCRYPTION_KEY harus 32 byte',
    );
  });
});
