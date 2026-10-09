import { Injectable, Logger } from '@nestjs/common';
import { Counter, Gauge, Histogram } from 'prom-client';
import { MetricsService } from '../common/metrics/metrics.service';

const LABEL_RE = /^[a-z_]{1,40}$/;
const CHANNEL_RE = /^[A-Z][A-Z0-9-]{1,15}$/;
function lbl(v: string, fallback = 'other'): string {
  return LABEL_RE.test(v) ? v : fallback;
}
function ch(v: string): string {
  return CHANNEL_RE.test(v) ? v : 'unknown';
}

/** `refund` = refund belum final > 24 j; `refund_attention` = refund `manual_required|failed` (alert EfnOnlineRefundPending). */
export type StuckKind = 'placing' | 'paying' | 'awaiting_payment' | 'refund' | 'refund_attention';
export type MismatchStage = 'checkout' | 'webhook' | 'portion' | 'odoo';

/**
 * Metrik Fase 3 (docs/04 §10.11), didaftarkan pada registry `MetricsService` (endpoint `/metrics` mTLS yang sama).
 * Label hanya kode/kanal/state (kardinalitas kecil) — TIDAK PERNAH order_id, part_id, member, kode ambil, atau nominal.
 * Juga menulis baris log `EFN_ONLINE_ANOMALY {json}` (Loki) untuk anomali.
 */
@Injectable()
export class OnlineMetrics {
  private readonly logger = new Logger('OnlineAnomaly');
  readonly orders: Counter<'status'>;
  readonly parts: Counter<'channel' | 'status'>;
  readonly stuck: Gauge<'state'>;
  readonly amountMismatch: Counter<'stage'>;
  readonly odooRequests: Counter<'method' | 'outcome'>;
  readonly odooSeconds: Histogram<'method'>;
  readonly odooAuthFailures: Counter<'channel'>;
  readonly circuitOpen: Gauge<'channel'>;
  readonly events: Counter<'event' | 'outcome'>;
  readonly refunds: Counter<'status'>;
  readonly catalogSyncAge: Gauge<'channel'>;
  readonly backwardTransitions: Counter<'source'>;
  readonly refundsOpen: Gauge<'status'>;

  constructor(metrics: MetricsService) {
    const registers = [metrics.registry];
    this.orders = new Counter({ name: 'efn_online_orders_total', help: 'Transisi status platform order online', labelNames: ['status'], registers });
    this.parts = new Counter({ name: 'efn_online_parts_total', help: 'Transisi status bagian order per kanal', labelNames: ['channel', 'status'], registers });
    this.stuck = new Gauge({ name: 'efn_online_stuck_parts', help: 'Bagian/order/refund online yang tertahan melewati batas', labelNames: ['state'], registers });
    this.amountMismatch = new Counter({
      name: 'efn_online_amount_mismatch_total',
      help: 'Selisih nominal online per tahap',
      labelNames: ['stage'],
      registers,
    });
    this.odooRequests = new Counter({
      name: 'efn_online_odoo_requests_total',
      help: 'Panggilan json2 ke Odoo per metode dan hasil',
      labelNames: ['method', 'outcome'],
      registers,
    });
    this.odooSeconds = new Histogram({
      name: 'efn_online_odoo_request_seconds',
      help: 'Durasi panggilan json2 ke Odoo',
      labelNames: ['method'],
      buckets: [0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32],
      registers,
    });
    this.odooAuthFailures = new Counter({
      name: 'efn_online_odoo_auth_failures_total',
      help: 'Odoo/gateway menolak autentikasi (401/403) per kanal',
      labelNames: ['channel'],
      registers,
    });
    this.circuitOpen = new Gauge({ name: 'efn_online_circuit_open', help: '1 bila circuit breaker kanal terbuka', labelNames: ['channel'], registers });
    this.events = new Counter({ name: 'efn_online_events_total', help: 'Event Odoo → BFF per hasil', labelNames: ['event', 'outcome'], registers });
    this.refunds = new Counter({ name: 'efn_online_refunds_total', help: 'Transisi status refund online', labelNames: ['status'], registers });
    this.catalogSyncAge = new Gauge({
      name: 'efn_online_catalog_sync_age_seconds',
      help: 'Umur sinkron katalog sukses terakhir per kanal',
      labelNames: ['channel'],
      registers,
    });
    this.refundsOpen = new Gauge({
      name: 'efn_online_refunds_open',
      help: 'Refund online yang belum settled per status (diperbarui tiap siklus rekonsiliasi)',
      labelNames: ['status'],
      registers,
    });
    this.backwardTransitions = new Counter({
      name: 'efn_online_backward_transitions_total',
      help: 'Transisi mundur yang diabaikan (monoton) per sumber',
      labelNames: ['source'],
      registers,
    });
  }

  order(status: string): void {
    this.orders.inc({ status: lbl(status) });
  }
  part(channel: string, status: string): void {
    this.parts.inc({ channel: ch(channel), status: lbl(status) });
  }
  mismatch(stage: MismatchStage): void {
    this.amountMismatch.inc({ stage });
  }
  odoo(method: string, outcome: string, seconds: number): void {
    this.odooRequests.inc({ method: lbl(method), outcome: lbl(outcome) });
    this.odooSeconds.observe({ method: lbl(method) }, seconds);
  }
  authFailure(channel: string): void {
    this.odooAuthFailures.inc({ channel: ch(channel) });
  }
  circuit(channel: string, open: boolean): void {
    this.circuitOpen.set({ channel: ch(channel) }, open ? 1 : 0);
  }
  event(event: string, outcome: string): void {
    this.events.inc({ event: lbl(event), outcome: lbl(outcome) });
  }
  refund(status: string): void {
    this.refunds.inc({ status: lbl(status) });
  }
  backward(source: 'odoo' | 'bff'): void {
    this.backwardTransitions.inc({ source });
  }
  setRefundsOpen(counts: Record<string, number>): void {
    for (const s of ['requested', 'odoo_done', 'gateway_pending', 'gateway_done', 'manual_required', 'failed']) {
      this.refundsOpen.set({ status: s }, counts[s] ?? 0);
    }
  }
  setStuck(state: StuckKind, n: number): void {
    this.stuck.set({ state }, n);
  }
  setCatalogAge(channel: string, seconds: number): void {
    this.catalogSyncAge.set({ channel: ch(channel) }, Math.max(0, Math.floor(seconds)));
  }

  /**
   * Baris `EFN_ONLINE_ANOMALY {json}` (§10.11): `kind`, `severity`, id order/bagian/refund saja — tanpa PII, kode ambil,
   * QR, quote_hash, API key, atau body Odoo/gateway. Field lain dibuang kecuali angka/kode pendek dari daftar aman.
   */
  anomaly(kind: string, severity: 'warning' | 'critical', ids: Record<string, string | number | null | undefined> = {}): void {
    const safe: Record<string, string | number> = {};
    for (const [k, v] of Object.entries(ids)) {
      if (!ANOMALY_FIELDS.has(k) || v === null || v === undefined) continue;
      if (typeof v === 'number' && Number.isFinite(v)) safe[k] = v;
      else if (typeof v === 'string' && /^[A-Za-z0-9_.:-]{1,64}$/.test(v)) safe[k] = v;
    }
    this.logger.warn(`EFN_ONLINE_ANOMALY ${JSON.stringify({ kind: lbl(kind), severity, ...safe })}`);
  }
}

const ANOMALY_FIELDS = new Set(['order_id', 'part_id', 'refund_id', 'payment_id', 'channel', 'state', 'code', 'stage', 'expected', 'actual', 'method']);
