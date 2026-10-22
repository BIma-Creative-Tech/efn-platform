import { Logger } from '@nestjs/common';
import { Counter } from 'prom-client';
import { AppConfig, FnbCompanyCode } from '../config/app-config';
import { isUuid } from '../common/validation';
import { MetricsService } from '../common/metrics/metrics.service';
import { CircuitBreaker } from '../odoo-rpc/circuit-breaker';
import { Json2Transport, OdooRpcGates, TransportResponse } from '../odoo-rpc/json2-client';
import { DATE_RE, ISO_UTC_RE, ODOO_STATES, OdooBookingState } from './booking.model';

/**
 * Klien json2 reservasi meja (docs/04 §15.3, path gateway 16–19). User teknis per PT (`efn_booking_pb`/`efn_booking_bcf`,
 * API key `odoo_api_key_booking_<pt>`). Respons Odoo divalidasi ulang (tidak dipercaya mentah). Galat bisnis Odoo
 * `efn_booking:<kode>` → `OdooBookingError('business', kode)`; selain itu kelas transport/auth tanpa detail.
 */

export const ODOO_BOOKING = Symbol('ODOO_BOOKING');

export const ODOO_BOOKING_PATHS = {
  availability: 'efn.table.booking/efn_booking_availability',
  create: 'efn.table.booking/efn_booking_create',
  cancel: 'efn.table.booking/efn_booking_cancel',
  status: 'efn.table.booking/efn_booking_status',
} as const;
export type OdooBookingMethod = keyof typeof ODOO_BOOKING_PATHS;

export const BOOKING_BUSINESS_CODES = [
  'invalid_input',
  'not_found',
  'slot_unavailable',
  'outside_hours',
  'party_size',
  'too_late',
  'too_far',
  'idempotency_conflict',
  'invalid_state',
  'booking_disabled',
] as const;
export type BookingBusinessCode = (typeof BOOKING_BUSINESS_CODES)[number];
export type BookingErrorKind = 'business' | 'auth' | 'not_found' | 'transport' | 'invalid_response' | 'circuit_open' | 'busy';

export class OdooBookingError extends Error {
  constructor(
    readonly kind: BookingErrorKind,
    readonly code: BookingBusinessCode | null = null,
  ) {
    super(code ? `odoo booking ${kind}: ${code}` : `odoo booking ${kind}`);
    this.name = 'OdooBookingError';
  }
}

export interface AvailabilitySlot {
  start_at: string;
  available: boolean;
}
export interface AvailabilityResult {
  outlet_code: string;
  date: string;
  timezone: string;
  slot_minutes: number;
  duration_minutes: number;
  min_party: number;
  max_party: number;
  horizon_days: number;
  slots: AvailabilitySlot[];
}
export interface OdooBookingResult {
  booking_ref: string;
  platform_booking_ref: string;
  state: OdooBookingState;
  start_at: string;
  end_at: string;
  party_size: number;
  table_label: string | null;
  seq: number;
}
export interface CreateArgs {
  idempotency_key: string;
  platform_booking_ref: string;
  start_at: string;
  party_size: number;
  guest_label?: string;
  note?: string;
}

export interface OdooBookingApi {
  availability(company: FnbCompanyCode, outlet: string, date: string, party: number): Promise<AvailabilityResult>;
  create(company: FnbCompanyCode, outlet: string, booking: CreateArgs): Promise<OdooBookingResult>;
  cancel(company: FnbCompanyCode, outlet: string, ref: string, key: string): Promise<{ state: OdooBookingState; seq: number }>;
  status(company: FnbCompanyCode, outlet: string, refs: string[]): Promise<OdooBookingResult[]>;
}

const BUSINESS_RE = /^efn_booking:([a-z_]{1,40})$/;

export function bookingBusinessError(body: unknown): BookingBusinessCode | null {
  if (typeof body !== 'object' || body === null) return null;
  const o = body as Record<string, unknown>;
  const cands: unknown[] = [o.message];
  if (typeof o.error === 'object' && o.error !== null) {
    const e = o.error as Record<string, unknown>;
    cands.push(e.message);
    if (typeof e.data === 'object' && e.data !== null) cands.push((e.data as Record<string, unknown>).message);
  }
  for (const c of cands) {
    if (typeof c !== 'string') continue;
    const m = BUSINESS_RE.exec(c.trim());
    if (m && (BOOKING_BUSINESS_CODES as readonly string[]).includes(m[1])) return m[1] as BookingBusinessCode;
  }
  return null;
}

const int = (v: unknown, min: number, max: number): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= min && v <= max;
const str = (v: unknown, max: number): v is string => typeof v === 'string' && v.length > 0 && v.length <= max;

