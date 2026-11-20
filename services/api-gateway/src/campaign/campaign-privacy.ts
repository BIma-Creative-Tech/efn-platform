import { Queryable } from '../common/db/db';

/**
 * Hook privasi `campaign` (docs/04 §28.7, ADR-047 §3.3, L-155). Dipanggil `src/privacy` di dalam transaksinya; aman pada
 * DB tanpa migrasi G50 (cek `to_regclass`). Kode voucher yang sudah ditugaskan TIDAK dikembalikan ke pool (tetap
 * `assigned_at`, hanya tautan member yang dilepas) agar kode yang mungkin sudah dibagikan tidak ditugaskan ulang.
 * Mode `member_bound` (kartu per partner di Odoo) dihapus/dianonimkan oleh jalur erasure Odoo, bukan di sini.
 */
async function ready(q: Queryable): Promise<boolean> {
  const r = await q.query<{ ok: boolean }>(`SELECT to_regclass('platform.campaign_delivery') IS NOT NULL AS ok`);
  return r.rows[0]?.ok === true;
}

export async function eraseCampaignMember(q: Queryable, memberId: string): Promise<void> {
  if (!(await ready(q))) return;
  await q.query(`DELETE FROM platform.member_inbox WHERE member_id = $1`, [memberId]);
  await q.query(`DELETE FROM platform.campaign_delivery WHERE member_id = $1`, [memberId]);
  await q.query(`UPDATE platform.member_voucher SET member_id = NULL WHERE member_id = $1`, [memberId]);
}

export interface CampaignExport {
  inbox: { title: string; body: string; created_at: string | null; valid_to: string | null }[];
  vouchers: { assigned_at: string | null; valid_to: string | null }[];
}

export async function exportCampaignMember(q: Queryable, memberId: string): Promise<CampaignExport> {
  if (!(await ready(q))) return { inbox: [], vouchers: [] };
  const i = await q.query<{ title: string; body: string; created_at: Date | string; valid_to: Date | string }>(
    `SELECT title, body, created_at, valid_to FROM platform.member_inbox WHERE member_id = $1 ORDER BY created_at DESC LIMIT 500`,
    [memberId],
  );
  const v = await q.query<{ assigned_at: Date | string | null; valid_to: Date | string }>(
    `SELECT assigned_at, valid_to FROM platform.member_voucher WHERE member_id = $1 ORDER BY assigned_at DESC LIMIT 500`,
    [memberId],
  );
  return {
    inbox: i.rows.map((r) => ({ title: r.title, body: r.body, created_at: iso(r.created_at), valid_to: iso(r.valid_to) })),
    vouchers: v.rows.map((r) => ({ assigned_at: iso(r.assigned_at), valid_to: iso(r.valid_to) })),
  };
}

function iso(v: Date | string | null | undefined): string | null {
  if (v === null || v === undefined) return null;
  return (v instanceof Date ? v : new Date(v)).toISOString();
}
