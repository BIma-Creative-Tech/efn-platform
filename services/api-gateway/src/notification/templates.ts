/**
 * Pemetaan `notify.push.template` (kontrak §4) → template WhatsApp yang sudah disetujui Meta.
 * `params` = urutan kunci dari `payload.params` yang mengisi {{1}}, {{2}}, ... di body template.
 * Nama template WA harus didaftarkan di WhatsApp Manager (kategori UTILITY) sebelum produksi.
 */
export interface NotifyTemplate {
  waTemplate: string;
  params: readonly string[];
}

export const NOTIFY_TEMPLATES: Readonly<Record<string, NotifyTemplate>> = {
  price_hunter_valid: { waTemplate: 'efn_price_hunter_valid', params: ['product_name', 'points'] },
  price_hunter_rejected: { waTemplate: 'efn_price_hunter_rejected', params: ['product_name', 'reason'] },
  // Fase 3 (docs/04 §10.3.6): pesan WA TANPA kode ambil/nominal — hanya "buka aplikasi" (template UTILITY tanpa parameter)
  online_order_ready: { waTemplate: 'efn_online_order_ready', params: [] },
  online_order_cancelled: { waTemplate: 'efn_online_order_cancelled', params: [] },
  online_order_refunded: { waTemplate: 'efn_online_order_refunded', params: [] },
  // G26 §17.5 (L-100): bukti transfer ditolak — TANPA alasan/nominal/rekening; hanya "unggah ulang, buka aplikasi".
  online_transfer_rejected: { waTemplate: 'efn_online_transfer_rejected', params: [] },
  // G23 §15.5: reservasi meja — TANPA nama tamu/catatan/nomor meja; hanya "buka aplikasi" (UTILITY tanpa parameter).
  booking_confirmed: { waTemplate: 'efn_booking_confirmed', params: [] },
  booking_cancelled: { waTemplate: 'efn_booking_cancelled', params: [] },
  // G22 §14.D.4 + L-76: pengingat hangus T−30 — HANYA tanggal hangus (tanpa jumlah poin, nama, nominal, tautan).
  // Kategori diharapkan UTILITY (keputusan Meta, belum terverifikasi); teks final = Legal.
  loyalty_inactivity_notice: { waTemplate: 'efn_loyalty_inactivity_notice', params: ['expire_date'] },
  // G33 §20.11: poin referral MGM masuk — TANPA jumlah poin/level/PT/identitas downline; hanya "buka aplikasi".
  // Dikirim hanya dengan consent marketing. Teks final = Legal (Permendag 70/2019).
  mgm_reward_settled: { waTemplate: 'efn_mgm_reward_settled', params: [] },
};

const MAX_PARAM_LENGTH = 100;

/** Nilai parameter template: string/number saja, tanpa baris baru (ditolak Graph API), dipotong. */
export function renderTemplateParams(tpl: NotifyTemplate, params: Record<string, unknown>): string[] {
  return tpl.params.map((key) => {
    const v = params[key];
    if (typeof v !== 'string' && typeof v !== 'number') return '-';
    const s = String(v).replace(/[\r\n\t]+/g, ' ').replace(/ {4,}/g, ' ').trim();
    if (s.length === 0) return '-';
    return s.length > MAX_PARAM_LENGTH ? `${s.slice(0, MAX_PARAM_LENGTH - 1)}…` : s;
  });
}
