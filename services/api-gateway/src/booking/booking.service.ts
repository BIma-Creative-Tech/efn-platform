import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { createHash, randomUUID } from 'node:crypto';
import { AppConfig, FnbCompanyCode } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { QUEUES } from '../common/amqp/queues';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import { CatalogService } from '../catalog/catalog.service';
import {
  BookingErrors,
  BookingEventInput,
  BookingRow,
  BookingStatus,
  BookingView,
  CreateBookingInput,
  IDEMPOTENCY_KEY_RE,
  STATUS_RANK,
  toView,
} from './booking.model';
import { BOOKING_REPO, BookingRepo } from './booking.repository';
import { AvailabilityResult, BookingMetrics, ODOO_BOOKING, OdooBookingApi, OdooBookingError, OdooBookingResult } from './odoo-booking';

export interface BookingOutlet {
  outlet_code: string;
  company_code: FnbCompanyCode;
  channel_code: string;
  public_name: string;
  brand_code: string | null;
}

export type BookingEventOutcome = 'applied' | 'duplicate' | 'stale' | 'unknown';

const AVAIL_TTL_S = 30;
const LIST_LIMIT = 20;
const RECONCILE_BATCH = 50;

/**
 * Reservasi meja (docs/04 §15.5). Gerai = outlet kanal katalog PT ∈ `BOOKING_COMPANIES` (L-87: satu sumber outlet).
 * Alur buat: baris `pending` (idempoten per member+key) → RPC Odoo `efn_booking_create` (idempoten `booking:create:<id>`)
 * → status Odoo. Odoo gagal transport → baris tetap `pending` dan klien mendapat 503; ulang dengan key sama aman.
 */
