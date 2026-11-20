import type { AppConfig } from '../../config/app-config';
import type { Queryable } from '../../common/db/db';
import { UNKNOWN_TEXT_SHA256 } from '../../privacy/consent.service';

/**
 * Consent `marketing` untuk kanal G50 (docs/04 §8.4, §28.7; Q-SM25 default: wajib untuk email & WA unofficial).
 * Tabel `platform.consent` append-only; status = baris terbaru per (member, purpose). Consent per PURPOSE, bukan per
 * kanal: "STOP" di WA atau berhenti berlangganan email mencabut `marketing` untuk semua kanal promo (push, email, WA).
 * Ini sengaja (perilaku paling aman PDP); consent per kanal butuh revisi kontrak §8.4 (usulan di laporan G50-B).
 */
export type MarketingWithdrawSource = 'email_unsubscribe' | 'wa_stop';

export async function isMarketingGranted(q: Queryable, memberId: string): Promise<boolean> {
  const res = await q.query<{ granted: boolean }>(
    `SELECT granted FROM platform.consent
      WHERE member_id = $1 AND purpose = 'marketing'
      ORDER BY created_at DESC, id DESC LIMIT 1`,
    [memberId],
  );
  return res.rows[0]?.granted === true;
}

/**
 * Cabut consent `marketing` (baris baru `granted=false`). Tidak menulis bila status terakhir sudah tidak setuju.
 * Versi/hash teks = teks aktif (bukti), atau penanda "tidak diketahui" bila belum dikonfigurasi.
 * @returns true bila baris penarikan ditulis.
 */
export async function withdrawMarketing(
  q: Queryable,
  config: AppConfig,
  memberId: string,
  source: MarketingWithdrawSource,
  ipEnc: Buffer | null = null,
): Promise<boolean> {
  if (!(await isMarketingGranted(q, memberId))) return false;
  let active: { version: string; textSha256: string } | null = null;
  try {
    active = config.consentActiveText('marketing');
  } catch {
    active = null; // konfigurasi teks rusak tidak boleh menghalangi pencabutan
  }
  await q.query(
    `INSERT INTO platform.consent (member_id, purpose, granted, version, text_sha256, source, ip_enc)
     VALUES ($1, 'marketing', false, $2, $3, $4, $5)`,
    [memberId, active?.version ?? 'unknown', active?.textSha256 ?? UNKNOWN_TEXT_SHA256, source, ipEnc],
  );
  return true;
}
