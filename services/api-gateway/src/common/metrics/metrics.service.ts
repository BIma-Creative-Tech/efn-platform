import { Injectable, OnModuleInit } from '@nestjs/common';
import { Counter, Registry, collectDefaultMetrics } from 'prom-client';

export type OtpPurposeLabel = 'pos_promo' | 'login' | 'dsar';
/** `budget_exhausted` = budget global per jam habis (SEC-06) — kandidat alert DevSecOps. */
export type OtpRequestOutcome = 'sent' | 'delivery_failed' | 'rate_limited' | 'budget_exhausted';
export type OtpVerifyOutcome = 'verified' | 'invalid' | 'expired' | 'locked';
export type WhatsAppSendOutcome = 'success' | 'failure';

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

  get contentType(): string {
    return this.registry.contentType;
  }

  render(): Promise<string> {
    return this.registry.metrics();
  }
}
