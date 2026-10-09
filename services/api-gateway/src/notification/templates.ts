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
