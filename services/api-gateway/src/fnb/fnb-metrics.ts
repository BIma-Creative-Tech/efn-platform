import { Injectable, Logger } from '@nestjs/common';
import { Counter, Gauge } from 'prom-client';
import { MetricsService } from '../common/metrics/metrics.service';

const LABEL_RE = /^[a-z_]{1,40}$/;
const lbl = (v: string): string => (LABEL_RE.test(v) ? v : 'other');

/** §14.A.7 anomali `EFN_FNB_ANOMALY {kind}` yang dikenal (selain ini → `other`). */
export const FNB_ANOMALY_KINDS = [
  'session_limit_hit',
  'submit_rate_limited',
  'qr_pos_member_conflict',
  'submit_reconciled',
  'submit_failed',
  'odoo_config_error',
  'event_unknown_submission',
  'earn_limited',
  'company_mismatch',
  // r15: SEC-173 (event Odoo→BFF melewati FNB_RL_EVENTS_*), K1 (order_uuid event ≠ yang tersimpan)
  'events_rate_limited',
  'order_uuid_mismatch',
] as const;

/**
 * Metrik QR meja (docs/04 §14.A.7, L-76) di registry `/metrics` (mTLS) yang sama. Label hanya status/metode/hasil —
 * TIDAK PERNAH token, id sesi, member, catatan, atau IP.
 *
 * - `efn_fnb_submissions_total{status}` — transisi status kiriman.
 * - `efn_fnb_sessions_open` (gauge) — sesi `open`.
 * - `efn_fnb_odoo_requests_total{method,outcome}` — panggilan json2 QR.
 * - L-76 (alert devsecops): `efn_fnb_sessions_open_overdue` (sesi `open` dibuka > 4 jam / melewati expires_at, sweeper
 *   macet), `efn_fnb_submissions_stuck` (kiriman `submitting` > 5 menit), `efn_fnb_qr_earn_pending` (order QR pada sesi
 *   ber-klaim member yang belum menerima `paid` > 15 menit setelah sesi ditutup/kedaluwarsa).
 * Baris log anomali: `EFN_FNB_ANOMALY {"kind":…,"severity":…,…}`; heartbeat job: `EFN_FNB_JOB {"status":"ok"|"error",…}`.
 */
@Injectable()
export class FnbMetrics {
  private readonly logger = new Logger('FnbAnomaly');
  readonly submissions: Counter<'status'>;
  readonly odooRequests: Counter<'method' | 'outcome'>;
  readonly sessionsOpen: Gauge;
  readonly sessionsOverdue: Gauge;
  readonly submissionsStuck: Gauge;
  readonly earnPending: Gauge;

  constructor(metrics: MetricsService) {
    const registers = [metrics.registry];
    this.submissions = new Counter({ name: 'efn_fnb_submissions_total', help: 'Transisi status kiriman QR meja', labelNames: ['status'], registers });
    this.odooRequests = new Counter({
      name: 'efn_fnb_odoo_requests_total',
      help: 'Panggilan json2 QR meja ke Odoo per metode dan hasil',
      labelNames: ['method', 'outcome'],
      registers,
    });
    this.sessionsOpen = new Gauge({ name: 'efn_fnb_sessions_open', help: 'Sesi meja QR berstatus open', registers });
    this.sessionsOverdue = new Gauge({ name: 'efn_fnb_sessions_open_overdue', help: 'Sesi QR open lebih dari 4 jam / lewat expires_at', registers });
    this.submissionsStuck = new Gauge({ name: 'efn_fnb_submissions_stuck', help: 'Kiriman QR berstatus submitting lebih dari 5 menit', registers });
    this.earnPending = new Gauge({ name: 'efn_fnb_qr_earn_pending', help: 'Order QR ber-klaim member tanpa paid lebih dari 15 menit setelah sesi selesai', registers });
  }

  submission(status: string): void {
    this.submissions.inc({ status: lbl(status) });
  }
  odoo(method: string, outcome: string): void {
    this.odooRequests.inc({ method: lbl(method), outcome: lbl(outcome) });
  }
  gauges(g: { open: number; overdue: number; stuck: number; earnPending: number }): void {
    this.sessionsOpen.set(g.open);
    this.sessionsOverdue.set(g.overdue);
    this.submissionsStuck.set(g.stuck);
    this.earnPending.set(g.earnPending);
  }

  /** Id yang boleh dicetak: table_session_id, submission_id, config_id, kode (tanpa token/member/catatan). */
  anomaly(kind: (typeof FNB_ANOMALY_KINDS)[number], severity: 'warning' | 'critical', ids: Record<string, string | number | null | undefined> = {}): void {
    const safe: Record<string, string | number> = {};
    for (const [k, v] of Object.entries(ids)) {
      if (!['table_session_id', 'submission_id', 'config_id', 'code', 'company', 'event'].includes(k) || v === null || v === undefined) continue;
      if (typeof v === 'number' && Number.isFinite(v)) safe[k] = v;
      else if (typeof v === 'string' && /^[A-Za-z0-9_.:-]{1,64}$/.test(v)) safe[k] = v;
    }
    this.logger.warn(`EFN_FNB_ANOMALY ${JSON.stringify({ kind, severity, ...safe })}`);
  }
}
