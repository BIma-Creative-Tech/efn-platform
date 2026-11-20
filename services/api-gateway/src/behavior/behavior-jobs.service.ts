import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { deterministicUuid } from './subject-key';

export const BEHAVIOR_JOB_LOCK_KEY = 0x45464e42; // 'EFNB'
const ABANDON_BATCH = 1000;

export interface BehaviorJobResult {
  skipped: boolean;
  partitionsCreated: number;
  partitionsDropped: number;
  purged: number;
  cartAbandon: number;
}

/**
 * Job periodik konteks behavior (`BEHAVIOR_JOB_INTERVAL_MS`, default 1 jam; satu replika per siklus lewat advisory lock):
 * 1. partisi harian `behavior.event_raw` (hari ini−1 .. +2) dan drop partisi > retensi (fungsi SECURITY DEFINER milik
 *    `efn_platform_owner`, efn_bff tidak punya hak DDL);
 * 2. retensi: `event_dedup` (= retensi staging), tautan device menganggur (180 h), bukti consent device (placeholder
 *    400 h), anggota segmen (30 h), daftar `erased_subject` (100 h > TTL ClickHouse 90 h);
 * 3. `cart_abandon` turunan server (ADR-047 §3.2): subjek yang `cart_add` terakhirnya 24–48 jam lalu tanpa
 *    `checkout_start` sesudahnya → satu event `cart_abandon` (event_id deterministik → aman diulang).
 * Tidak pernah melempar. Aktif bila tracking ATAU kampanye aktif (segmen ikut dipurge).
 */
@Injectable()
export class BehaviorJobsService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(BehaviorJobsService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
  ) {}

  onModuleInit(): void {
    const ms = this.config.behaviorJobIntervalMs;
    if (ms <= 0 || !(this.config.behaviorTrackingEnabled || this.config.promoCampaignEnabled)) return;
    this.timer = setInterval(() => void this.runOnce(), ms);
    this.timer.unref();
    setTimeout(() => void this.runOnce(), 15_000).unref();
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async runOnce(): Promise<BehaviorJobResult> {
    const empty: BehaviorJobResult = { skipped: true, partitionsCreated: 0, partitionsDropped: 0, purged: 0, cartAbandon: 0 };
    if (this.running) return empty;
    this.running = true;
    try {
      return await this.db.tx(async (q) => {
        const lock = await q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1) AS ok', [BEHAVIOR_JOB_LOCK_KEY]);
        if (!lock.rows[0]?.ok) return empty;
        const keep = this.config.behaviorEventRetentionDays;
        const created = await q.query<{ n: number }>('SELECT behavior.ensure_event_partitions(2) AS n');
        const dropped = await q.query<{ n: number }>('SELECT behavior.drop_old_event_partitions($1) AS n', [keep]);
        let purged = 0;
        const purge = async (sql: string, days: number): Promise<void> => {
          const r = await q.query(sql, [days]);
          purged += r.rowCount ?? 0;
        };
        await purge(`DELETE FROM behavior.event_dedup WHERE received_at < now() - make_interval(days => $1::int)`, keep);
        await purge(`DELETE FROM behavior.device_link WHERE last_seen_at < now() - make_interval(days => $1::int)`, this.config.behaviorDeviceLinkIdleDays);
        await purge(`DELETE FROM behavior.device_consent WHERE created_at < now() - make_interval(days => $1::int)`, this.config.behaviorDeviceConsentRetentionDays);
        await purge(`DELETE FROM behavior.segment_member WHERE received_at < now() - make_interval(days => $1::int)`, this.config.segmentRetentionDays);
        await purge(`DELETE FROM behavior.segment_chunk WHERE received_at < now() - make_interval(days => $1::int)`, this.config.segmentRetentionDays);
        await purge(`DELETE FROM behavior.erased_subject WHERE erased_at < now() - make_interval(days => $1::int)`, this.config.behaviorErasedSubjectRetentionDays);
        // Antrean erasure ETL: ≥ 100 hari (> TTL ClickHouse 90 hari; konfigurasi minimum 100)
        await purge(`DELETE FROM behavior.subject_erasure WHERE created_at < now() - make_interval(days => $1::int)`, this.config.behaviorErasedSubjectRetentionDays);
        // Penanda idempotensi event purchase (jendela job 48 jam)
        await purge(`DELETE FROM behavior.purchase_emitted WHERE emitted_at < now() - make_interval(days => $1::int)`, 30);
        const cartAbandon = this.config.behaviorTrackingEnabled ? await this.deriveCartAbandon(q) : 0;
        const res: BehaviorJobResult = {
          skipped: false,
          partitionsCreated: Number(created.rows[0]?.n ?? 0),
          partitionsDropped: Number(dropped.rows[0]?.n ?? 0),
          purged,
          cartAbandon,
        };
        this.logger.log(
          `Job behavior: partisi +${res.partitionsCreated}/-${res.partitionsDropped}, ${res.purged} baris dipurge, ${res.cartAbandon} cart_abandon`,
        );
        return res;
      });
    } catch (err) {
      this.logger.warn(`Job behavior gagal: ${err instanceof Error ? err.message : 'db'}`);
      return empty;
    } finally {
      this.running = false;
    }
  }

  private async deriveCartAbandon(q: Queryable): Promise<number> {
    // channel (web|app), channel_code, PT disalin dari cart_add terakhir: ETL menolak channel di luar web|app (fail closed)
    const cand = await q.query<{
      subject_key: string;
      subject_kind: 'device' | 'member';
      event_id: string;
      channel: string;
      channel_code: string | null;
      outlet_code: string | null;
      company_code: string;
    }>(
      `WITH last_add AS (
         SELECT DISTINCT ON (subject_key) subject_key, subject_kind, event_id, received_at, channel, channel_code, outlet_code, company_code
           FROM behavior.event_raw
          WHERE type = 'cart_add' AND received_at > now() - interval '48 hours'
          ORDER BY subject_key, received_at DESC, event_id
       )
       SELECT l.subject_key, l.subject_kind, l.event_id::text AS event_id, l.channel, l.channel_code, l.outlet_code, l.company_code
         FROM last_add l
        WHERE l.received_at <= now() - interval '24 hours'
          AND NOT EXISTS (SELECT 1 FROM behavior.event_raw e
                           WHERE e.subject_key = l.subject_key AND e.type IN ('checkout_start', 'cart_abandon')
                             AND e.received_at >= l.received_at)
        LIMIT $1`,
      [ABANDON_BATCH],
    );
    let n = 0;
    for (const c of cand.rows) {
      const subject = String(c.subject_key).trim();
      const eventId = deterministicUuid(`efn:cart_abandon:${subject}:${c.event_id}`);
      const ins = await q.query(
        `INSERT INTO behavior.event_dedup (subject_key, event_id) VALUES ($1, $2::uuid) ON CONFLICT DO NOTHING RETURNING event_id`,
        [subject, eventId],
      );
      if ((ins.rowCount ?? 0) === 0) continue;
      await q.query(
        `INSERT INTO behavior.event_raw
           (received_at, batch_id, subject_key, subject_kind, channel, event_id, type, client_ts, channel_code, outlet_code, company_code)
         VALUES (now(), $1::uuid, $2, $3, $7, $4::uuid, 'cart_abandon', now(), $5, $6, $8)`,
        [deterministicUuid(`efn:cart_abandon:batch:${eventId}`), subject, c.subject_kind, eventId, c.channel_code, c.outlet_code, c.channel, c.company_code],
      );
      n += 1;
    }
    return n;
  }
}
