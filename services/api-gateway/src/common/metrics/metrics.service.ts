import { Injectable, OnModuleInit } from '@nestjs/common';
import { Counter, Gauge, Registry, collectDefaultMetrics } from 'prom-client';

export type OtpPurposeLabel = 'pos_promo' | 'pos_redeem' | 'login' | 'dsar';
/** `budget_exhausted` = budget global per jam habis (SEC-06) — kandidat alert DevSecOps. */
export type OtpRequestOutcome = 'sent' | 'delivery_failed' | 'rate_limited' | 'budget_exhausted';
export type OtpVerifyOutcome = 'verified' | 'invalid' | 'expired' | 'locked';
export type WhatsAppSendOutcome = 'success' | 'failure';
export type LoyaltyPosOp = 'earn' | 'reserve' | 'commit' | 'release' | 'reverse';
export type LoyaltyPointsKind = 'earned' | 'reserved' | 'redeemed' | 'clawed_back' | 'restored';

/**
 * Metrik Prometheus BFF. Nama counter disepakati dengan alert rules DevSecOps:
 * - efn_otp_request_total{purpose,outcome}
 * - efn_otp_verify_total{purpose,outcome}
 * - efn_whatsapp_send_total{outcome}
 * - efn_altcha_challenge_total{level} (SEC-34)
 * Label TIDAK PERNAH memuat nomor HP, member_hash, order_ref, atau IP (kardinalitas & UU PDP).
 * Registry per-instance (bukan global) agar unit test bisa membuat banyak instance.
 */
@Injectable()
export class MetricsService implements OnModuleInit {
  readonly registry = new Registry();

  readonly otpRequest = new Counter({
    name: 'efn_otp_request_total',
    help: 'Permintaan OTP per tujuan dan hasil',
    labelNames: ['purpose', 'outcome'] as const,
    registers: [this.registry],
  });

  readonly otpVerify = new Counter({
    name: 'efn_otp_verify_total',
    help: 'Verifikasi OTP per tujuan dan hasil',
    labelNames: ['purpose', 'outcome'] as const,
    registers: [this.registry],
  });

  readonly whatsappSend = new Counter({
    name: 'efn_whatsapp_send_total',
    help: 'Pengiriman pesan WhatsApp (OTP & notifikasi) per hasil',
    labelNames: ['outcome'] as const,
    registers: [this.registry],
  });

  /** SEC-34: challenge ALTCHA per level biaya adaptif (0 = normal, 3 = maksimum). Tanpa IP di label. */
  readonly altchaChallenge = new Counter({
    name: 'efn_altcha_challenge_total',
    help: 'Challenge ALTCHA yang diterbitkan per level biaya adaptif',
    labelNames: ['level'] as const,
    registers: [this.registry],
  });

  /**
   * Fase 1 loyalty POS (docs/04 §9): jumlah panggilan per operasi & hasil. `op` ∈ earn|reserve|commit|release|reverse;
   * `outcome` = `ok`, `replay` (idempoten), `zero` (earn 0 poin), `late` (commit reservasi kedaluwarsa), atau kode
   * error kontrak (`insufficient_points`, `token_invalid`, …). Tanpa member/order di label.
   */
  readonly loyaltyPosOps = new Counter({
    name: 'efn_loyalty_pos_ops_total',
    help: 'Operasi loyalty POS (earn/redeem/reverse) per hasil',
    labelNames: ['op', 'outcome'] as const,
    registers: [this.registry],
  });

  /** Poin yang benar-benar dibukukan/ditahan: kind ∈ earned|reserved|redeemed|clawed_back|restored, per PT (4 nilai). */
  readonly loyaltyPosPoints = new Counter({
    name: 'efn_loyalty_pos_points_total',
    help: 'Poin loyalty POS per jenis mutasi dan PT',
    labelNames: ['kind', 'company'] as const,
    registers: [this.registry],
  });

  readonly loyaltyReservationExpired = new Counter({
    name: 'efn_loyalty_reservation_expired_total',
    help: 'Reservasi redeem poin yang dilepas otomatis karena kedaluwarsa',
    registers: [this.registry],
  });

