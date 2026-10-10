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
 *   `paying|confirmed|ready|review` → `409 open_online_orders` (ambil/batalkan lewat CS dulu).
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
  async lockAndAssertNoOpenOrders(q: Queryable, memberId: string): Promise<void> {
    const r = new PgOnlineRepo(q);
    await r.lockMember(memberId);
    if ((await r.memberUnpaidOrders(memberId)).length > 0 || (await r.memberOpenPaidParts(memberId)) > 0) throw openOnlineOrders();
  }

  /** Dipanggil `DELETE /v1/me` sebelum permintaan hapus dibuat. */
  async prepareDelete(memberId: string): Promise<void> {
    for (const o of await this.repo.memberUnpaidOrders(memberId)) {
      try {
        await this.orders.cancel(memberId, o.id);
      } catch (err) {
        // Sudah dibayar di sela waktu → ditangani pemeriksaan bagian terbuka di bawah
        if (!(err instanceof ApiError)) throw err;
      }
    }
    if ((await this.repo.memberOpenPaidParts(memberId)) > 0) throw openOnlineOrders();
    this.logger.log('Pra-hapus akun: tidak ada pesanan online terbuka');
  }
}

export function openOnlineOrders(): ApiError {
  return ApiError.of(
    409,
    'open_online_orders',
    'Masih ada pesanan online yang sedang diproses. Ambil pesanan atau minta pembatalan lewat layanan pelanggan, lalu ulangi penghapusan akun.',
  );
}
