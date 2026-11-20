import { E164_RE, WA_PUBLISHERS, WaResult, WaResultStatus, parseWaMessage, toHash } from './contract';
import type { Limits } from './config';
import { Log, errMsg, maskMsisdn } from './log';
import type { Metrics } from './metrics';
import { MinuteWindow, PersistentState } from './state';
import { renderWaText } from './templates';
import type { SessionState, WaClient } from './wa-client';

export interface Delivery {
  payload: unknown;
  userId: string | undefined;
}

/** `ack` = selesai (termasuk gagal yang dilaporkan); `dlq` = nack tanpa requeue → `notify.wa_unofficial.dlq`. */
export type Disposition = 'ack' | 'dlq';

export interface ResultSink {
  publish(result: WaResult, messageId: string): Promise<void>;
}

export interface ProcessorDeps {
  client: WaClient;
  state: PersistentState;
  minute: MinuteWindow;
  limits: Limits;
  hashKey: Buffer;
  sink: ResultSink;
  log: Log;
  metrics: Metrics;
  senderLabel: string;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
  random?: () => number;
  /** Lama menunggu sesi pulih sebelum pesan dibuang ke DLQ. */
  connectWaitMs?: number;
}

const TRANSIENT: readonly SessionState[] = ['connecting', 'disconnected', 'idle'];

/**
 * Pemroses `notify.wa_unofficial` (docs/04 §28.7, ADR-044 §3). Urutan pagar:
 *  1. publisher `user_id` = `efn-bff` (selain itu DLQ);
 *  2. parse + allowlist kategori/template, verifikasi `to_hash = HMAC(to_e164)` → gagal = ack + result `failed/<alasan>`
 *     (payload tanpa idempotency_key/to_hash valid → DLQ karena tidak bisa dilaporkan);
 *  3. dedup `idempotency_key` (state lokal 48 jam) → ack tanpa kirim;
 *  4. `expires_at` lewat → `failed/expired`; penerima pernah STOP → `failed/opted_out`;
 *  5. sesi: tunggu pulih ≤ 30 dtk; `banned` → result `banned` + DLQ; tetap terputus → `disconnected` + DLQ (ADR-044 §3.7);
 *  6. kuota harian global / per penerima → `rate_limited` (ack); kuota per menit → TUNGGU slot;
 *  7. jitter acak 2–8 dtk; cek nomor terdaftar WhatsApp (`failed/not_on_whatsapp`);
 *  8. tandai diproses + flush SEBELUM kirim (at-most-once: lebih baik hilang daripada spam ganda), lalu kirim.
 * Nomor hanya di memori; log memakai mask. Isi pesan tidak di-log.
 */
export class Processor {
  private readonly now: () => number;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly random: () => number;
  private readonly connectWaitMs: number;