@Injectable()
export class BookingService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger('Booking');
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(BOOKING_REPO) private readonly repo: BookingRepo,
    @Inject(ODOO_BOOKING) private readonly odoo: OdooBookingApi,
    @Inject(KV_STORE) private readonly kv: KvStore,
    private readonly catalog: CatalogService,
    private readonly metrics: BookingMetrics,
  ) {}

  onModuleInit(): void {
    if (!this.config.bookingEnabled) return;
    this.timer = setInterval(() => void this.jobOnce(), 60_000);
    this.timer.unref();
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  requireEnabled(): void {
    if (!this.config.bookingEnabled) throw BookingErrors.unavailable();
  }

  // ───────────── gerai ─────────────
  async outlets(brand?: string | null): Promise<BookingOutlet[]> {
    const companies = new Set<string>(this.config.bookingCompanies);
    const out: BookingOutlet[] = [];
    const seen = new Set<string>();
    for (const ch of await this.catalog.channels(brand)) {
      if (!companies.has(ch.company_code)) continue;
      for (const o of ch.outlets) {
        if (seen.has(o.outlet_code)) continue;
        seen.add(o.outlet_code);
        out.push({
          outlet_code: o.outlet_code,
          company_code: ch.company_code as FnbCompanyCode,
          channel_code: ch.channel_code,
          public_name: o.public_name,
          brand_code: ch.brand_code,
        });
      }
    }
    return out;
  }

  private async outlet(code: string): Promise<BookingOutlet> {
    const found = (await this.outlets()).find((o) => o.outlet_code === code);
    if (!found) throw BookingErrors.outletUnavailable();
    return found;
  }

  // ───────────── ketersediaan ─────────────
  async availability(outletCode: string, date: string, party: number): Promise<AvailabilityResult & { outlet_name: string }> {
    const outlet = await this.outlet(outletCode);
    const cacheKey = `booking:avail:${outlet.outlet_code}:${date}:${party}`;
    const cached = await this.kv.get(cacheKey).catch(() => null);
    if (cached) return JSON.parse(cached) as AvailabilityResult & { outlet_name: string };
    let res: AvailabilityResult;
    try {
      res = await this.odoo.availability(outlet.company_code, outlet.outlet_code, date, party);
    } catch (e) {
      throw this.mapOdoo(e);
    }
    const view = { ...res, outlet_name: outlet.public_name };
    await this.kv.setEx(cacheKey, JSON.stringify(view), AVAIL_TTL_S).catch(() => undefined);
    return view;
  }

  // ───────────── buat ─────────────
  async create(memberId: string, key: string | undefined, input: CreateBookingInput): Promise<BookingView> {
    if (!key) throw BookingErrors.idempotencyKeyRequired();
    if (!IDEMPOTENCY_KEY_RE.test(key)) throw Errors.invalidRequest();
    // Hash TANPA teks tamu (tidak disimpan) → beda catatan saja dengan key sama dianggap replay yang sama.
    const sha = createHash('sha256')
      .update(JSON.stringify([input.outlet_code, input.start_at, input.party_size]))
      .digest('hex');
    const existing = await this.repo.byKey(memberId, key);
    if (existing) {
      if (existing.request_sha256 !== sha) throw BookingErrors.idempotencyConflict();
      if (existing.status !== 'pending') return toView(existing, this.now());
      return this.submit(existing, input);
    }
    const outlet = await this.outlet(input.outlet_code);
    const start = new Date(input.start_at);
    if (start.getTime() <= this.now()) throw BookingErrors.tooLate();
    const row = await this.repo.tx(async (r) => {
      const active = await r.countActive(memberId, new Date(this.now()));
      if (active >= this.config.bookingMaxActivePerMember) throw BookingErrors.tooManyActive();
      return r.insert({
        id: randomUUID(),
        member_id: memberId,
        idempotency_key: key,
        request_sha256: sha,
        company_code: outlet.company_code,
        channel_code: outlet.channel_code,
        outlet_code: outlet.outlet_code,
        outlet_name: outlet.public_name,
        start_at: start,
        end_at: new Date(start.getTime() + 90 * 60_000), // sementara; diganti hasil Odoo
        party_size: input.party_size,
      });
    });
    // Balapan dua permintaan dengan key sama: yang kalah membaca baris pemenang.
    const target = row ?? (await this.repo.byKey(memberId, key));
    if (!target) throw Errors.serviceUnavailable('booking_unavailable');
    if (target.request_sha256 !== sha) throw BookingErrors.idempotencyConflict();
    return this.submit(target, input);
  }

  private async submit(row: BookingRow, input: CreateBookingInput): Promise<BookingView> {
    let res: OdooBookingResult;
    try {
      res = await this.odoo.create(row.company_code as FnbCompanyCode, row.outlet_code, {
        idempotency_key: `booking:create:${row.id}`,
        platform_booking_ref: row.id,
        start_at: input.start_at,
        party_size: input.party_size,
        ...(input.guest_label ? { guest_label: input.guest_label } : {}),
        ...(input.note ? { note: input.note } : {}),
      });
    } catch (e) {
      if (e instanceof OdooBookingError && e.kind === 'business') {
        await this.repo.update(row.id, { status: 'failed' });
        this.metrics.transition('failed');
      }
      throw this.mapOdoo(e);
    }
    if (res.platform_booking_ref !== row.id) throw Errors.serviceUnavailable('booking_unavailable');
    const patch = {
      status: res.state as BookingStatus,
      odoo_booking_ref: res.booking_ref,
      table_label: res.table_label,
      start_at: new Date(res.start_at),
      end_at: new Date(res.end_at),
      last_seq: res.seq,
    };
    await this.repo.update(row.id, patch);
    this.metrics.transition(res.state);
    await this.invalidateAvailability(row.outlet_code, res.start_at);
    return toView({ ...row, ...patch }, this.now());
  }

  // ───────────── baca ─────────────
  async list(memberId: string): Promise<{ items: BookingView[] }> {
    const rows = await this.repo.ofMember(memberId, LIST_LIMIT);
    return { items: rows.map((r) => toView(r, this.now())) };
  }

  async get(memberId: string, id: string): Promise<BookingView> {
    const row = await this.repo.byId(id);
    if (!row || row.member_id !== memberId || row.status === 'failed') throw Errors.notFound();
    return toView(row, this.now());
  }

  // ───────────── batal ─────────────
  async cancel(memberId: string, id: string): Promise<{ status: 'cancelled' }> {
    const row = await this.repo.byId(id);
    if (!row || row.member_id !== memberId || row.status === 'failed') throw Errors.notFound();
    if (row.status === 'cancelled') return { status: 'cancelled' };
    if (!toView(row, this.now()).cancellable) throw BookingErrors.notCancellable();
    try {
      const res = await this.odoo.cancel(row.company_code as FnbCompanyCode, row.outlet_code, row.id, `booking:cancel:${row.id}`);
      if (res.state !== 'cancelled') throw BookingErrors.notCancellable();
      await this.repo.tx(async (r) => {
        const locked = await r.byId(row.id, true);
        if (!locked || locked.status === 'cancelled') return;
        await r.update(row.id, { status: 'cancelled', last_seq: Math.max(locked.last_seq, res.seq) });
      });
    } catch (e) {
      if (e instanceof OdooBookingError && e.kind === 'business' && (e.code === 'invalid_state' || e.code === 'not_found')) {
        throw BookingErrors.notCancellable();
      }
      throw this.mapOdoo(e);
    }
    this.metrics.transition('cancelled');
    await this.invalidateAvailability(row.outlet_code, row.start_at.toISOString());
    return { status: 'cancelled' };
  }

  // ───────────── event Odoo ─────────────
  async handleEvent(ev: BookingEventInput): Promise<{ ok: true; status: BookingEventOutcome }> {
    const status = await this.repo.tx(async (r): Promise<BookingEventOutcome> => {
      const row = await r.byId(ev.platform_booking_ref, true);
      if (!row || row.company_code !== ev.company_code || row.outlet_code !== ev.outlet_code) return 'unknown';
      if (!(await r.claim(ev.idempotency_key))) return 'duplicate';
      const next = ev.state as BookingStatus;
      if (ev.seq <= row.last_seq || STATUS_RANK[next] < STATUS_RANK[row.status]) return 'stale';
      await r.update(row.id, { status: next, last_seq: ev.seq });
      if ((ev.event === 'confirmed' || ev.event === 'cancelled') && row.member_id) {
        const hash = await r.memberHashOf(row.member_id);
        if (hash) {
          const template = ev.event === 'confirmed' ? 'booking_confirmed' : 'booking_cancelled';
          const key = `notify:${template}:${row.id}`;
          await r.enqueue(
            QUEUES.NOTIFY_PUSH,
            { schema_version: 2, idempotency_key: key, member_hash: hash, source: 'table_booking', source_ref: row.id, template, params: {} },
            key,
          );
        }
      }
      return 'applied';
    });
    if (status === 'applied') this.metrics.transition(ev.state);
    if (status === 'unknown') this.logger.warn(`Event booking untuk ref tidak dikenal diabaikan (${ev.company_code}/${ev.event})`);
    return { ok: true, status };
  }

  // ───────────── job: rekonsiliasi + retensi ─────────────
  async jobOnce(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      const now = this.now();
      const rows = await this.repo.staleActive(new Date(now + 24 * 3600_000), RECONCILE_BATCH);
      const groups = new Map<string, BookingRow[]>();
      for (const r of rows) {
        const k = `${r.company_code}|${r.outlet_code}`;
        groups.set(k, [...(groups.get(k) ?? []), r]);
      }
      for (const [k, list] of groups) {
        const [company, outlet] = k.split('|');
        try {
          const states = await this.odoo.status(company as FnbCompanyCode, outlet, list.map((r) => r.id));
          const byRef = new Map(states.map((s) => [s.platform_booking_ref, s]));
          for (const r of list) {
            const s = byRef.get(r.id);
            if (!s) {
              // Odoo tidak mengenal baris `pending` > 10 menit → create tidak pernah sampai → gagal.
              if (r.status === 'pending' && now - r.created_at.getTime() > 10 * 60_000) await this.repo.update(r.id, { status: 'failed' });
              continue;
            }
            if (s.seq >= r.last_seq && STATUS_RANK[s.state] >= STATUS_RANK[r.status] && s.state !== r.status) {
              await this.repo.update(r.id, { status: s.state, last_seq: s.seq, odoo_booking_ref: s.booking_ref, table_label: s.table_label });
            }
          }
        } catch {
          this.logger.warn(`Rekonsiliasi booking ${k} gagal; dicoba lagi siklus berikut`);
        }
      }
      await this.repo.unlinkMembers(new Date(now - this.config.bookingMemberLinkDays * 86_400_000));
      await this.repo.purge(new Date(now - this.config.bookingRetentionDays * 86_400_000));
    } catch (err) {
      this.logger.error(`EFN_BOOKING_JOB ${JSON.stringify({ status: 'error', error: err instanceof Error ? err.name : 'error' })}`);
    } finally {
      this.running = false;
    }
  }

  private async invalidateAvailability(outlet: string, startIso: string): Promise<void> {
    // Kunci cache per (outlet, tanggal lokal WIB, party); hapus party 1..12 (cukup untuk max_party). Gagal = abaikan (TTL 30 s).
    const date = new Date(Date.parse(startIso) + 7 * 3600_000).toISOString().slice(0, 10);
    await Promise.all(Array.from({ length: 12 }, (_, i) => this.kv.del(`booking:avail:${outlet}:${date}:${i + 1}`).catch(() => 0)));
  }

  mapOdoo(e: unknown): Error {
    if (!(e instanceof OdooBookingError)) return e instanceof Error && 'getStatus' in e ? e : Errors.serviceUnavailable('booking_unavailable');
    if (e.kind !== 'business') return Errors.serviceUnavailable('booking_unavailable');
    switch (e.code) {
      case 'slot_unavailable':
        return BookingErrors.slotUnavailable();
      case 'outside_hours':
        return BookingErrors.outsideHours();
      case 'too_late':
        return BookingErrors.tooLate();
      case 'too_far':
        return BookingErrors.tooFar();
      case 'party_size':
        return BookingErrors.partySize();
      case 'idempotency_conflict':
        return BookingErrors.idempotencyConflict();
      case 'invalid_state':
        return BookingErrors.notCancellable();
      case 'invalid_input':
        return Errors.invalidRequest();
      default:
        return BookingErrors.outletUnavailable();
    }
  }
}
