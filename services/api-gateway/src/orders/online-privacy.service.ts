import { Inject, Injectable, Logger } from '@nestjs/common';
import { Queryable } from '../common/db/db';
import { ApiError } from '../common/errors/api-error';
import { ONLINE_REPO, OnlineRepo, PgOnlineRepo } from './online.repository';
import { OrdersService } from './orders.service';

export const EXPORT_ORDER_LIMIT = 1000;

/**
 * Hak subjek data untuk kanal online (docs/04 §10.8.3, L-4):
 * - export: order + bagian milik member (tanpa kode ambil, tanpa QR, tanpa id Odoo/payment);
 * - sebelum `DELETE /v1/me`: order `created|awaiting_payment` dibatalkan otomatis; bila masih ada bagian
 *   `paying|confirmed|ready|review` → `409 open_online_orders` (ambil/batalkan lewat CS dulu) KECUALI pelanggan
 *   mengonfirmasi (`?confirm_open_orders=true`, W-18/Q-P18): permintaan hapus tetap dicatat dan erasure ditunda
 *   paling lama `DSAR_OPEN_ORDER_MAX_DEFER_DAYS` (14) hari sejak permintaan, lalu tetap dijalankan (`ErasureService`).
 * Erasure platform sendiri (member_id → NULL) dijalankan `ErasureService` (privacy).
 */
@Injectable()
export class OnlinePrivacyService {
  private readonly logger = new Logger('OnlinePrivacy');

  constructor(
    @Inject(ONLINE_REPO) private readonly repo: OnlineRepo,
    private readonly orders: OrdersService,
  ) {}

  async exportOrders(memberId: string): Promise<{ items: unknown[]; truncated: boolean }> {
    const rows = await this.repo.exportOrders(memberId, EXPORT_ORDER_LIMIT + 1);
    return {
      truncated: rows.length > EXPORT_ORDER_LIMIT,
      items: rows.slice(0, EXPORT_ORDER_LIMIT).map(({ order, parts }) => ({
        order_id: order.id,
        status: order.status,
        created_at: order.created_at.toISOString(),
        paid_at: order.paid_at ? order.paid_at.toISOString() : null,
        closed_at: order.closed_at ? order.closed_at.toISOString() : null,
        amount_payable: order.amount_payable,
        currency: order.currency,
        parts: parts.map((p) => ({
          channel_code: p.channel_code,
          company_code: p.company_code,
          outlet_code: p.outlet_code,
          status: p.status,
          sale_order_name: p.sale_order_name,
          lines: p.lines,
          amount_payable: p.amount_payable,
          refunded_amount: p.refunded_amount,
          ready_at: p.ready_at ? p.ready_at.toISOString() : null,
          handed_over_at: p.handed_over_at ? p.handed_over_at.toISOString() : null,
        })),
      })),
    };
  }

  /**
   * SEC-100 (L-16): DI DALAM transaksi pembuat `dsar_request` — ambil kunci member yang SAMA dengan checkout
   * (`lockMember`), lalu cek ulang: masih ada order belum dibayar atau bagian terbuka → 409. Checkout yang menunggu
   * kunci ini kemudian melihat `hasActiveDelete` dan ditolak (`409 account_deleting`) — atomik.
   */
  async lockAndAssertNoOpenOrders(q: Queryable, memberId: string, opts: OpenOrderOpts = {}): Promise<number> {
    const r = new PgOnlineRepo(q);
    await r.lockMember(memberId);
    const unpaid = (await r.memberUnpaidOrders(memberId)).length;
    const open = await r.memberOpenPaidParts(memberId);
    if (!opts.confirm && unpaid + open > 0) throw openOnlineOrders(open, opts.maxDeferDays);
    return open + unpaid;
  }

  /** Jumlah bagian dibayar yang belum selesai (read-only; pra-cek `DELETE /v1/me` sebelum token step-up dipakai). */
  async openPaidParts(memberId: string): Promise<number> {
    return this.repo.memberOpenPaidParts(memberId);
  }

  /** Dipanggil `DELETE /v1/me` sebelum permintaan hapus dibuat. */
  async prepareDelete(memberId: string, opts: OpenOrderOpts = {}): Promise<number> {
    for (const o of await this.repo.memberUnpaidOrders(memberId)) {
      try {
        await this.orders.cancel(memberId, o.id);
      } catch (err) {
        // Sudah dibayar di sela waktu → ditangani pemeriksaan bagian terbuka di bawah
        if (!(err instanceof ApiError)) throw err;
      }
    }
    const open = await this.repo.memberOpenPaidParts(memberId);
    if (open > 0 && !opts.confirm) throw openOnlineOrders(open, opts.maxDeferDays);
    // Tanpa id/PII; jumlah saja (Q-P18: CS diberi tahu lewat eskalasi ErasureService)
    this.logger.log(open > 0 ? `Pra-hapus akun: ${open} bagian pesanan online terbuka, dikonfirmasi pelanggan (erasure ditunda)` : 'Pra-hapus akun: tidak ada pesanan online terbuka');
    return open;
  }
}

export interface OpenOrderOpts {
  /** Pelanggan sudah mengonfirmasi hapus akun walau ada pesanan terbuka (W-18, Q-P18). */
  confirm?: boolean;
  /** Batas penundaan (hari) untuk teks respons; default 14 (Q-P18). */
  maxDeferDays?: number;
}

export const OPEN_ORDERS_CONFIRM_PARAM = 'confirm_open_orders';

/**
 * L-4 + W-18 (Q-P18): 409 dengan langkah yang jelas + batas penundaan. Teks final menunggu Legal (PDP_DATA_INVENTORY §4
 * butir 6); field tambahan `open_parts`, `max_defer_days`, `confirm_param` untuk frontend (tanpa PII).
 */
export function openOnlineOrders(openParts = 0, maxDeferDays = 14): ApiError {
  return new ApiError(409, {
    error: {
      code: 'open_online_orders',
      message:
        'Masih ada pesanan online yang sedang diproses. Anda dapat mengambil pesanan atau meminta pembatalan lewat layanan pelanggan, ' +
        'lalu ulangi penghapusan akun. Bila Anda tetap ingin menghapus akun sekarang, akun akan dihapus paling lambat ' +
        `${maxDeferDays} hari sejak permintaan; pesanan yang belum selesai akan diselesaikan layanan pelanggan tanpa tautan ke akun Anda ` +
        '(kode ambil tidak lagi tampil di aplikasi).',
      open_parts: openParts,
      max_defer_days: maxDeferDays,
      confirm_param: OPEN_ORDERS_CONFIRM_PARAM,
    },
  });
}
