import { Queryable } from '../common/db/db';

/**
 * Hook privasi `wishlist` (docs/04 §28.8, L-161): DSAR export + erasure. Dipanggil `src/privacy` di dalam transaksinya.
 * Aman pada DB tanpa migrasi `20-platform-g50a.sql` (cek `to_regclass`). FK `ON DELETE CASCADE` ke `platform.member`
 * adalah jaring pengaman kedua (baris member dihapus di akhir erasure).
 */
const EXPORT_LIMIT = 500;

async function ready(q: Queryable): Promise<boolean> {
  const r = await q.query<{ ok: boolean }>(`SELECT to_regclass('platform.member_wishlist') IS NOT NULL AS ok`);
  return r.rows[0]?.ok === true;
}

export async function eraseWishlistMember(q: Queryable, memberId: string): Promise<void> {
  if (!(await ready(q))) return;
  await q.query(`DELETE FROM platform.member_wishlist WHERE member_id = $1`, [memberId]);
  await q.query(`DELETE FROM platform.restock_alert WHERE member_id = $1`, [memberId]);
  await q.query(`DELETE FROM platform.price_alert WHERE member_id = $1`, [memberId]);
}

export interface WishlistExport {
  wishlist: { channel_code: string; product_id: number; added_at: string | null }[];
  restock_alerts: { channel_code: string; outlet_code: string; product_id: number; channels: string[]; created_at: string | null; sent_at: string | null }[];
  price_alerts: { channel_code: string; product_id: number; target_price_idr: number; created_at: string | null; triggered_at: string | null }[];
}

export async function exportWishlistMember(q: Queryable, memberId: string): Promise<WishlistExport> {
  if (!(await ready(q))) return { wishlist: [], restock_alerts: [], price_alerts: [] };
  const w = await q.query<{ channel_code: string; product_id: number; added_at: Date | string }>(
    `SELECT channel_code, product_id, added_at FROM platform.member_wishlist WHERE member_id = $1 ORDER BY added_at DESC LIMIT $2`,
    [memberId, EXPORT_LIMIT],
  );
  const r = await q.query<{ channel_code: string; outlet_code: string; product_id: number; channels: string[]; created_at: Date | string; sent_at: Date | string | null }>(
    `SELECT channel_code, outlet_code, product_id, channels, created_at, sent_at FROM platform.restock_alert
      WHERE member_id = $1 ORDER BY created_at DESC LIMIT $2`,
    [memberId, EXPORT_LIMIT],
  );
  const p = await q.query<{ channel_code: string; product_id: number; target_price_idr: string | number; created_at: Date | string; triggered_at: Date | string | null }>(
    `SELECT channel_code, product_id, target_price_idr, created_at, triggered_at FROM platform.price_alert
      WHERE member_id = $1 ORDER BY created_at DESC LIMIT $2`,
    [memberId, EXPORT_LIMIT],
  );
  return {
    wishlist: w.rows.map((x) => ({ channel_code: x.channel_code, product_id: Number(x.product_id), added_at: iso(x.added_at) })),
    restock_alerts: r.rows.map((x) => ({
      channel_code: x.channel_code,
      outlet_code: x.outlet_code,
      product_id: Number(x.product_id),
      channels: x.channels,
      created_at: iso(x.created_at),
      sent_at: iso(x.sent_at),
    })),
    price_alerts: p.rows.map((x) => ({
      channel_code: x.channel_code,
      product_id: Number(x.product_id),
      target_price_idr: Number(x.target_price_idr),
      created_at: iso(x.created_at),
      triggered_at: iso(x.triggered_at),
    })),
  };
}

function iso(v: Date | string | null | undefined): string | null {
  if (v === null || v === undefined) return null;
  return (v instanceof Date ? v : new Date(v)).toISOString();
}
