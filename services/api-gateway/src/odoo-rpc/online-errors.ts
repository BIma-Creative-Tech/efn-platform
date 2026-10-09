import { ApiError } from '../common/errors/api-error';
import { OdooRpcError } from './odoo-online.types';
import { OnlineMetrics } from './online-metrics';

/**
 * Error pelanggan kanal online (docs/04 §10.3.7, K-ON5). Pesan generik Bahasa Indonesia; tidak pernah memuat detail
 * Odoo (model, traceback, nomor SO lain).
 */
export const OnlineErrors = {
  unavailable: (): ApiError => ApiError.of(503, 'online_unavailable', 'Belanja online sedang tidak tersedia. Silakan coba lagi nanti.'),
  priceChanged: (): ApiError => ApiError.of(409, 'price_changed', 'Harga atau total berubah. Muat ulang ringkasan pesanan.'),
  outOfStock: (): ApiError => ApiError.of(409, 'out_of_stock', 'Sebagian barang sedang habis. Perbarui keranjang Anda.'),
  itemUnavailable: (): ApiError => ApiError.of(409, 'item_unavailable', 'Sebagian barang tidak lagi tersedia. Perbarui keranjang Anda.'),
  orderNotCancellable: (): ApiError =>
    ApiError.of(409, 'order_not_cancellable', 'Pesanan sudah dibayar dan tidak dapat dibatalkan di sini. Hubungi layanan pelanggan.'),
  invalidState: (): ApiError => ApiError.of(409, 'invalid_state', 'Status pesanan tidak memungkinkan tindakan ini.'),
  quoteExpired: (): ApiError => ApiError.of(409, 'quote_expired', 'Ringkasan harga sudah kedaluwarsa. Muat ulang ringkasan pesanan.'),
  cartChanged: (): ApiError => ApiError.of(409, 'cart_changed', 'Keranjang berubah sejak ringkasan dibuat. Muat ulang ringkasan pesanan.'),
  cartHasIssues: (): ApiError => ApiError.of(409, 'cart_has_issues', 'Ada barang yang tidak tersedia. Perbarui keranjang Anda.'),
  cartVersionConflict: (): ApiError => ApiError.of(409, 'cart_version_conflict', 'Keranjang diubah di perangkat lain. Muat ulang keranjang.'),
  cartEmpty: (): ApiError => ApiError.of(409, 'cart_has_issues', 'Keranjang kosong.'),
  idempotencyConflict: (): ApiError =>
    ApiError.of(409, 'idempotency_conflict', 'Permintaan dengan kunci yang sama sudah dipakai untuk pesanan lain.'),
  idempotencyKeyRequired: (): ApiError => ApiError.of(400, 'idempotency_key_required', 'Header Idempotency-Key wajib diisi.'),
  tooManyOpenOrders: (): ApiError =>
    ApiError.of(429, 'too_many_open_orders', 'Masih ada pesanan yang menunggu pembayaran. Selesaikan atau batalkan dulu.'),
  fulfilmentUnavailable: (): ApiError => ApiError.of(422, 'fulfilment_unavailable', 'Metode pengambilan belum tersedia.'),
  pickupCodeUnavailable: (): ApiError => ApiError.of(409, 'pickup_code_unavailable', 'Kode ambil belum tersedia untuk pesanan ini.'),
  accountDeleting: (): ApiError => ApiError.of(409, 'account_deleting', 'Akun sedang dijadwalkan untuk dihapus.'),
};

/**
 * Peta `OdooRpcError` → error pelanggan (§10.3.7). Penolakan yang menandakan bug/konfigurasi (`idempotency_conflict`,
 * `amount_mismatch`, `invalid_input`, respons tak valid, 401/403/404) → 503 + anomali `online_config_error`.
 * `invalid_state` dipetakan oleh pemanggil (cancel → `order_not_cancellable`).
 */
export function mapOdooError(err: unknown, metrics: OnlineMetrics, ctx: { method: string; channel?: string; partId?: string }): ApiError {
  if (!(err instanceof OdooRpcError)) return OnlineErrors.unavailable();
  if (err.kind === 'business') {
    switch (err.code) {
      case 'price_changed':
        return OnlineErrors.priceChanged();
      case 'out_of_stock':
        return OnlineErrors.outOfStock();
      case 'not_found':
      case 'channel_inactive':
        return OnlineErrors.itemUnavailable();
      case 'invalid_state':
        return OnlineErrors.invalidState();
      default:
        metrics.anomaly('online_config_error', 'critical', { method: ctx.method, channel: ctx.channel, part_id: ctx.partId, code: err.code });
        return OnlineErrors.unavailable();
    }
  }
  if (err.kind === 'invalid_response' || err.kind === 'not_found' || err.kind === 'auth') {
    metrics.anomaly('online_config_error', 'critical', { method: ctx.method, channel: ctx.channel, part_id: ctx.partId, code: err.kind });
  }
  return OnlineErrors.unavailable();
}
