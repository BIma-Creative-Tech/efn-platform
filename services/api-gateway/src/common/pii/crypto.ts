import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import type { AppConfig } from '../../config/app-config';

/**
 * Enkripsi kolom PII at rest (docs/04 §7.2, ADR-022, UU PDP 27/2022, R-L01).
 *
 * Algoritma: AES-256-GCM, kunci 32 byte dari secret `pii_encryption_key` (base64, `PII_ENCRYPTION_KEY_FILE`).
 * Format `bytea` (persis kontrak):
 *
 *     0x01 (versi) ‖ IV 12 byte (acak per enkripsi) ‖ ciphertext ‖ tag 16 byte
 *
 * AAD = `"efn:<tabel>.<kolom>:v1"` (nama tabel TANPA skema, mis. `efn:price_hunter_report.user_enc:v1`) —
 * mengikat ciphertext ke kolomnya: nilai yang disalin ke kolom lain tidak bisa didekripsi dengan AAD kolom tujuan.
 * Karena itu menyalin PII antar-kolom (mis. user_enc → loyalty_ledger.member_ref_enc) = dekripsi lalu enkripsi ulang.
 *
 * Key ring (docs/04 §7.2): byte versi = versi kunci. v1 = `PII_ENCRYPTION_KEY_FILE`, vN = `PII_ENCRYPTION_KEY_V<N>_FILE`
 * (opsional). Enkripsi memakai `PII_ACTIVE_KEY_VERSION` (default 1); dekripsi memilih kunci dari byte versi.
 * Rotasi: tambah kunci v2 → set versi aktif 2 → `node dist/cli/pii-reencrypt.js` (lihat `reencrypt.ts`) →
 * set `PII_RETIRED_VERSIONS=1` + hapus secret v1 (BFF menolak start bila masih ada ciphertext v1, `pii-key-check.ts`).
 * Versi tanpa kunci → `PiiDecryptError` (bukan fallback).
 *
 * Pesan error tidak pernah memuat plaintext, kunci, atau ciphertext.
 */
export const PII_VERSION_V1 = 0x01;
export const PII_IV_BYTES = 12;
export const PII_TAG_BYTES = 16;
const KEY_BYTES = 32;
const IDENT_RE = /^[a-z_][a-z0-9_]*$/;

export class PiiDecryptError extends Error {}

/** AAD kontrak untuk kolom `<tabel>.<kolom>`. */
export function piiAad(table: string, column: string): string {
  if (!IDENT_RE.test(table) || !IDENT_RE.test(column)) throw new Error('nama tabel/kolom PII tidak valid');
  return `efn:${table}.${column}:v1`;
}

/** Kolom terenkripsi yang ditulis/dibaca BFF. */
export const PII_AAD = {
  PRICE_HUNTER_REPORT_USER: piiAad('price_hunter_report', 'user_enc'),
  LOYALTY_LEDGER_MEMBER_REF: piiAad('loyalty_ledger', 'member_ref_enc'),
  OTP_AUDIT_IP: piiAad('otp_audit', 'ip_enc'),
  // v3 (docs/04 §8.2, §8.4)
  MEMBER_REF: piiAad('member', 'member_ref_enc'),
  CONSENT_IP: piiAad('consent', 'ip_enc'),
  /** Fase 3 (docs/04 §10.3.6, L-13): kode ambil online — rahasia yang harus bisa ditampilkan ulang ke pemilik. */
  ONLINE_PICKUP_CODE: piiAad('online_order_part', 'pickup_code_enc'),
  /** SEC-24: nomor HP di challenge OTP login (Redis hash `otp:<id>` field `m`) — bukan kolom DB, tidak di-re-encrypt. */
  REDIS_OTP_MEMBER: piiAad('redis_otp', 'm'),
} as const;

export class PiiCipher {
  private readonly keys = new Map<number, Buffer>();

  /**
   * @param keys kunci tunggal (= versi 1) atau key ring `versi → kunci 32 byte`.
   * @param currentVersion versi kunci untuk enkripsi baru (harus ada di ring).
   */
  constructor(
    keys: Buffer | ReadonlyMap<number, Buffer>,
    private readonly currentVersion: number = PII_VERSION_V1,
  ) {
    const ring = Buffer.isBuffer(keys) ? new Map([[PII_VERSION_V1, keys]]) : keys;
    for (const [version, key] of ring) {
      if (!Number.isInteger(version) || version < 1 || version > 255) throw new Error('versi kunci PII harus 1..255');
      if (!Buffer.isBuffer(key) || key.length !== KEY_BYTES) throw new Error('kunci PII harus 32 byte');
      this.keys.set(version, Buffer.from(key));
    }
    if (!this.keys.has(currentVersion)) throw new Error('versi kunci PII aktif tidak punya kunci');
  }

  static fromConfig(config: AppConfig): PiiCipher {
    return new PiiCipher(config.piiKeyRing, config.piiActiveKeyVersion);
  }

  /** Versi kunci untuk enkripsi baru. */
  get activeVersion(): number {
    return this.currentVersion;
  }

  /** Versi kunci yang dimuat (urut naik). */
  get loadedVersions(): number[] {
    return [...this.keys.keys()].sort((a, b) => a - b);
  }

  /** Byte versi ciphertext (tanpa dekripsi), `null` bila blob kosong. */
  static versionOf(blob: Buffer | Uint8Array): number | null {
    return blob.length > 0 ? blob[0] : null;
  }

  encrypt(plaintext: string, aad: string): Buffer {
    const key = this.keys.get(this.currentVersion) as Buffer;
    const iv = randomBytes(PII_IV_BYTES);
    const cipher = createCipheriv('aes-256-gcm', key, iv, { authTagLength: PII_TAG_BYTES });
    cipher.setAAD(Buffer.from(aad, 'utf8'));
    const ct = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
    return Buffer.concat([Buffer.from([this.currentVersion]), iv, ct, cipher.getAuthTag()]);
  }

  decrypt(blob: Buffer | Uint8Array, aad: string): string {
    const b = Buffer.isBuffer(blob) ? blob : Buffer.from(blob);
    if (b.length < 1 + PII_IV_BYTES + PII_TAG_BYTES) throw new PiiDecryptError('ciphertext PII terlalu pendek');
    const key = this.keys.get(b[0]);
    if (!key) throw new PiiDecryptError('versi ciphertext PII tidak dikenal');
    const iv = b.subarray(1, 1 + PII_IV_BYTES);
    const tag = b.subarray(b.length - PII_TAG_BYTES);
    const ct = b.subarray(1 + PII_IV_BYTES, b.length - PII_TAG_BYTES);
    try {
      const decipher = createDecipheriv('aes-256-gcm', key, iv, { authTagLength: PII_TAG_BYTES });
      decipher.setAAD(Buffer.from(aad, 'utf8'));
      decipher.setAuthTag(tag);
      return Buffer.concat([decipher.update(ct), decipher.final()]).toString('utf8');
    } catch {
      throw new PiiDecryptError('autentikasi ciphertext PII gagal (kunci/AAD salah atau data dimodifikasi)');
    }
  }
}