export function parseAvailability(v: unknown): AvailabilityResult {
  if (typeof v !== 'object' || v === null) throw new OdooBookingError('invalid_response');
  const o = v as Record<string, unknown>;
  if (
    !str(o.outlet_code, 32) ||
    !(typeof o.date === 'string' && DATE_RE.test(o.date)) ||
    !str(o.timezone, 64) ||
    !int(o.slot_minutes, 5, 240) ||
    !int(o.duration_minutes, 15, 480) ||
    !int(o.min_party, 1, 50) ||
    !int(o.max_party, 1, 50) ||
    !int(o.horizon_days, 1, 365) ||
    !Array.isArray(o.slots) ||
    o.slots.length > 96
  ) {
    throw new OdooBookingError('invalid_response');
  }
  const slots = o.slots.map((s): AvailabilitySlot => {
    const r = s as Record<string, unknown>;
    if (typeof r !== 'object' || r === null || typeof r.start_at !== 'string' || !ISO_UTC_RE.test(r.start_at) || typeof r.available !== 'boolean') {
      throw new OdooBookingError('invalid_response');
    }
    return { start_at: r.start_at, available: r.available };
  });
  return {
    outlet_code: o.outlet_code,
    date: o.date,
    timezone: o.timezone,
    slot_minutes: o.slot_minutes,
    duration_minutes: o.duration_minutes,
    min_party: o.min_party,
    max_party: o.max_party,
    horizon_days: o.horizon_days,
    slots,
  };
}

export function parseBookingResult(v: unknown): OdooBookingResult {
  if (typeof v !== 'object' || v === null) throw new OdooBookingError('invalid_response');
  const o = v as Record<string, unknown>;
  const ok =
    str(o.booking_ref, 64) &&
    isUuid(o.platform_booking_ref) &&
    (ODOO_STATES as readonly unknown[]).includes(o.state) &&
    typeof o.start_at === 'string' &&
    ISO_UTC_RE.test(o.start_at) &&
    typeof o.end_at === 'string' &&
    ISO_UTC_RE.test(o.end_at) &&
    int(o.party_size, 1, 50) &&
    (o.table_label === null || str(o.table_label, 64)) &&
    int(o.seq, 0, 2_147_483_647);
  if (!ok) throw new OdooBookingError('invalid_response');
  return {
    booking_ref: o.booking_ref as string,
    platform_booking_ref: (o.platform_booking_ref as string).toLowerCase(),
    state: o.state as OdooBookingState,
    start_at: o.start_at as string,
    end_at: o.end_at as string,
    party_size: o.party_size as number,
    table_label: (o.table_label as string | null) ?? null,
    seq: o.seq as number,
  };
}

const BACKOFF_MS = [500, 1000];
const DEADLINE_MS = 8_000;

export class BookingMetrics {
  readonly odooRequests: Counter<'method' | 'outcome'>;
  readonly bookings: Counter<'status'>;
  constructor(metrics: MetricsService) {
    const registers = [metrics.registry];
    this.odooRequests = new Counter({
      name: 'efn_booking_odoo_requests_total',
      help: 'Panggilan json2 reservasi meja ke Odoo per metode dan hasil',
      labelNames: ['method', 'outcome'],
      registers,
    });
    this.bookings = new Counter({ name: 'efn_booking_transitions_total', help: 'Transisi status reservasi meja', labelNames: ['status'], registers });
  }
  odoo(method: string, outcome: string): void {
    this.odooRequests.inc({ method, outcome });
  }
  transition(status: string): void {
    this.bookings.inc({ status });
  }
}