  constructor(private readonly d: ProcessorDeps) {
    this.now = d.now ?? (() => Date.now());
    this.sleep = d.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms).unref()));
    this.random = d.random ?? Math.random;
    this.connectWaitMs = d.connectWaitMs ?? 30_000;
  }

  async handle(delivery: Delivery): Promise<Disposition> {
    if (delivery.userId === undefined || !WA_PUBLISHERS.includes(delivery.userId)) {
      this.d.log.warn('pesan ditolak: publisher tidak berhak', { publisher: delivery.userId ?? '(kosong)' });
      this.d.metrics.inc('dlq', 'publisher');
      return 'dlq';
    }
    const parsed = parseWaMessage(delivery.payload, this.d.hashKey);
    if (!parsed.ok) {
      if (!parsed.reportable) {
        this.d.metrics.inc('dlq', parsed.reason);
        this.d.log.warn('pesan tidak valid tanpa kunci yang bisa dilaporkan → DLQ', { reason: parsed.reason });
        return 'dlq';
      }
      const { key, toHash: h, reason } = parsed.reportable;
      if (this.d.state.isProcessed(key)) return 'ack';
      this.d.state.markProcessed(key, this.now());
      await this.d.state.flush();
      await this.report(key, h, 'failed', reason);
      this.d.metrics.inc('failed', reason);
      this.d.log.warn('pesan ditolak (ack + failed)', { reason });
      return 'ack';
    }
    const msg = parsed.msg;
    const masked = maskMsisdn(msg.to_e164);
    if (this.d.state.isProcessed(msg.idempotency_key)) {
      this.d.metrics.inc('duplicate');
      return 'ack';
    }
    const finish = async (status: WaResultStatus, reason?: string): Promise<Disposition> => {
      this.d.state.markProcessed(msg.idempotency_key, this.now());
      await this.d.state.flush();
      await this.report(msg.idempotency_key, msg.to_hash, status, reason);
      if (status === 'rate_limited') this.d.metrics.inc('rate_limited', reason ?? '');
      else this.d.metrics.inc('failed', reason ?? status);
      this.d.log.info('pesan tidak dikirim', { to: masked, status, reason: reason ?? null });
      return 'ack';
    };

    if (msg.expires_at <= this.now()) return finish('failed', 'expired');
    if (this.d.state.isOptedOut(msg.to_hash, this.now())) return finish('failed', 'opted_out');

    const session = await this.awaitSession();
    if (session !== 'connected') {
      const status: WaResultStatus = session === 'banned' ? 'banned' : 'disconnected';
      await this.report(msg.idempotency_key, msg.to_hash, status, session);
      this.d.metrics.inc('dlq', status);
      this.d.log.warn('sesi WhatsApp tidak tersedia → DLQ', { status, session });
      return 'dlq';
    }

    if (this.d.state.dayCount(this.now()) >= this.d.limits.maxPerDay) return finish('rate_limited', 'max_per_day');
    if (this.d.state.memberCount(msg.to_hash, this.now()) >= this.d.limits.maxPerMemberPerDay) return finish('rate_limited', 'max_per_member_per_day');

    for (let w = this.d.minute.waitMs(this.now()); w > 0; w = this.d.minute.waitMs(this.now())) await this.sleep(w);
    const { jitterMinMs: lo, jitterMaxMs: hi } = this.d.limits;
    await this.sleep(lo + Math.floor(this.random() * (hi - lo + 1)));
    if (msg.expires_at <= this.now()) return finish('failed', 'expired');

    let exists: boolean;
    try {
      exists = await this.d.client.isOnWhatsApp(msg.to_e164);
    } catch (err) {
      this.d.log.warn('cek nomor WhatsApp gagal', { to: masked, err: errMsg(err) });
      return finish('failed', 'lookup_error');
    }
    if (!exists) return finish('failed', 'not_on_whatsapp');

    const text = renderWaText(msg.template, msg.params, this.d.senderLabel);
    // at-most-once: tandai + flush SEBELUM kirim
    const t = this.now();
    this.d.state.markProcessed(msg.idempotency_key, t);
    this.d.state.recordSend(msg.to_hash, t);
    this.d.minute.record(t);
    await this.d.state.flush();
    this.d.metrics.dayCount = this.d.state.dayCount(t);
    try {
      await this.d.client.sendText(msg.to_e164, text);
    } catch (err) {
      await this.report(msg.idempotency_key, msg.to_hash, 'failed', 'send_error');
      this.d.metrics.inc('failed', 'send_error');
      this.d.log.warn('kirim gagal', { to: masked, err: errMsg(err) });
      return 'ack';
    }
    await this.report(msg.idempotency_key, msg.to_hash, 'sent');
    this.d.metrics.inc('sent');
    this.d.log.info('terkirim', { to: masked, category: msg.category, template: msg.template });
    return 'ack';
  }

  /** Pesan masuk: "STOP" → penekan lokal + publish `opt_out` (BFF mencabut consent marketing). */
  async handleInbound(fromE164: string | null, text: string, isStop: (t: string) => boolean): Promise<void> {
    if (!isStop(text)) return;
    this.d.metrics.inc('stop_received');
    if (!fromE164 || !E164_RE.test(fromE164)) {
      this.d.metrics.inc('stop_unmapped');
      this.d.log.warn('STOP diterima tetapi pengirim tidak dapat dipetakan ke nomor (mis. alamat LID) — tidak diproses');
      return;
    }
    const h = toHash(this.d.hashKey, fromE164);
    const t = this.now();
    this.d.state.markOptOut(h, t);
    await this.d.state.flush();
    const result: WaResult = { schema_version: 1, ref_idempotency_key: null, kind: 'opt_out', to_hash: h, at: new Date(t).toISOString() };
    await this.d.sink.publish(result, `waoptout:${h}:${t}`);
    this.d.log.info('STOP diproses', { from: maskMsisdn(fromE164) });
  }

  private async report(key: string, h: string, status: WaResultStatus, reason?: string): Promise<void> {
    const result: WaResult = {
      schema_version: 1,
      ref_idempotency_key: key,
      kind: 'result',
      status,
      ...(reason ? { reason } : {}),
      to_hash: h,
      at: new Date(this.now()).toISOString(),
    };
    // message_id = `warslt:<key>:<status>` (bukan hanya `<key>`): pesan yang di-DLQ `disconnected` lalu diputar ulang
    // dan kemudian `sent` menghasilkan hasil kedua yang TIDAK boleh di-dedup BFF (usulan revisi §28.7).
    await this.d.sink.publish(result, `warslt:${key}:${status}`);
  }

  private async awaitSession(): Promise<SessionState> {
    const deadline = this.now() + this.connectWaitMs;
    let s = this.d.client.state();
    while (s !== 'connected' && TRANSIENT.includes(s) && this.now() < deadline) {
      await this.sleep(1000);
      s = this.d.client.state();
    }
    return s;
  }
}