  /**
   * SEC-61 (Gelombang 13c): anomali poin (`kind` dari daftar tetap `LOYALTY_ANOMALY_KINDS`, severity warning|critical).
   * Pasangan baris log `EFN_LOYALTY_ANOMALY {json}`. Kandidat alert: `increase(...{severity="critical"}[15m]) > 0`.
   */
  readonly loyaltyAnomaly = new Counter({
    name: 'efn_loyalty_anomaly_total',
    help: 'Anomali loyalty (pagar batas earn, rekonsiliasi ledger, migrasi) per jenis',
    labelNames: ['kind', 'severity'] as const,
    registers: [this.registry],
  });

  /** SEC-61/SEC-66: poin saldo pembuka migrasi K-7 yang dibukukan, per PT. */
  readonly loyaltyMigrationPoints = new Counter({
    name: 'efn_loyalty_migration_points_total',
    help: 'Poin saldo pembuka migrasi yang dibukukan per PT',
    labelNames: ['company'] as const,
    registers: [this.registry],
  });

  /** SEC-61: hasil siklus job rekonsiliasi loyalty (`ok` | `error` | `skipped`). */
  readonly loyaltyReconRuns = new Counter({
    name: 'efn_loyalty_recon_runs_total',
    help: 'Siklus job rekonsiliasi loyalty per hasil',
    labelNames: ['outcome'] as const,
    registers: [this.registry],
  });

  /** SEC-61: dead-man — waktu (unix detik) siklus rekonsiliasi terakhir yang sukses. */
  readonly loyaltyReconLastSuccess = new Gauge({
    name: 'efn_loyalty_recon_last_success_timestamp_seconds',
    help: 'Waktu siklus rekonsiliasi loyalty terakhir yang sukses',
    registers: [this.registry],
  });

  /** Metrik default proses Node (CPU, heap, event loop) hanya di runtime, bukan di unit test. */
  onModuleInit(): void {
    collectDefaultMetrics({ register: this.registry });
  }

  recordOtpRequest(purpose: OtpPurposeLabel, outcome: OtpRequestOutcome): void {
    this.otpRequest.inc({ purpose, outcome });
  }

  recordOtpVerify(purpose: OtpPurposeLabel, outcome: OtpVerifyOutcome): void {
    this.otpVerify.inc({ purpose, outcome });
  }

  recordWhatsAppSend(outcome: WhatsAppSendOutcome): void {
    this.whatsappSend.inc({ outcome });
  }

  recordAltchaChallenge(level: number): void {
    this.altchaChallenge.inc({ level: String(Math.max(0, Math.min(3, Math.floor(level)))) });
  }

  recordLoyaltyPosOp(op: LoyaltyPosOp, outcome: string): void {
    // outcome dari kode internal (bukan input pengguna); batasi bentuk agar kardinalitas terjaga
    this.loyaltyPosOps.inc({ op, outcome: /^[a-z_]{1,40}$/.test(outcome) ? outcome : 'error' });
  }

  recordLoyaltyPosPoints(kind: LoyaltyPointsKind, company: string, points: number): void {
    if (points > 0) this.loyaltyPosPoints.inc({ kind, company }, points);
  }

  recordLoyaltyAnomaly(kind: string, severity: string): void {
    this.loyaltyAnomaly.inc({
      kind: /^[a-z_]{1,40}$/.test(kind) ? kind : 'other',
      severity: severity === 'critical' ? 'critical' : 'warning',
    });
  }

  recordLoyaltyMigrationPoints(company: string, points: number): void {
    if (points > 0) this.loyaltyMigrationPoints.inc({ company }, points);
  }

  recordLoyaltyRecon(outcome: 'ok' | 'error' | 'skipped', atMs?: number): void {
    this.loyaltyReconRuns.inc({ outcome });
    if (outcome === 'ok') this.loyaltyReconLastSuccess.set(Math.floor((atMs ?? Date.now()) / 1000));
  }

  recordLoyaltyReservationExpired(n: number): void {
    if (n > 0) this.loyaltyReservationExpired.inc(n);
  }

  get contentType(): string {
    return this.registry.contentType;
  }

  render(): Promise<string> {
    return this.registry.metrics();
  }
}
