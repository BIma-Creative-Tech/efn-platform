import { Inject, Injectable } from '@nestjs/common';
import { isIP } from 'node:net';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { ApiError, Errors } from '../common/errors/api-error';
import { PII_AAD, PiiCipher } from '../common/pii/crypto';

export const CONSENT_PURPOSES = ['location', 'marketing'] as const;
export type ConsentPurpose = (typeof CONSENT_PURPOSES)[number];
/** SHA-256 string kosong: penanda "teks tidak diketahui" untuk penarikan saat teks aktif belum dikonfigurasi. */
export const UNKNOWN_TEXT_SHA256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';

export function isConsentPurpose(v: unknown): v is ConsentPurpose {
  return typeof v === 'string' && (CONSENT_PURPOSES as readonly string[]).includes(v);
}

export interface ConsentState {
  purpose: ConsentPurpose;
  granted: boolean;
  version: string | null;
  updated_at: string | null;
}

export interface SetConsentInput {
  granted: boolean;
  version: string;
}

/**
 * Consent server-side (docs/04 §8.4, §8.7, §8.9; PDP_CONSENT.md). Tabel `platform.consent` APPEND-ONLY: setiap
 * perubahan = baris baru; status = baris terbaru per (member, purpose). Bukti: versi + `text_sha256` teks yang
 * disetujui, sumber, waktu, IP terenkripsi (`ip_enc`, AAD `efn:consent.ip_enc:v1`).
 *
 * - Grant (`granted=true`) hanya untuk versi teks AKTIF (`CONSENT_<PURPOSE>_VERSION`) → selain itu `409 stale_text`;
 *   teks aktif belum dikonfigurasi → `503 consent_unavailable`.
 * - Penarikan (`granted=false`) selalu diterima; `text_sha256` = hash teks aktif bila versinya sama, selain itu hash
 *   teks aktif saat penarikan (atau `UNKNOWN_TEXT_SHA256` bila belum dikonfigurasi).
 * - Purpose tanpa riwayat → `granted: false, version: null, updated_at: null` (default tidak setuju).
 */
@Injectable()
export class ConsentService {
  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly cipher: PiiCipher,
  ) {}

  async list(memberId: string, q: Queryable = this.db): Promise<ConsentState[]> {
    const res = await q.query<{ purpose: ConsentPurpose; granted: boolean; version: string; created_at: Date | string }>(
      `SELECT DISTINCT ON (purpose) purpose, granted, version, created_at
         FROM platform.consent
        WHERE member_id = $1
        ORDER BY purpose, created_at DESC, id DESC`,
      [memberId],
    );
    const latest = new Map(res.rows.map((r) => [r.purpose, r]));
    return CONSENT_PURPOSES.map((purpose) => {
      const r = latest.get(purpose);
      return r
        ? { purpose, granted: r.granted === true, version: r.version, updated_at: toIso(r.created_at) }
        : { purpose, granted: false, version: null, updated_at: null };
    });
  }

  async isGranted(memberId: string, purpose: ConsentPurpose, q: Queryable = this.db): Promise<boolean> {
    const res = await q.query<{ granted: boolean }>(
      `SELECT granted FROM platform.consent
        WHERE member_id = $1 AND purpose = $2
        ORDER BY created_at DESC, id DESC LIMIT 1`,
      [memberId, purpose],
    );
    return res.rows[0]?.granted === true;
  }

  async set(memberId: string, purpose: ConsentPurpose, input: SetConsentInput, ip: string | null): Promise<ConsentState> {
    const active = this.config.consentActiveText(purpose);
    let textSha256: string;
    if (input.granted) {
      if (!active) throw ApiError.of(503, 'consent_unavailable', 'Pengaturan persetujuan belum tersedia. Coba lagi nanti.');
      if (input.version !== active.version) {
        throw Errors.conflict('stale_text', 'Teks persetujuan sudah diperbarui. Muat ulang lalu baca versi terbaru.');
      }
      textSha256 = active.textSha256;
    } else {
      textSha256 = active?.textSha256 ?? UNKNOWN_TEXT_SHA256;
    }
    const ipEnc = ip && isIP(ip) ? this.cipher.encrypt(ip, PII_AAD.CONSENT_IP) : null;
    const res = await this.db.query<{ created_at: Date | string }>(
      `INSERT INTO platform.consent (member_id, purpose, granted, version, text_sha256, source, ip_enc)
       VALUES ($1, $2, $3, $4, $5, 'superapp', $6)
       RETURNING created_at`,
      [memberId, purpose, input.granted, input.version, textSha256, ipEnc],
    );
    return { purpose, granted: input.granted, version: input.version, updated_at: toIso(res.rows[0]?.created_at ?? new Date()) };
  }
}

export function toIso(v: Date | string | null | undefined): string | null {
  if (v === null || v === undefined) return null;
  return (v instanceof Date ? v : new Date(v)).toISOString();
}
