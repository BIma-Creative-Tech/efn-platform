import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit, Optional } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { QUEUES } from '../common/amqp/queues';
import { Db, Queryable } from '../common/db/db';
import { MetricsService } from '../common/metrics/metrics.service';
import { reportLoyaltyAnomaly } from './loyalty-anomaly';
import { ExpiryParams, LoyaltyPolicyService } from './loyalty-policy.service';
import { CompanyCode, LedgerBucket, LoyaltyService, isCompanyCode } from './loyalty.service';

export const EXPIRY_STORE = Symbol('EXPIRY_STORE');
/** Advisory lock per fase (bigint; berbeda dari kunci job lain). */
export const EXPIRY_LOCK = { NOTICE: 0x45464e58, EXPIRE: 0x45464e59 } as const; // 'EFNX', 'EFNY'
export const INACTIVITY_SOURCE = 'loyalty_expiry';
export const INACTIVITY_REASON = 'inactivity_expire';
export const NOTICE_TEMPLATE = 'loyalty_inactivity_notice';
const WIB_MS = 7 * 3600 * 1000;
const DAY_MS = 86_400_000;

export interface NoticeRow {
  id: string;
  member_hash: string;
  anchor_at: Date;
  due_at: Date;
}
export interface NoticeCandidate {
  member_hash: string;
  anchor_at: Date;
  /** 00:00 WIB hari setelah ulang N bulan jangkar (kalender PostgreSQL). */
  expiry_at: Date;
}

/** Penyimpanan job hangus — `PgExpiryStore` (runtime) / fake di spec. Semua metode dalam transaksi `tx` pemanggil. */
export interface ExpiryStore {
  tx<T>(fn: (s: ExpiryStore) => Promise<T>): Promise<T>;
  tryLock(key: number): Promise<boolean>;
  noticeCandidates(p: { epoch: Date; months: number; noticeDays: number; now: Date; limit: number }): Promise<NoticeCandidate[]>;
  /** false = notice untuk (member, jangkar) sudah ada. */
  insertNotice(n: NoticeRow & { notified_at: Date }): Promise<boolean>;
  supersedeOlder(memberHash: string, anchorAt: Date): Promise<number>;
  enqueue(queue: string, payload: Record<string, unknown>, messageId: string): Promise<void>;
  countDue(now: Date): Promise<number>;
  dueNotices(now: Date, limit: number): Promise<NoticeRow[]>;
  /** Notice `queued` untuk (member, jangkar) — dipakai `GET /v1/me`. */
  findNotice(memberHash: string, anchorAt: Date): Promise<NoticeRow | null>;
  lockMember(memberHash: string): Promise<void>;
  /** `GREATEST(last_activity_at, epoch)` saat ini (null = tidak ada baris aktivitas). */
  currentAnchor(memberHash: string, epoch: Date): Promise<Date | null>;
  /** Reservasi `reserved` aktif, bagian online terbuka, atau DSAR aktif → jangan hanguskan sekarang (coba besok). */
  hasBlocker(memberHash: string, now: Date): Promise<boolean>;
  lots(memberHash: string, now: Date): Promise<LedgerBucket[]>;
  /** Ciphertext `member_ref_enc` entri ledger terbaru subjek (kolom & AAD sama → disalin apa adanya). */
  latestMemberRefEnc(memberHash: string): Promise<Buffer | null>;
  insertExpireEntry(e: { memberHash: string; enc: Buffer; issuer: CompanyCode; points: number; expiresAt: Date | null }): Promise<void>;
  setNoticeStatus(id: string, status: 'superseded' | 'expired' | 'cancelled'): Promise<void>;
  /** D.7: `dsar_request(type='retention')`; false bila sudah ada request hapus aktif. */
  scheduleRetention(memberHash: string, scheduledAt: Date): Promise<boolean>;
  purge(now: Date, noticeDays: number, proofDays: number): Promise<number>;
  /** D.7: aktivitas selama masa tenggang → request retention `scheduled` dibatalkan. */
  cancelRetentionOnActivity(): Promise<number>;
  /**
   * SEC-171 r15: consent `marketing` AKTIF (baris `platform.consent` terbaru per member = `granted`) untuk akun
   * `member.member_hash` yang belum dihapus. Subjek tanpa akun (hanya ledger POS) / tanpa riwayat → false.
   */
  hasMarketingConsent(memberHash: string): Promise<boolean>;
  /**
   * SEC-171 + L-76: bukti TANPA identitas bahwa pemberitahuan tersedia lewat kanal `in_app` saja (WA tidak dikirim
   * karena consent tidak ada). Idempoten per notice.
   */
  recordInAppProof(noticeId: string, expireDate: string, at: Date): Promise<void>;
}

