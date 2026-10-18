import { Queryable } from '../common/db/db';

/**
 * W-18 (Q-P18): daftar kerja CS untuk penghapusan akun yang tertahan pesanan online (read-only, TANPA PII):
 * - `deferred`: request hapus `scheduled` yang member-nya masih punya bagian pesanan terbuka → batas erasure
 *   (`requested_at` + `DSAR_OPEN_ORDER_MAX_DEFER_DAYS`), tingkat eskalasi, id bagian (opak);
 * - `unlinked_open_parts`: bagian pesanan yang MASIH terbuka padahal tautan member sudah dilepas (erasure dipaksa setelah
 *   batas, atau retensi) — CS menyelesaikannya lewat `online-refund` / `online-resolve` / serah terima di Odoo setelah
 *   verifikasi lewat kanal lain (pelanggan tidak lagi melihat kode ambil di aplikasi).
 * Tidak memuat member_id, member_hash, nomor HP, atau kode ambil.
 */
export interface ErasurePendingReport {
  deferred: {
    request_id: string;
    requested_at: string;
    scheduled_at: string;
    erasure_deadline: string;
    escalation: number;
    part_ids: string[];
  }[];
  unlinked_open_parts: {
    part_id: string;
    order_id: string;
    status: string;
    company_code: string;
    outlet_code: string | null;
    member_unlinked_at: string | null;
  }[];
}

const OPEN = `('paying', 'confirmed', 'ready', 'review')`;
const LIMIT = 500;

function iso(v: Date | string | null): string | null {
  if (v === null || v === undefined) return null;
  return (v instanceof Date ? v : new Date(v)).toISOString();
}

export async function listErasurePending(q: Queryable, maxDeferDays: number): Promise<ErasurePendingReport> {
  const deferred = await q.query<{
    request_id: string;
    requested_at: Date | string;
    scheduled_at: Date | string;
    deadline: Date | string;
    defer_escalation: number | string;
    part_ids: string[] | null;
  }>(
    `SELECT d.id::text AS request_id, d.requested_at, d.scheduled_at,
            GREATEST(d.requested_at + make_interval(days => $1::int), d.scheduled_at) AS deadline,
            d.defer_escalation,
            ARRAY(SELECT p.id::text FROM platform.online_order o JOIN platform.online_order_part p ON p.order_id = o.id
                   WHERE o.member_id = d.member_id AND p.status IN ${OPEN} ORDER BY p.id) AS part_ids
       FROM platform.dsar_request d
      WHERE d.type = 'delete' AND d.status = 'scheduled'
        AND EXISTS (SELECT 1 FROM platform.online_order o JOIN platform.online_order_part p ON p.order_id = o.id
                     WHERE o.member_id = d.member_id AND p.status IN ${OPEN})
      ORDER BY d.requested_at
      LIMIT $2`,
    [maxDeferDays, LIMIT],
  );
  const unlinked = await q.query<{
    part_id: string;
    order_id: string;
    status: string;
    company_code: string;
    outlet_code: string | null;
    member_unlinked_at: Date | string | null;
  }>(
    `SELECT p.id::text AS part_id, o.id::text AS order_id, p.status, p.company_code, p.outlet_code, o.member_unlinked_at
       FROM platform.online_order_part p JOIN platform.online_order o ON o.id = p.order_id
      WHERE o.member_id IS NULL AND p.status IN ${OPEN}
      ORDER BY o.member_unlinked_at NULLS FIRST, p.id
      LIMIT $1`,
    [LIMIT],
  );
  return {
    deferred: deferred.rows.map((r) => ({
      request_id: r.request_id,
      requested_at: iso(r.requested_at) ?? '',
      scheduled_at: iso(r.scheduled_at) ?? '',
      erasure_deadline: iso(r.deadline) ?? '',
      escalation: Number(r.defer_escalation),
      part_ids: r.part_ids ?? [],
    })),
    unlinked_open_parts: unlinked.rows.map((r) => ({
      part_id: r.part_id,
      order_id: r.order_id,
      status: r.status,
      company_code: r.company_code,
      outlet_code: r.outlet_code,
      member_unlinked_at: iso(r.member_unlinked_at),
    })),
  };
}
