import { Inject, Injectable } from '@nestjs/common';
import { createHash } from 'node:crypto';
import { DB, Db } from '../common/db/db';
import { isCompanyCode } from '../loyalty/loyalty.service';

export const MAX_SUBJECTS_PER_CHUNK = 5000;
const SEGMENT_RE = /^[a-z][a-z0-9_]{1,39}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const HEX64 = /^[0-9a-f]{64}$/;
/** SEC-G50-08: `company_code` WAJIB (perubahan kontrak §28.7 untuk segment-worker). */
const KEYS = new Set(['schema_version', 'company_code', 'run_id', 'segment_code', 'subject_keys', 'chunk_no']);

export type SegmentIngestResult =
  | { status: 200; body: { ok: true; run_id: string; segment_code: string; chunk_no: number; accepted: number; duplicate_chunk: boolean } }
  | { status: 400 | 409; body: { error: { code: string; message: string } } };

const bad = (): SegmentIngestResult => ({ status: 400, body: { error: { code: 'invalid_request', message: 'Permintaan tidak valid.' } } });

/**
 * `POST /internal/v1/segments/{run_id}/members` (docs/04 §28.7): anggota segmen dari `segment-worker` (mTLS, lihat
 * `segment-internal.server.ts`). Idempoten per `(run_id, segment_code, chunk_no)`: kirim ulang isi yang sama →
 * `duplicate_chunk: true`; isi berbeda untuk kunci yang sama → `409 chunk_conflict`. Subjek yang sudah dihapus
 * (`behavior.erased_subject`) tidak disimpan (mart ClickHouse bisa terlambat menghapusnya). Hanya `subject_key`
 * (pseudonim) — tanpa data member lain. Retensi 30 hari (BehaviorJobsService).
 */
@Injectable()
export class SegmentIngestService {
  constructor(@Inject(DB) private readonly db: Db) {}

  async ingest(pathRunId: string, body: unknown): Promise<SegmentIngestResult> {
    if (typeof body !== 'object' || body === null || Array.isArray(body)) return bad();
    const o = body as Record<string, unknown>;
    if (!Object.keys(o).every((k) => KEYS.has(k)) || o.schema_version !== 1) return bad();
    const runId = typeof o.run_id === 'string' ? o.run_id.toLowerCase() : '';
    if (!UUID_RE.test(runId) || runId !== pathRunId.toLowerCase()) return bad();
    if (typeof o.segment_code !== 'string' || !SEGMENT_RE.test(o.segment_code)) return bad();
    const chunkNo = o.chunk_no;
    if (typeof chunkNo !== 'number' || !Number.isSafeInteger(chunkNo) || chunkNo < 0 || chunkNo > 100_000) return bad();
    if (!Array.isArray(o.subject_keys) || o.subject_keys.length > MAX_SUBJECTS_PER_CHUNK) return bad();
    if (!o.subject_keys.every((k) => typeof k === 'string' && HEX64.test(k))) return bad();
    // SEC-G50-08: PT wajib dan konsisten per run_id (dispatcher hanya memakai anggota ber-PT sama dengan kampanye)
    if (!isCompanyCode(o.company_code)) return bad();
    const company = o.company_code;
    const keys = [...new Set(o.subject_keys as string[])].sort();
    const segment = o.segment_code;
    const sha = createHash('sha256').update(`${company}\n${segment}\n${chunkNo}\n${keys.join(',')}`, 'utf8').digest('hex');
    return this.db.tx(async (q): Promise<SegmentIngestResult> => {
      await q.query(`SELECT pg_advisory_xact_lock(hashtext('efn:segment_run:' || $1::text))`, [runId]);
      const runCompany = await q.query<{ company_code: string }>(
        `SELECT company_code FROM behavior.segment_chunk WHERE run_id = $1::uuid AND company_code <> $2 LIMIT 1`,
        [runId, company],
      );
      if (runCompany.rows.length > 0) {
        return { status: 409, body: { error: { code: 'run_company_mismatch', message: 'Run segmen sudah terdaftar untuk PT lain.' } } };
      }
      const ins = await q.query(
        `INSERT INTO behavior.segment_chunk (run_id, segment_code, chunk_no, body_sha256, member_count, company_code)
         VALUES ($1::uuid, $2, $3, $4, $5, $6)
         ON CONFLICT (run_id, segment_code, chunk_no) DO NOTHING RETURNING chunk_no`,
        [runId, segment, chunkNo, sha, keys.length, company],
      );
      if ((ins.rowCount ?? 0) === 0) {
        const prev = await q.query<{ body_sha256: string }>(
          `SELECT body_sha256 FROM behavior.segment_chunk WHERE run_id = $1::uuid AND segment_code = $2 AND chunk_no = $3`,
          [runId, segment, chunkNo],
        );
        if (String(prev.rows[0]?.body_sha256 ?? '').trim() !== sha) {
          return { status: 409, body: { error: { code: 'chunk_conflict', message: 'Chunk dengan nomor sama sudah diterima dengan isi berbeda.' } } };
        }
        return { status: 200, body: { ok: true, run_id: runId, segment_code: segment, chunk_no: chunkNo, accepted: 0, duplicate_chunk: true } };
      }
      let accepted = 0;
      if (keys.length > 0) {
        const r = await q.query(
          `INSERT INTO behavior.segment_member (run_id, segment_code, subject_key, company_code)
           SELECT $1::uuid, $2, k, $4 FROM unnest($3::text[]) AS k
            WHERE NOT EXISTS (SELECT 1 FROM behavior.erased_subject e WHERE e.subject_key = k)
           ON CONFLICT (run_id, segment_code, subject_key) DO NOTHING`,
          [runId, segment, keys, company],
        );
        accepted = r.rowCount ?? 0;
      }
      return { status: 200, body: { ok: true, run_id: runId, segment_code: segment, chunk_no: chunkNo, accepted, duplicate_chunk: false } };
    });
  }
}