/** 00:00 WIB hari (kalender) yang memuat `ms`. */
function startOfWibDay(ms: number): number {
  return Math.floor((ms + WIB_MS) / DAY_MS) * DAY_MS - WIB_MS;
}
function wibDate(d: Date): string {
  return new Date(d.getTime() + WIB_MS).toISOString().slice(0, 10);
}
function wibHour(ms: number): number {
  return new Date(ms + WIB_MS).getUTCHours();
}

/**
 * Hangus 12 bulan tidak aktif (docs/04 §14.D, W-17; L-73/L-75/L-76/L-77). Logger `LoyaltyInactivityJob`, heartbeat
 * `EFN_LOYALTY_EXPIRY_RUN {"status":"ok|dry_run|aborted|error","phase":"notice|expire|anonymize",…}` tanpa PII
 * (Promtail/alert devsecops bergantung format & urutan kunci ini).
 *
 * - OFF bila `LOYALTY_EXPIRY_ENABLED=false` (default SEMUA lingkungan; production OFF sampai Legal & Meta, L-73).
 * - Parameter (L-75): `inactivity_expiry_months`, `expiry_notice_days`, `activity_epoch` dari kebijakan global EFN aktif,
 *   per kunci fallback env. Epoch tidak ada → job tidak berjalan (heartbeat error).
 * - Fase notice (≥ `LOYALTY_EXPIRY_NOTICE_HOUR_WIB`): member bersaldo > 0 yang jatuh tempo ≤ N hari lagi dan belum punya
 *   notice untuk jangkar ini → `loyalty_expiry_notice` (due = GREATEST(jatuh tempo, sekarang + N hari)) + outbox
 *   `notify.push` v2 `source: loyalty_expiry`, params HANYA `expire_date` (L-76, tanpa jumlah poin).
 *   SEC-171 r15: `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=true` (default, sampai Legal memutuskan) → WA hanya untuk member
 *   ber-consent `marketing` aktif; tanpa consent → notice TETAP dibuat (due ≥ N hari tetap dijamin, info in-app D.6),
 *   bukti anonim kanal `in_app`, metrik outcome `no_consent`, TANPA outbox WA.
 * - Fase expire (≥ `LOYALTY_EXPIRY_RUN_HOUR_WIB`): kandidat > `LOYALTY_EXPIRY_MAX_PER_RUN` → BATAL (aborted + anomali
 *   critical). Dry run (default) → hanya hitungan per penerbit. Selain itu per member satu transaksi: lockMember → jangkar
 *   bergeser → `superseded`; ada blocker → lewati (besok); setiap lot (penerbit, expires_at) bersisa > 0 → entri
 *   `inactivity_expire` (−sisa, kunci `inactivity_expire:<uuid acak>`), notice `expired`; lot utang tidak disentuh.
 * - Anonimisasi (D.7, `LOYALTY_INACTIVE_ANONYMIZE`, default false) → `dsar_request(type='retention')` → ErasureService.
 * - Metrik `efn_loyalty_expiry_total{phase,outcome}` naik sekali per member per fase (seri diinisialisasi 0, L-77).
 */
