import { AppConfig } from '../config/app-config';
import { Queryable } from '../common/db/db';
import { memberSubjectKey } from './subject-key';

/**
 * Hook privasi konteks `behavior` (ADR-047 §3.3, docs/04 §28.6, L-152, L-154). Dipanggil dari `src/privacy`
 * (ErasureService, DsarService, ConsentService) di dalam transaksi pemanggil. Semua fungsi aman dipanggil pada DB yang
 * belum menjalankan migrasi `20-platform-g50a.sql` (cek `to_regclass` dulu) agar erasure akun tidak gagal.
 */

const EXPORT_LIMIT = 1000;

export async function behaviorSchemaReady(q: Queryable): Promise<boolean> {
  const r = await q.query<{ ok: boolean }>(`SELECT to_regclass('behavior.device_link') IS NOT NULL AS ok`);
  return r.rows[0]?.ok === true;
}

/** Pepper subject (null bila belum dikonfigurasi — fitur tidak pernah aktif). */
export function optionalSubjectPepper(config: AppConfig): string | null {
  try {
    return config.behaviorSubjectPepper;
  } catch {
    return null;
  }
}

/** Semua pseudonim subjek: subject_key member (tersimpan + dihitung) dan device_key yang pernah ditautkan. */
export async function subjectKeysOf(q: Queryable, memberId: string, pepper: string | null): Promise<string[]> {
  const res = await q.query<{ k: string }>(
    `SELECT subject_key AS k FROM behavior.subject_member WHERE member_id = $1
     UNION SELECT device_key AS k FROM behavior.device_link WHERE member_id = $1`,
    [memberId],
  );
  const keys = new Set(res.rows.map((r) => String(r.k).trim()));
  if (pepper) keys.add(memberSubjectKey(pepper, memberId));
  return [...keys];
}

/**
 * Erasure (L-154): catat SEMUA subject_key subjek (HMAC member + setiap device_key tertaut) di
 * `behavior.subject_erasure` dengan `request_id` DSAR — antrean job ETL `behavior_erasure` (ALTER DELETE
 * `fact_visitor_event`) dan `BehaviorDsarHook` (menahan `privacy.erasure.completed` sampai kunci hilang dari ClickHouse);
 * lalu hapus event staging, dedup, anggota segmen, tautan device, pemetaan subject; `erased_subject` dipakai BFF untuk
 * menolak subjek terhapus di ingest segmen. Dipanggil di DALAM transaksi ErasureService yang sama dengan transisi
 * `dsar_request` → `processing` (atomik: ETL tidak pernah melihat `processing` tanpa baris `subject_erasure`).
 * Bukti consent device (`device_consent`, tanpa PII) dipertahankan — setelah tautan dihapus tidak dapat dikaitkan.
 */
export async function eraseBehaviorMember(
  q: Queryable,
  memberId: string,
  pepper: string | null,
  requestId: string,
  reason = 'dsar_erasure',
): Promise<number> {
  if (!(await behaviorSchemaReady(q))) return 0;
  const keys = await subjectKeysOf(q, memberId, pepper);
  if (keys.length > 0) {
    await q.query(
      `INSERT INTO behavior.subject_erasure (subject_key, request_id, reason)
       SELECT k, $2::uuid, $3 FROM unnest($1::text[]) AS k`,
      [keys, requestId, reason],
    );
    await q.query(`DELETE FROM behavior.event_raw WHERE subject_key = ANY($1::text[])`, [keys]);
    await q.query(`DELETE FROM behavior.event_dedup WHERE subject_key = ANY($1::text[])`, [keys]);
    await q.query(`DELETE FROM behavior.segment_member WHERE subject_key = ANY($1::text[])`, [keys]);
    await q.query(
      `INSERT INTO behavior.erased_subject (subject_key, erased_at) SELECT k, now() FROM unnest($1::text[]) AS k
       ON CONFLICT (subject_key) DO UPDATE SET erased_at = now()`,
      [keys],
    );
  }
  await q.query(`DELETE FROM behavior.device_link WHERE member_id = $1`, [memberId]);
  await q.query(`DELETE FROM behavior.subject_member WHERE member_id = $1`, [memberId]);
  return keys.length;
}

/** Consent `tracking` akun dicabut (ADR-047 §3.1 no. 4): tautan device & pemetaan subject dihapus. */
export async function withdrawBehaviorTracking(q: Queryable, memberId: string): Promise<void> {
  if (!(await behaviorSchemaReady(q))) return;
  await q.query(`DELETE FROM behavior.device_link WHERE member_id = $1`, [memberId]);
  await q.query(`DELETE FROM behavior.subject_member WHERE member_id = $1`, [memberId]);
}

export interface BehaviorExport {
  linked_devices: { linked_at: string | null; last_seen_at: string | null }[];
  events: { type: string; client_ts: string | null; channel_code: string | null; product_id: number | null; query: string | null }[];
  truncated: boolean;
}

/** DSAR export: tautan perangkat (tanpa device_key) + event staging ≤ 7 hari milik subjek. */
export async function exportBehaviorMember(q: Queryable, memberId: string, pepper: string | null): Promise<BehaviorExport> {
  const empty: BehaviorExport = { linked_devices: [], events: [], truncated: false };
  if (!(await behaviorSchemaReady(q))) return empty;
  const links = await q.query<{ linked_at: Date | string; last_seen_at: Date | string }>(
    `SELECT linked_at, last_seen_at FROM behavior.device_link WHERE member_id = $1 ORDER BY linked_at DESC LIMIT 50`,
    [memberId],
  );
  const keys = await subjectKeysOf(q, memberId, pepper);
  const events =
    keys.length === 0
      ? { rows: [] as { type: string; client_ts: Date | string; channel_code: string | null; product_id: number | null; query: string | null }[] }
      : await q.query<{ type: string; client_ts: Date | string; channel_code: string | null; product_id: number | null; query: string | null }>(
          `SELECT type, client_ts, channel_code, product_id, query FROM behavior.event_raw
            WHERE subject_key = ANY($1::text[]) ORDER BY received_at DESC LIMIT $2`,
          [keys, EXPORT_LIMIT + 1],
        );
  return {
    linked_devices: links.rows.map((r) => ({ linked_at: iso(r.linked_at), last_seen_at: iso(r.last_seen_at) })),
    events: events.rows.slice(0, EXPORT_LIMIT).map((r) => ({
      type: r.type,
      client_ts: iso(r.client_ts),
      channel_code: r.channel_code ?? null,
      product_id: r.product_id === null || r.product_id === undefined ? null : Number(r.product_id),
      query: r.query ?? null,
    })),
    truncated: events.rows.length > EXPORT_LIMIT,
  };
}

function iso(v: Date | string | null | undefined): string | null {
  if (v === null || v === undefined) return null;
  return (v instanceof Date ? v : new Date(v)).toISOString();
}

/** Status consent terbaru member untuk satu purpose (pola `ConsentService.isGranted`, tanpa dependensi modul privacy). */
export async function memberConsentGranted(q: Queryable, memberId: string, purpose: 'tracking' | 'marketing'): Promise<boolean> {
  const res = await q.query<{ granted: boolean }>(
    `SELECT granted FROM platform.consent WHERE member_id = $1 AND purpose = $2 ORDER BY created_at DESC, id DESC LIMIT 1`,
    [memberId, purpose],
  );
  return res.rows[0]?.granted === true;
}
