import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { MetricsService } from '../common/metrics/metrics.service';
import { EARN_SOURCE_PUBLISHERS } from '../loyalty/dto/loyalty-earn.message';
import { LoyaltyAnomalyKind, reportLoyaltyAnomaly } from '../loyalty/loyalty-anomaly';
import { MIGRATION_SOURCE } from '../migration/migration.service';
import { POS_SOURCES, REVERSE_REASON, startOfWibDay } from './pos-loyalty.service';

/** Advisory lock job (bigint, berbeda dari 'EFNL' sweeper, 'EFNE' erasure, 'EFNR' retensi). */
export const LOYALTY_RECON_LOCK_KEY = 0x45464e43; // 'EFNC'
/** Batas baris per jenis pemeriksaan per siklus (log & biaya query tetap kecil saat insiden besar). */
export const RECON_ROW_LIMIT = 200;

/**
 * Daftar `source` ledger yang ditulis BFF. Selain ini → `ledger_unknown_source` (admin/SQL manual/server action).
 * `dsar` = forfeit erasure (erasure.service.ts).
 */
export const KNOWN_LEDGER_SOURCES: readonly string[] = [
  POS_SOURCES.EARN,
  POS_SOURCES.REDEEM,
  POS_SOURCES.REVERSE,
  MIGRATION_SOURCE,
  'dsar',
  // G22: poin online (§14.C) dan hangus tidak aktif (§14.D)
  'online',
  'loyalty_expiry',
  // G31 §20.6: reward & clawback MGM
  'mgm',
  ...Object.keys(EARN_SOURCE_PUBLISHERS),
];

export interface ReconResult {
  skipped: boolean;
  anomalies: number;
  byKind: Partial<Record<LoyaltyAnomalyKind, number>>;
}

interface EarnRow {
  order_ref: string;
  company_code: string;
  eligible_amount: string | number;
  points_earned: string | number;
  ledger_points: string | number;
  rule_points: string | number;
}

/**
 * SEC-61 (Gelombang 13c): rekonsiliasi ringan ledger ↔ aturan earn POS, di dalam BFF (tanpa data Odoo).
 *
 * Tiap `LOYALTY_RECON_INTERVAL_MS` (default 1 jam; satu replika per siklus via `pg_try_advisory_xact_lock`, transaksi
 * READ ONLY) memeriksa data `LOYALTY_RECON_LOOKBACK_HOURS` terakhir (default 26 jam):
 * 1. Catatan earn POS (`loyalty_pos_earn`): Σ ledger `pos_earn` order ≠ `points_earned` → `earn_ledger_mismatch`;
 *    `points_earned` > floor(eligible / `LOYALTY_EARN_IDR_PER_POINT`) → `earn_above_rule`; > plafon per order saat ini →
 *    `earn_above_order_cap`.
 * 2. Entri ledger `pos_earn` tanpa catatan earn (tidak ter-erasure) → `earn_ledger_orphan`.
 * 3. Σ earn per member per hari WIB > `LOYALTY_EARN_DAILY_MAX_POINTS` → `member_day_above_limit` (tanpa member_hash di
 *    log; contoh order_ref terbesar).
 * 4. Order dengan reverse di jendela: clawback kumulatif > earn → `reverse_exceeds_earn`; restore > redeem →
 *    `restore_exceeds_redeem`.
 * 5. Entri ledger dengan `source` di luar `KNOWN_LEDGER_SOURCES` → `ledger_unknown_source` (agregat per source/PT).
 *
 * Setiap temuan = satu baris `EFN_LOYALTY_ANOMALY {json}` + `efn_loyalty_anomaly_total{kind,severity}`. Akhir siklus
 * selalu mencetak `EFN_LOYALTY_RECON {json}` (heartbeat: `anomalies`, `lookback_hours`) dan
 * `efn_loyalty_recon_last_success_timestamp_seconds` untuk alert dead-man. Temuan berulang di siklus berikutnya selama
 * masih di jendela (sengaja: alert tetap menyala sampai ditangani).
 *
 * BUKAN pengganti rekonsiliasi ledger ↔ `pos.order` Odoo (SEC-61 rekomendasi 3, ETL) — job ini tidak bisa melihat order
 * yang tidak pernah ada di Odoo. Tarif yang dipakai = tarif SAAT INI: bila tarif dinaikkan (poin per Rupiah turun)
 * setelah earn, `earn_above_rule` dapat positif palsu sampai data keluar dari jendela.
 */