@Injectable()
export class LoyaltyInactivityJob implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger('LoyaltyInactivityJob');
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private lastNoticeDay: string | null = null;
  private lastExpireDay: string | null = null;
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(EXPIRY_STORE) private readonly store: ExpiryStore,
    private readonly metrics: MetricsService,
    @Optional() private readonly policies: LoyaltyPolicyService | null = null,
  ) {}

  onModuleInit(): void {
    this.metrics.initLoyaltyExpirySeries();
    const ms = this.config.loyaltyExpiryTickMs;
    if (this.config.loyaltyExpiryEnabled && ms > 0) {
      this.timer = setInterval(() => void this.tick(), ms);
      this.timer.unref();
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  private heartbeat(status: 'ok' | 'dry_run' | 'aborted' | 'error', phase: 'notice' | 'expire' | 'anonymize', fields: Record<string, unknown> = {}): void {
    const line = `EFN_LOYALTY_EXPIRY_RUN ${JSON.stringify({ status, phase, ...fields })}`;
    if (status === 'aborted' || status === 'error') this.logger.error(line);
    else this.logger.log(line);
  }

  /** Dipanggil timer; tiap fase sekali per hari WIB setelah jamnya. Tidak pernah melempar. */
  async tick(): Promise<void> {
    if (this.running || !this.config.loyaltyExpiryEnabled) return;
    this.running = true;
    try {
      const nowMs = this.now();
      const day = wibDate(new Date(nowMs));
      if (wibHour(nowMs) >= this.config.loyaltyExpiryNoticeHourWib && this.lastNoticeDay !== day) {
        await this.noticeOnce();
        this.lastNoticeDay = day;
      }
      if (wibHour(nowMs) >= this.config.loyaltyExpiryRunHourWib && this.lastExpireDay !== day) {
        await this.expireOnce();
        await this.purgeOnce().catch(() => undefined);
        this.lastExpireDay = day;
      }
    } finally {
      this.running = false;
    }
  }

  private async params(): Promise<ExpiryParams> {
    if (this.policies) return this.policies.expiryParams();
    return {
      inactivityMonths: this.config.loyaltyInactivityExpiryMonths,
      noticeDays: this.config.loyaltyExpiryNoticeDays,
      activityEpoch: this.config.loyaltyActivityEpoch,
      source: 'env',
      policyVersion: null,
    };
  }

  // ───────────── fase notice ─────────────
  async noticeOnce(): Promise<{ queued: number; dry_run: boolean; no_consent: number } | null> {
    try {
      const p = await this.params();
      if (!p.activityEpoch) {
        this.heartbeat('error', 'notice', { error: 'activity_epoch_missing' });
        return null;
      }
      const epoch = p.activityEpoch;
      const now = new Date(this.now());
      const dry = this.config.loyaltyExpiryDryRun;
      const requireConsent = this.config.loyaltyExpiryNoticeRequireConsent;
      let queued = 0;
      let noConsent = 0;
      const out = await this.store.tx(async (s) => {
        if (!(await s.tryLock(EXPIRY_LOCK.NOTICE))) return null;
        const cands = await s.noticeCandidates({ epoch, months: p.inactivityMonths, noticeDays: p.noticeDays, now, limit: this.config.loyaltyExpiryMaxPerRun + 1 });
        if (cands.length > this.config.loyaltyExpiryMaxPerRun) {
          this.metrics.recordLoyaltyExpiry('notice', 'aborted');
          return 'aborted' as const;
        }
        if (dry) {
          this.metrics.recordLoyaltyExpiry('notice', 'dry_run', cands.length);
          return { candidates: cands.length };
        }
        for (const c of cands) {
          const id = randomUUID();
          const due = new Date(Math.max(c.expiry_at.getTime(), startOfWibDay(now.getTime()) + (p.noticeDays + 1) * DAY_MS));
          if (!(await s.insertNotice({ id, member_hash: c.member_hash, anchor_at: c.anchor_at, due_at: due, notified_at: now }))) {
            this.metrics.recordLoyaltyExpiry('notice', 'skipped');
            continue;
          }
          await s.supersedeOlder(c.member_hash, c.anchor_at);
          if (requireConsent && !(await s.hasMarketingConsent(c.member_hash))) {
            // SEC-171: tanpa consent tercatat → tidak ada WA; bukti anonim in-app (L-76), notice tetap berlaku
            await s.recordInAppProof(id, wibDate(due), now);
            noConsent += 1;
            this.metrics.recordLoyaltyExpiry('notice', 'no_consent');
            continue;
          }
          const key = `notify:loyalty_inactivity:${id}`;
          await s.enqueue(
            QUEUES.NOTIFY_PUSH,
            {
              schema_version: 2,
              idempotency_key: key,
              member_hash: c.member_hash,
              source: INACTIVITY_SOURCE,
              source_ref: id,
              template: NOTICE_TEMPLATE,
              params: { expire_date: wibDate(due) }, // L-76: tanpa jumlah poin
            },
            key,
          );
          queued += 1;
          this.metrics.recordLoyaltyExpiry('notice', 'queued');
        }
        return { candidates: cands.length };
      });
      if (out === null) return null;
      if (out === 'aborted') {
        reportLoyaltyAnomaly(this.logger, this.metrics, 'expiry_run_aborted', { phase: 'notice', limit: this.config.loyaltyExpiryMaxPerRun });
        this.heartbeat('aborted', 'notice', { max_per_run: this.config.loyaltyExpiryMaxPerRun });
        return null;
      }
      this.heartbeat(dry ? 'dry_run' : 'ok', 'notice', {
        candidates: out.candidates,
        queued,
        months: p.inactivityMonths,
        notice_days: p.noticeDays,
        source: p.source,
        no_consent: noConsent,
        require_consent: requireConsent,
      });
      return { queued, dry_run: dry, no_consent: noConsent };
    } catch (err) {
      this.metrics.recordLoyaltyExpiry('notice', 'error');
      this.heartbeat('error', 'notice', { error: err instanceof Error ? err.name : 'error' });
      return null;
    }
  }

  // ───────────── fase expire (+ anonymize) ─────────────
  async expireOnce(): Promise<{ expired: number; superseded: number; skipped: number; points_by_issuer: Record<string, number>; anonymize: number } | null> {
    try {
      const p = await this.params();
      if (!p.activityEpoch) {
        this.heartbeat('error', 'expire', { error: 'activity_epoch_missing' });
        return null;
      }
      const epoch = p.activityEpoch;
      const now = new Date(this.now());
      const cancelled = await this.store.cancelRetentionOnActivity();
      if (cancelled > 0) this.metrics.recordLoyaltyExpiry('anonymize', 'skipped', cancelled);
      const total = await this.store.countDue(now);
      const max = this.config.loyaltyExpiryMaxPerRun;
      if (total > max) {
        this.metrics.recordLoyaltyExpiry('expire', 'aborted');
        reportLoyaltyAnomaly(this.logger, this.metrics, 'expiry_run_aborted', { phase: 'expire', candidates: total, limit: max });
        this.heartbeat('aborted', 'expire', { candidates: total, max_per_run: max });
        return null;
      }
      const byIssuer: Record<string, number> = {};
      const dry = this.config.loyaltyExpiryDryRun;
      if (dry) {
        for (const n of await this.store.dueNotices(now, max)) {
          for (const b of await this.store.lots(n.member_hash, now)) {
            if (b.remaining > 0 && b.issuer) byIssuer[b.issuer] = (byIssuer[b.issuer] ?? 0) + b.remaining;
          }
        }
        this.metrics.recordLoyaltyExpiry('expire', 'dry_run', total);
        this.heartbeat('dry_run', 'expire', { candidates: total, by_issuer: byIssuer });
        return { expired: 0, superseded: 0, skipped: 0, points_by_issuer: byIssuer, anonymize: 0 };
      }
      const res = { expired: 0, superseded: 0, skipped: 0, anonymize: 0 };
      let processed = 0;
      while (processed < max) {
        const batch = await this.store.tx(async (s) => ((await s.tryLock(EXPIRY_LOCK.EXPIRE)) ? s.dueNotices(now, this.config.loyaltyExpiryBatchSize) : null));
        if (batch === null || batch.length === 0) break;
        let progressed = 0;
        for (const n of batch) {
          processed += 1;
          const r = await this.expireMember(n, epoch, now, byIssuer);
          res[r.outcome] += 1;
          if (r.outcome !== 'skipped') progressed += 1;
          if (r.anonymized) res.anonymize += 1;
        }
        if (progressed === 0) break; // semua tertahan blocker → coba besok
      }
      this.heartbeat('ok', 'expire', { candidates: total, ...res, by_issuer: byIssuer });
      if (this.config.loyaltyInactiveAnonymize) this.heartbeat('ok', 'anonymize', { scheduled: res.anonymize });
      return { ...res, points_by_issuer: byIssuer };
    } catch (err) {
      this.metrics.recordLoyaltyExpiry('expire', 'error');
      this.heartbeat('error', 'expire', { error: err instanceof Error ? err.name : 'error' });
      return null;
    }
  }

  private async expireMember(
    n: NoticeRow,
    epoch: Date,
    now: Date,
    byIssuer: Record<string, number>,
  ): Promise<{ outcome: 'expired' | 'superseded' | 'skipped'; anonymized: boolean }> {
    return this.store.tx(async (s) => {
      await s.lockMember(n.member_hash);
      const anchor = await s.currentAnchor(n.member_hash, epoch);
      if (anchor === null || anchor.getTime() > n.anchor_at.getTime()) {
        await s.setNoticeStatus(n.id, 'superseded');
        this.metrics.recordLoyaltyExpiry('expire', 'superseded');
        return { outcome: 'superseded' as const, anonymized: false };
      }
      if (await s.hasBlocker(n.member_hash, now)) {
        this.metrics.recordLoyaltyExpiry('expire', 'skipped');
        return { outcome: 'skipped' as const, anonymized: false };
      }
      const lots = (await s.lots(n.member_hash, now)).filter((b) => b.remaining > 0);
      if (lots.length > 0) {
        const enc = await s.latestMemberRefEnc(n.member_hash);
        if (!enc) throw new Error('member_ref_enc ledger subjek tidak ada');
        // Snapshot (penerbit, expires_at, sisa) SEBELUM menulis entri: objek bucket bisa berubah oleh store setelah insert
        // (fake spec memutasi `remaining`; implementasi lain bisa me-refresh). Nilai yang dicatat ke ledger, total per
        // penerbit, dan metrik harus berasal dari angka yang sama.
        const plan = lots.map((b) => ({ issuer: b.issuer, expiresAt: b.expiresAt, points: b.remaining }));
        for (const lot of plan) {
          const issuer = lot.issuer;
          if (!issuer || !isCompanyCode(issuer)) throw new Error('penerbit lot tidak dikenal');
          await s.insertExpireEntry({ memberHash: n.member_hash, enc, issuer, points: lot.points, expiresAt: lot.expiresAt });
          byIssuer[issuer] = (byIssuer[issuer] ?? 0) + lot.points;
          this.metrics.loyaltyExpiredPoints.inc({ issuer }, lot.points);
        }
      }
      await s.setNoticeStatus(n.id, 'expired');
      this.metrics.recordLoyaltyExpiry('expire', 'expired');
      let anonymized = false;
      if (this.config.loyaltyInactiveAnonymize) {
        anonymized = await s.scheduleRetention(n.member_hash, new Date(now.getTime() + this.config.loyaltyInactiveAnonymizeGraceDays * DAY_MS));
        this.metrics.recordLoyaltyExpiry('anonymize', anonymized ? 'scheduled' : 'skipped');
      }
      return { outcome: 'expired' as const, anonymized };
    });
  }

  /** Retensi notice (400 h) & bukti tanpa identitas (L-76). */
  async purgeOnce(): Promise<number> {
    return this.store.purge(new Date(this.now()), this.config.loyaltyExpiryNoticeRetentionDays, this.config.loyaltyExpiryProofRetentionDays);
  }

  /**
   * §14.D.6 `GET /v1/me` → `points_expiry`. `expires_on` hanya bila saldo > 0, job aktif, dan ≤ 60 hari lagi
   * (minimisasi). Tanggal = tanggal WIB.
   */
  async expiryInfo(memberHash: string, balance: number): Promise<{ inactive_since: string | null; expires_on: string | null; notice_sent: boolean }> {
    const p = await this.params();
    const epoch = p.activityEpoch ?? new Date(0);
    const anchor = await this.store.currentAnchor(memberHash, epoch);
    if (!anchor) return { inactive_since: null, expires_on: null, notice_sent: false };
    const expiry = calendarExpiry(anchor, p.inactivityMonths);
    const notice = await this.store.findNotice(memberHash, anchor);
    const due = notice ? notice.due_at : expiry;
    const within = due.getTime() - this.now() <= 60 * DAY_MS;
    return {
      inactive_since: wibDate(anchor),
      // tanggal (WIB) poin hangus pukul 00:00 — sama dengan `expire_date` pesan WA
      expires_on: this.config.loyaltyExpiryEnabled && p.activityEpoch && balance > 0 && within ? wibDate(due) : null,
      notice_sent: notice !== null,
    };
  }
}

/** Jatuh tempo (TS, pembanding SQL): 00:00 WIB hari setelah tanggal jangkar + N bulan kalender (tanggal dipotong ke akhir bulan). */
export function calendarExpiry(anchor: Date, months: number): Date {
  const w = new Date(anchor.getTime() + WIB_MS);
  const y = w.getUTCFullYear();
  const m = w.getUTCMonth() + months;
  const lastDay = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  const day = Math.min(w.getUTCDate(), lastDay);
  return new Date(Date.UTC(y, m, day + 1) - WIB_MS);
}

/** Implementasi PostgreSQL `ExpiryStore` (SQL parametris, owner efn_bff). */
export class PgExpiryStore implements ExpiryStore {
  constructor(
    private readonly q: Queryable,
    private readonly db: Db | null,
    private readonly loyalty: LoyaltyService,
  ) {}

  async tx<T>(fn: (s: ExpiryStore) => Promise<T>): Promise<T> {
    if (!this.db) return fn(this);
    return this.db.tx((q) => fn(new PgExpiryStore(q, null, this.loyalty)));
  }
  async tryLock(key: number): Promise<boolean> {
    const r = await this.q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1) AS ok', [key]);
    return r.rows[0]?.ok === true;
  }
  async noticeCandidates(p: { epoch: Date; months: number; noticeDays: number; now: Date; limit: number }): Promise<NoticeCandidate[]> {
    // expiry_at = 00:00 WIB hari setelah (tanggal WIB jangkar + N bulan kalender PostgreSQL)
    const r = await this.q.query<{ member_hash: string; anchor_at: Date | string; expiry_at: Date | string }>(
      `WITH a AS (
         SELECT member_hash, GREATEST(last_activity_at, $1::timestamptz) AS anchor_at
           FROM platform.loyalty_member_activity
       ), e AS (
         SELECT member_hash, anchor_at,
                ((date_trunc('day', anchor_at AT TIME ZONE 'Asia/Jakarta') + make_interval(months => $2::int) + interval '1 day')
                   AT TIME ZONE 'Asia/Jakarta') AS expiry_at
           FROM a
       )
       SELECT e.member_hash, e.anchor_at, e.expiry_at FROM e
        WHERE e.expiry_at - make_interval(days => $3::int) <= $4::timestamptz
          AND NOT EXISTS (SELECT 1 FROM platform.loyalty_expiry_notice n WHERE n.member_hash = e.member_hash AND n.anchor_at = e.anchor_at)
          AND (SELECT COALESCE(SUM(l.delta_points), 0) FROM platform.loyalty_ledger l
                WHERE l.member_ref_hash = e.member_hash AND (l.expires_at IS NULL OR l.expires_at > $4::timestamptz)) > 0
        ORDER BY e.expiry_at
        LIMIT $5`,
      [p.epoch.toISOString(), p.months, p.noticeDays, p.now.toISOString(), p.limit],
    );
    return r.rows.map((x) => ({ member_hash: x.member_hash, anchor_at: new Date(x.anchor_at), expiry_at: new Date(x.expiry_at) }));
  }
  async insertNotice(n: NoticeRow & { notified_at: Date }): Promise<boolean> {
    const r = await this.q.query(
      `INSERT INTO platform.loyalty_expiry_notice (id, member_hash, anchor_at, due_at, notified_at, status)
       VALUES ($1, $2, $3, $4, $5, 'queued') ON CONFLICT (member_hash, anchor_at) DO NOTHING`,
      [n.id, n.member_hash, n.anchor_at.toISOString(), n.due_at.toISOString(), n.notified_at.toISOString()],
    );
    return (r.rowCount ?? 0) > 0;
  }
  async supersedeOlder(memberHash: string, anchorAt: Date): Promise<number> {
    const r = await this.q.query(
      `UPDATE platform.loyalty_expiry_notice SET status = 'superseded', updated_at = now()
        WHERE member_hash = $1 AND status = 'queued' AND anchor_at < $2`,
      [memberHash, anchorAt.toISOString()],
    );
    return r.rowCount ?? 0;
  }
  async enqueue(queue: string, payload: Record<string, unknown>, messageId: string): Promise<void> {
    await this.q.query(`INSERT INTO platform.outbox (queue, payload, message_id) VALUES ($1, $2::jsonb, $3)`, [queue, JSON.stringify(payload), messageId]);
  }
  async countDue(now: Date): Promise<number> {
    const r = await this.q.query<{ n: string }>(`SELECT count(*) AS n FROM platform.loyalty_expiry_notice WHERE status = 'queued' AND due_at <= $1`, [now.toISOString()]);
    return Number(r.rows[0]?.n ?? 0);
  }
  async dueNotices(now: Date, limit: number): Promise<NoticeRow[]> {
    const r = await this.q.query<{ id: string; member_hash: string; anchor_at: Date | string; due_at: Date | string }>(
      `SELECT id::text AS id, member_hash, anchor_at, due_at FROM platform.loyalty_expiry_notice
        WHERE status = 'queued' AND due_at <= $1 ORDER BY due_at LIMIT $2`,
      [now.toISOString(), limit],
    );
    return r.rows.map((x) => ({ id: x.id, member_hash: x.member_hash, anchor_at: new Date(x.anchor_at), due_at: new Date(x.due_at) }));
  }
  async findNotice(memberHash: string, anchorAt: Date): Promise<NoticeRow | null> {
    const r = await this.q.query<{ id: string; member_hash: string; anchor_at: Date | string; due_at: Date | string }>(
      `SELECT id::text AS id, member_hash, anchor_at, due_at FROM platform.loyalty_expiry_notice
        WHERE member_hash = $1 AND anchor_at = $2 AND status = 'queued'`,
      [memberHash, anchorAt.toISOString()],
    );
    const x = r.rows[0];
    return x ? { id: x.id, member_hash: x.member_hash, anchor_at: new Date(x.anchor_at), due_at: new Date(x.due_at) } : null;
  }
  lockMember(memberHash: string): Promise<void> {
    return this.loyalty.lockMember(this.q, memberHash);
  }
  async currentAnchor(memberHash: string, epoch: Date): Promise<Date | null> {
    const r = await this.q.query<{ anchor: Date | string }>(
      `SELECT GREATEST(last_activity_at, $2::timestamptz) AS anchor FROM platform.loyalty_member_activity WHERE member_hash = $1`,
      [memberHash, epoch.toISOString()],
    );
    return r.rows[0] ? new Date(r.rows[0].anchor) : null;
  }
  async hasBlocker(memberHash: string, now: Date): Promise<boolean> {
    const r = await this.q.query<{ blocked: boolean }>(
      `SELECT (EXISTS (SELECT 1 FROM platform.loyalty_reservation WHERE member_hash = $1 AND status = 'reserved' AND hold_until > $2)
            OR EXISTS (SELECT 1 FROM platform.online_order o JOIN platform.member m ON m.id = o.member_id
                        WHERE m.member_hash = $1
                          AND (o.status IN ('created', 'awaiting_payment', 'awaiting_counter_payment', 'paid', 'fulfilling', 'review')))
            OR EXISTS (SELECT 1 FROM platform.dsar_request WHERE member_hash = $1 AND type IN ('delete', 'retention')
                        AND status IN ('scheduled', 'processing'))) AS blocked`,
      [memberHash, now.toISOString()],
    );
    return r.rows[0]?.blocked === true;
  }
  lots(memberHash: string, now: Date): Promise<LedgerBucket[]> {
    return this.loyalty.buckets(this.q, memberHash, now);
  }
  async latestMemberRefEnc(memberHash: string): Promise<Buffer | null> {
    const r = await this.q.query<{ member_ref_enc: Buffer }>(
      `SELECT member_ref_enc FROM platform.loyalty_ledger WHERE member_ref_hash = $1 AND member_ref_enc IS NOT NULL ORDER BY id DESC LIMIT 1`,
      [memberHash],
    );
    return r.rows[0]?.member_ref_enc ?? null;
  }
  async insertExpireEntry(e: { memberHash: string; enc: Buffer; issuer: CompanyCode; points: number; expiresAt: Date | null }): Promise<void> {
    const ok = await this.loyalty.insertEntry(
      {
        idempotencyKey: `${INACTIVITY_REASON}:${randomUUID()}`, // acak (pola SEC-31), idempotensi = lock + cek ulang + status notice
        memberHash: e.memberHash,
        memberRefEnc: e.enc,
        delta: -e.points,
        reason: INACTIVITY_REASON,
        source: INACTIVITY_SOURCE,
        sourceRef: null,
        companyCode: e.issuer,
        expiresAt: e.expiresAt,
        issuerCompanyCode: e.issuer,
      },
      this.q,
    );
    if (!ok) throw new Error('entri inactivity_expire bentrok kunci acak');
  }
  async setNoticeStatus(id: string, status: 'superseded' | 'expired' | 'cancelled'): Promise<void> {
    await this.q.query(`UPDATE platform.loyalty_expiry_notice SET status = $2, updated_at = now() WHERE id = $1`, [id, status]);
  }
  async scheduleRetention(memberHash: string, scheduledAt: Date): Promise<boolean> {
    const r = await this.q.query(
      `INSERT INTO platform.dsar_request (id, member_id, member_hash, type, status, stores_pending, requested_at, scheduled_at, reason)
       SELECT $1, (SELECT id FROM platform.member WHERE member_hash = $2 AND deleted_at IS NULL), $2, 'retention', 'scheduled', '{}', now(), $3, 'inactivity'
        WHERE NOT EXISTS (SELECT 1 FROM platform.dsar_request WHERE member_hash = $2 AND type IN ('delete', 'retention') AND status IN ('scheduled', 'processing'))
       ON CONFLICT DO NOTHING`,
      [randomUUID(), memberHash, scheduledAt.toISOString()],
    );
    return (r.rowCount ?? 0) > 0;
  }
  async cancelRetentionOnActivity(): Promise<number> {
    const r = await this.q.query(
      `UPDATE platform.dsar_request d SET status = 'cancelled', member_hash = NULL, reason = 'inactivity_cancelled_activity'
        WHERE d.type = 'retention' AND d.status = 'scheduled'
          AND EXISTS (SELECT 1 FROM platform.loyalty_member_activity a WHERE a.member_hash = d.member_hash AND a.last_activity_at > d.requested_at)`,
    );
    return r.rowCount ?? 0;
  }
  async hasMarketingConsent(memberHash: string): Promise<boolean> {
    // Sumber consent yang sama dengan ConsentService (append-only; status = baris terbaru per member+purpose)
    const r = await this.q.query<{ ok: boolean }>(
      `SELECT EXISTS (
         SELECT 1 FROM platform.member m
          WHERE m.member_hash = $1 AND m.deleted_at IS NULL
            AND (SELECT c.granted FROM platform.consent c
                  WHERE c.member_id = m.id AND c.purpose = 'marketing'
                  ORDER BY c.created_at DESC, c.id DESC LIMIT 1) IS TRUE) AS ok`,
      [memberHash],
    );
    return r.rows[0]?.ok === true;
  }
  async recordInAppProof(noticeId: string, expireDate: string, at: Date): Promise<void> {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(expireDate)) return;
    await this.q.query(
      `INSERT INTO platform.loyalty_expiry_notice_proof (id, notice_id, category, channel, expire_date, sent_at)
       VALUES (gen_random_uuid(), $1, 'loyalty_inactivity_notice', 'in_app', $2::date, $3)
       ON CONFLICT (notice_id) DO NOTHING`,
      [noticeId, expireDate, at.toISOString()],
    );
  }
  async purge(now: Date, noticeDays: number, proofDays: number): Promise<number> {
    const a = await this.q.query(
      `DELETE FROM platform.loyalty_expiry_notice WHERE id IN (SELECT id FROM platform.loyalty_expiry_notice
        WHERE status <> 'queued' AND created_at < $1::timestamptz - make_interval(days => $2::int) LIMIT 5000)`,
      [now.toISOString(), noticeDays],
    );
    const b = await this.q.query(
      `DELETE FROM platform.loyalty_expiry_notice_proof WHERE id IN (SELECT id FROM platform.loyalty_expiry_notice_proof
        WHERE sent_at < $1::timestamptz - make_interval(days => $2::int) LIMIT 5000)`,
      [now.toISOString(), proofDays],
    );
    return (a.rowCount ?? 0) + (b.rowCount ?? 0);
  }
}
