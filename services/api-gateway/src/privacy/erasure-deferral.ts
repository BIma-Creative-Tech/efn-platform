import { toIso } from './consent.service';
import type { DsarRequestItem } from './dsar.service';

/**
 * W-18 (Q-P18): bagian bersama untuk menampilkan penundaan hapus akun karena pesanan online terbuka
 * (`DELETE /v1/me`, `GET /v1/me/requests`, `GET /v1/dsar/requests/:id`). Hanya JUMLAH bagian terbuka — tanpa id/detail
 * order (respons status tanpa sesi tidak boleh membuka isi pesanan).
 */

/** Kolom `open_n` untuk query `platform.dsar_request d`: jumlah bagian terbuka, hanya untuk hapus yang masih `scheduled`. */
export const OPEN_PARTS_COLUMN = `CASE WHEN d.type = 'delete' AND d.status = 'scheduled' THEN (
         SELECT count(*) FROM platform.online_order_part p JOIN platform.online_order o ON o.id = p.order_id
          WHERE o.member_id = d.member_id AND p.status IN ('paying', 'confirmed', 'ready', 'review'))
       ELSE 0 END AS open_n`;

/** Batas erasure: max(`requested_at` + N hari, `scheduled_at`); `null` bila tidak ada pesanan terbuka. */
export function erasureDeadlineIso(
  requestedAt: string | null,
  scheduledAt: string | null,
  openParts: number,
  maxDeferDays: number,
): string | null {
  if (openParts <= 0 || !requestedAt) return null;
  const byDefer = Date.parse(requestedAt) + maxDeferDays * 86_400_000;
  if (Number.isNaN(byDefer)) return null;
  const sched = scheduledAt ? Date.parse(scheduledAt) : Number.NaN;
  return new Date(Number.isNaN(sched) ? byDefer : Math.max(byDefer, sched)).toISOString();
}

/** Baris `dsar_request` (+ `open_n` dari `OPEN_PARTS_COLUMN`) → item respons (sesi & receipt). Tanpa PII. */
export function dsarItem(
  r: {
    id: string;
    type: string;
    status: string;
    requested_at: Date | string;
    scheduled_at: Date | string | null;
    completed_at: Date | string | null;
    open_n?: number | string | null;
  },
  maxDeferDays: number,
): DsarRequestItem {
  const requested = toIso(r.requested_at);
  const scheduled = toIso(r.scheduled_at);
  const open = Number(r.open_n ?? 0) || 0;
  return {
    id: r.id,
    type: r.type,
    status: r.status,
    requested_at: requested,
    scheduled_at: scheduled,
    completed_at: toIso(r.completed_at),
    open_online_orders: open,
    erasure_deadline: erasureDeadlineIso(requested, scheduled, open, maxDeferDays),
  };
}