export class Json2OdooBookingClient implements OdooBookingApi {
  private readonly logger = new Logger('OdooBooking');
  private readonly breakers = new Map<string, CircuitBreaker>();
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms).unref());
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    private readonly transport: Json2Transport,
    private readonly gates: OdooRpcGates,
    private readonly metrics: BookingMetrics,
  ) {}

  async availability(company: FnbCompanyCode, outlet: string, date: string, party: number): Promise<AvailabilityResult> {
    return parseAvailability(await this.call('availability', company, { outlet_code: outlet, date, party_size: party }));
  }

  async create(company: FnbCompanyCode, outlet: string, booking: CreateArgs): Promise<OdooBookingResult> {
    return parseBookingResult(await this.call('create', company, { outlet_code: outlet, booking }));
  }

  async cancel(company: FnbCompanyCode, outlet: string, ref: string, key: string): Promise<{ state: OdooBookingState; seq: number }> {
    const v = await this.call('cancel', company, { outlet_code: outlet, platform_booking_ref: ref, idempotency_key: key });
    const o = (v ?? {}) as Record<string, unknown>;
    if (!(ODOO_STATES as readonly unknown[]).includes(o.state) || !int(o.seq, 0, 2_147_483_647)) throw new OdooBookingError('invalid_response');
    return { state: o.state as OdooBookingState, seq: o.seq as number };
  }

  async status(company: FnbCompanyCode, outlet: string, refs: string[]): Promise<OdooBookingResult[]> {
    const v = await this.call('status', company, { outlet_code: outlet, platform_booking_refs: refs });
    if (!Array.isArray(v) || v.length > refs.length) throw new OdooBookingError('invalid_response');
    return v.map(parseBookingResult);
  }

  private breaker(company: string): CircuitBreaker {
    let b = this.breakers.get(company);
    if (!b) {
      b = new CircuitBreaker(5, 30_000, () => this.now(), (open) => {
        if (open) this.logger.warn(`Circuit Odoo booking PT ${company} TERBUKA (30 s)`);
      });
      this.breakers.set(company, b);
    }
    return b;
  }

  private classify(res: TransportResponse): { value: unknown } | OdooBookingError {
    let parsed: unknown;
    let jsonOk = true;
    try {
      parsed = res.body.length > 0 ? JSON.parse(res.body) : null;
    } catch {
      jsonOk = false;
    }
    if (res.status === 200) return jsonOk ? { value: parsed } : new OdooBookingError('invalid_response');
    const biz = bookingBusinessError(parsed);
    if (biz) return new OdooBookingError('business', biz);
    if (res.status === 401 || res.status === 403) return new OdooBookingError('auth');
    if (res.status === 404) return new OdooBookingError('not_found');
    if (res.status === 429 || res.status >= 500 || res.status === 0) return new OdooBookingError('transport');
    return new OdooBookingError('invalid_response');
  }

  private async call(method: OdooBookingMethod, company: FnbCompanyCode, args: Record<string, unknown>): Promise<unknown> {
    const breaker = this.breaker(company);
    const started = this.now();
    let apiKey: string;
    try {
      apiKey = this.config.odooApiKeyBooking(company);
    } catch {
      this.metrics.odoo(method, 'auth');
      throw new OdooBookingError('auth');
    }
    if (!breaker.allow()) {
      this.metrics.odoo(method, 'circuit_open');
      throw new OdooBookingError('circuit_open');
    }
    const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'application/json', Authorization: `Bearer ${apiKey}` };
    const db = this.config.odooDb;
    if (db) headers['X-Odoo-Database'] = db;
    const body = JSON.stringify({ ...args, context: { lang: 'id_ID' } });
    // Hanya baca (availability/status) yang diulang; create/cancel idempoten di Odoo → aman diulang juga.
    for (let attempt = 0; ; attempt += 1) {
      let release: () => void;
      try {
        release = await this.gates.public.acquire();
      } catch {
        this.metrics.odoo(method, 'busy');
        throw new OdooBookingError('busy');
      }
      let err: OdooBookingError;
      try {
        const out = this.classify(await this.transport(ODOO_BOOKING_PATHS[method], body, headers, this.config.odooRpcTimeoutMs));
        if (!(out instanceof OdooBookingError)) {
          breaker.success();
          this.metrics.odoo(method, 'ok');
          return out.value;
        }
        err = out;
      } catch {
        err = new OdooBookingError('transport');
      } finally {
        release();
      }
      if (err.kind !== 'transport') {
        breaker.neutral();
        this.metrics.odoo(method, err.kind);
        this.logger.warn(`json2 booking ${method} PT ${company}: ${err.kind}${err.code ? ` ${err.code}` : ''}`);
        throw err;
      }
      breaker.failure();
      const delay = attempt < BACKOFF_MS.length ? BACKOFF_MS[attempt] : -1;
      if (delay < 0 || this.now() - started + delay >= DEADLINE_MS || !breaker.allow()) {
        this.metrics.odoo(method, 'transport');
        throw err;
      }
      await this.sleep(delay);
    }
  }
}

/**
 * Stub in-memory (DEV/TEST, `ODOO_ONLINE_MOCK=true`). Jam 10:00–22:00 WIB, slot 30 menit, durasi 90, 2 meja (2 & 4
 * kursi), maks 8 tamu, konfirmasi otomatis untuk BCF dan staf untuk PB. Perilaku galat meniru modul Odoo.
 */