@Injectable()
export class LoyaltyReconService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(LoyaltyReconService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  now: () => number = () => Date.now();

  constructor(
    @Inject(DB) private readonly db: Db,
    private readonly config: AppConfig,
    private readonly metrics: MetricsService,
  ) {}

  onModuleInit(): void {
    const ms = this.config.loyaltyReconIntervalMs;
    if (ms > 0) {
      this.timer = setInterval(() => void this.runOnce(), ms);
      this.timer.unref();
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Tidak pernah melempar (dipanggil dari timer). */
  async runOnce(): Promise<ReconResult> {
    const skipped: ReconResult = { skipped: true, anomalies: 0, byKind: {} };
    if (this.running) return skipped;
    this.running = true;
    try {
      const found = await this.db.tx(async (q) => {
        // READ ONLY harus pernyataan pertama transaksi; advisory lock tetap diizinkan di transaksi read-only
        await q.query('SET TRANSACTION READ ONLY');
        const lock = await q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1) AS ok', [LOYALTY_RECON_LOCK_KEY]);
        if (!lock.rows[0]?.ok) return null;
        return this.check(q);
      });
      if (found === null) {
        this.metrics.recordLoyaltyRecon('skipped');
        return skipped;
      }
      const byKind: Partial<Record<LoyaltyAnomalyKind, number>> = {};
      for (const f of found) {
        reportLoyaltyAnomaly(this.logger, this.metrics, f.kind, f.fields);
        byKind[f.kind] = (byKind[f.kind] ?? 0) + 1;
      }
      this.metrics.recordLoyaltyRecon('ok', this.now());
      this.logger.log(
        `EFN_LOYALTY_RECON ${JSON.stringify({ status: 'ok', anomalies: found.length, lookback_hours: this.config.loyaltyReconLookbackHours })}`,
      );
      return { skipped: false, anomalies: found.length, byKind };
    } catch (err) {
      this.metrics.recordLoyaltyRecon('error');
      // pesan error pg tidak memuat nilai parameter; tetap tanpa detail baris
      this.logger.error(`EFN_LOYALTY_RECON ${JSON.stringify({ status: 'error' })} ${err instanceof Error ? err.message : String(err)}`);
      return skipped;
    } finally {
      this.running = false;
    }
  }

  private async check(q: Queryable): Promise<{ kind: LoyaltyAnomalyKind; fields: Record<string, string | number> }[]> {
    const nowMs = this.now();
    const since = new Date(nowMs - this.config.loyaltyReconLookbackHours * 3_600_000);
    const out: { kind: LoyaltyAnomalyKind; fields: Record<string, string | number> }[] = [];
    // G21 §13.1: earn dihitung dengan kebijakan PT (≤ plafon env) → aturan rekonsiliasi = PLAFON (batas keras yang tidak
    // boleh dilampaui kebijakan mana pun). Default plafon = env lama → perilaku pra-G21 sama.
    const ceilings = this.config.loyaltyPolicyCeilings;
    const rate = ceilings.earnIdrPerPointMin;
    const cap = ceilings.maxPointsPerOrderMax;

    // 1. Catatan earn vs ledger & aturan
    const earns = await q.query<EarnRow>(
      `SELECT e.order_ref, e.company_code, e.eligible_amount, e.points_earned,
              COALESCE(l.total, 0)::bigint AS ledger_points,
              floor(e.eligible_amount / $2::numeric)::bigint AS rule_points
         FROM platform.loyalty_pos_earn e
         LEFT JOIN LATERAL (
           SELECT SUM(x.delta_points) AS total FROM platform.loyalty_ledger x
            WHERE x.source = 'pos_earn' AND x.source_ref = e.order_ref
         ) l ON true
        WHERE e.created_at >= $1
          AND (COALESCE(l.total, 0) <> e.points_earned
               OR e.points_earned > floor(e.eligible_amount / $2::numeric)
               OR e.points_earned > $3)
        ORDER BY e.created_at
        LIMIT $4`,
      [since.toISOString(), rate, cap, RECON_ROW_LIMIT],
    );
    for (const r of earns.rows) {
      const points = Number(r.points_earned);
      const ledger = Number(r.ledger_points);
      const rule = Number(r.rule_points);
      const base = { company: r.company_code, order_ref: r.order_ref, points, source: 'recon' };
      if (ledger !== points) out.push({ kind: 'earn_ledger_mismatch', fields: { ...base, ledger_points: ledger } });
      if (points > rule) out.push({ kind: 'earn_above_rule', fields: { ...base, rule_points: rule, eligible_amount: Number(r.eligible_amount) } });
      if (points > cap) out.push({ kind: 'earn_above_order_cap', fields: { ...base, limit: cap } });
    }

    // 2. Ledger pos_earn tanpa catatan earn (erasure menghapus catatan earn tetapi menandai ledger erased_at)
    const orphans = await q.query<{ order_ref: string; company_code: string; ledger_points: string | number }>(
      `SELECT l.source_ref AS order_ref, l.company_code, SUM(l.delta_points)::bigint AS ledger_points
         FROM platform.loyalty_ledger l
        WHERE l.source = 'pos_earn' AND l.created_at >= $1 AND l.erased_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM platform.loyalty_pos_earn e WHERE e.order_ref = l.source_ref)
        GROUP BY l.source_ref, l.company_code
        LIMIT $2`,
      [since.toISOString(), RECON_ROW_LIMIT],
    );
    for (const r of orphans.rows) {
      out.push({
        kind: 'earn_ledger_orphan',
        fields: { company: r.company_code, order_ref: r.order_ref ?? 'null', ledger_points: Number(r.ledger_points), source: 'recon' },
      });
    }

    // 3. Σ earn per member per hari WIB (dari awal hari WIB jendela)
    const daily = ceilings.dailyMaxPointsMemberMax;
    if (daily > 0) {
      const days = await q.query<{ day: string; orders: string | number; points: string | number; order_ref: string }>(
        `SELECT to_char((created_at AT TIME ZONE 'Asia/Jakarta')::date, 'YYYY-MM-DD') AS day,
                COUNT(*)::bigint AS orders, SUM(points_earned)::bigint AS points,
                (array_agg(order_ref ORDER BY points_earned DESC))[1] AS order_ref
           FROM platform.loyalty_pos_earn
          WHERE created_at >= $1
          GROUP BY member_hash, 1
         HAVING SUM(points_earned) > $2
          LIMIT $3`,
        [startOfWibDay(since.getTime()).toISOString(), daily, RECON_ROW_LIMIT],
      );
      for (const r of days.rows) {
        out.push({
          kind: 'member_day_above_limit',
          fields: { day: r.day, orders: Number(r.orders), points: Number(r.points), limit: daily, order_ref: r.order_ref, source: 'recon' },
        });
      }
    }

    // 4. Reverse kumulatif vs earn/redeem order asal
    const reverses = await q.query<{
      order_ref: string;
      company_code: string;
      earned: string | number;
      redeemed: string | number;
      clawed: string | number;
      restored: string | number;
    }>(
      `WITH touched AS (
         SELECT DISTINCT source_ref FROM platform.loyalty_ledger
          WHERE source = 'pos_reverse' AND created_at >= $1 AND source_ref IS NOT NULL
       ), agg AS (
         SELECT l.source_ref,
                MIN(l.company_code) AS company_code,
                COALESCE(SUM(l.delta_points) FILTER (WHERE l.source = 'pos_earn'), 0)::bigint AS earned,
                COALESCE(-SUM(l.delta_points) FILTER (WHERE l.source = 'pos_redeem'), 0)::bigint AS redeemed,
                COALESCE(-SUM(l.delta_points) FILTER (WHERE l.source = 'pos_reverse' AND l.reason = $2), 0)::bigint AS clawed,
                COALESCE(SUM(l.delta_points) FILTER (WHERE l.source = 'pos_reverse' AND l.reason = $3), 0)::bigint AS restored
           FROM platform.loyalty_ledger l
           JOIN touched t ON t.source_ref = l.source_ref
          WHERE l.source IN ('pos_earn', 'pos_redeem', 'pos_reverse')
          GROUP BY l.source_ref
       )
       SELECT source_ref AS order_ref, company_code, earned, redeemed, clawed, restored
         FROM agg
        WHERE clawed > earned OR restored > redeemed
        LIMIT $4`,
      [since.toISOString(), REVERSE_REASON.CLAWBACK, REVERSE_REASON.RESTORE, RECON_ROW_LIMIT],
    );
    for (const r of reverses.rows) {
      const base = { company: r.company_code, order_ref: r.order_ref, source: 'recon' };
      const [earned, redeemed, clawed, restored] = [r.earned, r.redeemed, r.clawed, r.restored].map(Number);
      if (clawed > earned) out.push({ kind: 'reverse_exceeds_earn', fields: { ...base, earned, clawed } });
      if (restored > redeemed) out.push({ kind: 'restore_exceeds_redeem', fields: { ...base, redeemed, restored } });
    }

    // 5. Source ledger tak dikenal (penulis selain BFF)
    const unknown = await q.query<{ source: string; company_code: string; entries: string | number; points: string | number }>(
      `SELECT source, company_code, COUNT(*)::bigint AS entries, SUM(delta_points)::bigint AS points
         FROM platform.loyalty_ledger
        WHERE created_at >= $1 AND NOT (source = ANY($2::text[]))
        GROUP BY source, company_code
        LIMIT $3`,
      [since.toISOString(), [...KNOWN_LEDGER_SOURCES], RECON_ROW_LIMIT],
    );
    for (const r of unknown.rows) {
      out.push({
        kind: 'ledger_unknown_source',
        fields: { company: r.company_code, ledger_source: r.source, entries: Number(r.entries), points: Number(r.points), source: 'recon' },
      });
    }
    return out;
  }
}