export class MockOdooBooking implements OdooBookingApi {
  readonly tables = [
    { label: 'Meja 1', seats: 2 },
    { label: 'Meja 2', seats: 4 },
    { label: 'Meja 3', seats: 6 },
  ];
  readonly bookings = new Map<string, OdooBookingResult & { key: string; table: string; outlet: string; company: FnbCompanyCode }>();
  now: () => number = () => Date.now();

  private slotsFor(date: string): number[] {
    const base = Date.parse(`${date}T00:00:00Z`) - 7 * 3600_000; // 00:00 WIB
    const out: number[] = [];
    for (let m = 10 * 60; m + 90 <= 22 * 60; m += 30) out.push(base + m * 60_000);
    return out;
  }

  private free(start: number, party: number, outlet: string): string | null {
    const end = start + 90 * 60_000;
    for (const t of [...this.tables].sort((a, b) => a.seats - b.seats)) {
      if (t.seats < party) continue;
      const busy = [...this.bookings.values()].some(
        (b) =>
          b.outlet === outlet && b.table === t.label && ['requested', 'confirmed', 'seated'].includes(b.state) && Date.parse(b.start_at) < end && Date.parse(b.end_at) > start,
      );
      if (!busy) return t.label;
    }
    return null;
  }

  async availability(_company: FnbCompanyCode, outlet: string, date: string, party: number): Promise<AvailabilityResult> {
    if (party < 1 || party > 8) throw new OdooBookingError('business', 'party_size');
    const lead = this.now() + 60 * 60_000;
    const slots = this.slotsFor(date)
      .filter((s) => s >= lead)
      .map((s) => ({ start_at: iso(s), available: this.free(s, party, outlet) !== null }));
    return { outlet_code: outlet, date, timezone: 'Asia/Jakarta', slot_minutes: 30, duration_minutes: 90, min_party: 1, max_party: 8, horizon_days: 30, slots };
  }

  async create(company: FnbCompanyCode, outlet: string, b: CreateArgs): Promise<OdooBookingResult> {
    const existing = this.bookings.get(b.platform_booking_ref);
    if (existing) {
      if (existing.start_at !== b.start_at || existing.party_size !== b.party_size) throw new OdooBookingError('business', 'idempotency_conflict');
      return strip(existing);
    }
    if (b.party_size < 1 || b.party_size > 8) throw new OdooBookingError('business', 'party_size');
    const start = Date.parse(b.start_at);
    const date = new Date(start + 7 * 3600_000).toISOString().slice(0, 10);
    if (!this.slotsFor(date).includes(start)) throw new OdooBookingError('business', 'outside_hours');
    if (start < this.now() + 60 * 60_000) throw new OdooBookingError('business', 'too_late');
    const table = this.free(start, b.party_size, outlet);
    if (!table) throw new OdooBookingError('business', 'slot_unavailable');
    const rec = {
      booking_ref: `BKG/DEMO/${String(this.bookings.size + 1).padStart(5, '0')}`,
      platform_booking_ref: b.platform_booking_ref,
      state: (company === 'BCF' ? 'confirmed' : 'requested') as OdooBookingState,
      start_at: b.start_at,
      end_at: iso(start + 90 * 60_000),
      party_size: b.party_size,
      table_label: table,
      seq: company === 'BCF' ? 1 : 0,
      key: b.idempotency_key,
      table,
      outlet,
      company,
    };
    this.bookings.set(b.platform_booking_ref, rec);
    return strip(rec);
  }

  async cancel(_company: FnbCompanyCode, _outlet: string, ref: string): Promise<{ state: OdooBookingState; seq: number }> {
    const rec = this.bookings.get(ref);
    if (!rec) throw new OdooBookingError('business', 'not_found');
    if (rec.state === 'cancelled') return { state: rec.state, seq: rec.seq };
    if (!['requested', 'confirmed'].includes(rec.state)) throw new OdooBookingError('business', 'invalid_state');
    rec.state = 'cancelled';
    rec.seq += 1;
    return { state: rec.state, seq: rec.seq };
  }

  async status(_company: FnbCompanyCode, _outlet: string, refs: string[]): Promise<OdooBookingResult[]> {
    return refs.flatMap((r) => {
      const rec = this.bookings.get(r);
      return rec ? [strip(rec)] : [];
    });
  }
}

function iso(ms: number): string {
  return new Date(ms).toISOString().replace('.000Z', 'Z');
}

function strip(r: OdooBookingResult): OdooBookingResult {
  return {
    booking_ref: r.booking_ref,
    platform_booking_ref: r.platform_booking_ref,
    state: r.state,
    start_at: r.start_at,
    end_at: r.end_at,
    party_size: r.party_size,
    table_label: r.table_label,
    seq: r.seq,
  };
}
