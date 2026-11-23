/**
 * Tracking perilaku (G50; docs/04 §28.6, ADR-047, Proposed). MODUL MURNI (server + client, diuji unit).
 *
 * - Teks & versi consent `tracking` = DRAFT, MENUNGGU DPO/Legal (Q-SM16/17/26). Mengubah `body` = versi baru.
 *   BFF mencatat `text_sha256` dari env teks aktif; versi/hash di sini HARUS sama dengan env BFF (lihat laporan).
 * - Device ID = UUID v4 acak first-party. V-01 (23 Nov 2026): bila `features.visit_log`, dibuat saat kunjungan pertama TANPA
 *   consent untuk log anonim (tanpa penautan akun/segmen); ID consent baru selalu dibuat saat pengguna setuju (ADR-047 §3.1 no. 3).
 * - Payload event mengikuti allowlist kontrak (`additionalProperties: false`): tidak ada IP/UA/GPS/PII.
 *   `query` pencarian dikirim apa adanya (server yang meredaksi, §28.6).
 * - Tanpa console.log apa pun di jalur ini (UU PDP).
 */
import { isChannelCode } from './catalog';

export const TRACKING_TEXT = {
  purpose: 'tracking',
  /** Format sama dengan purpose lain (`<purpose>@<tanggal>`); cocok regex BFF `[A-Za-z0-9._@-]{1,32}`. */
  version: 'tracking@2026-11-20',
  title: 'Pelacakan aktivitas di aplikasi dan situs',
  /** DRAFT, menunggu DPO. */
  body:
    'Saya mengizinkan EFN mencatat aktivitas saya di situs dan aplikasi (produk yang dilihat, wishlist, isi keranjang, ' +
    'dan kata pencarian) memakai kode acak perangkat, untuk memahami minat saya dan menyusun penawaran yang lebih relevan. ' +
    'Kode ini tidak memuat nama atau nomor HP. Saya dapat menarik persetujuan kapan saja.',
  whenOff: 'Anda tetap dapat berbelanja seperti biasa; aktivitas Anda tidak dicatat.',
} as const;

/**
 * Log kunjungan anonim (V-01, keputusan pemilik proyek 23 Nov 2026; kontrak BFF `features.visit_log`). BUKAN teks consent:
 * tidak ikut versi/hash `TRACKING_TEXT` (mengubahnya tidak membuat versi consent baru). Menunggu tinjauan DPO/Legal.
 */
export const VISIT_LOG_TEXT = {
  notice:
    'Kunjungan Anda (halaman yang dibuka, produk yang dilihat, kata pencarian, isi keranjang) dicatat secara anonim memakai kode ' +
    'acak perangkat, untuk memahami cara pengunjung memakai situs. Kami tidak mencatat alamat IP atau jenis peramban, dan catatan ' +
    'anonim ini tidak dikaitkan dengan akun maupun dipakai untuk promo personal.',
  consentHint: 'Bila Anda setuju, kami juga memakai aktivitas Anda untuk rekomendasi dan promo personal. Menolak tidak menghentikan pencatatan anonim.',
  whenOff:
    'Anda tetap dapat berbelanja seperti biasa. Kunjungan tetap dicatat anonim, tanpa kaitan ke akun dan tanpa promo personal.',
} as const;

export const TRACKING_VERSION_RE = /^[A-Za-z0-9._@-]{1,32}$/;

// ---------- identitas ----------

const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isUuidV4(v: unknown): v is string {
  return typeof v === 'string' && UUID_V4_RE.test(v);
}

// ---------- pilihan consent perangkat (disimpan di cookie/localStorage) ----------

export interface DeviceChoice {
  granted: boolean;
  version: string;
}

/** Bentuk simpan: `1.<versi>` / `0.<versi>`. Nilai asing -> null (dianggap belum memilih). */
export function serializeChoice(c: DeviceChoice): string {
  return `${c.granted ? '1' : '0'}.${c.version}`;
}

export function parseChoice(raw: unknown): DeviceChoice | null {
  if (typeof raw !== 'string' || raw.length > 64) return null;
  const m = /^([01])\.(.+)$/.exec(raw);
  if (!m || !TRACKING_VERSION_RE.test(m[2]!)) return null;
  return { granted: m[1] === '1', version: m[2]! };
}

/** Banner tampil bila belum memilih atau pilihan lama atas versi teks yang berbeda. */
export function needsDecision(choice: DeviceChoice | null, currentVersion: string = TRACKING_TEXT.version): boolean {
  return choice === null || choice.version !== currentVersion;
}

/** Event boleh dikirim hanya bila pilihan = setuju atas versi teks terbaru dan ID perangkat ada. */
export function trackingActive(choice: DeviceChoice | null, deviceId: string | null): boolean {
  return choice !== null && choice.granted && choice.version === TRACKING_TEXT.version && isUuidV4(deviceId);
}

/** Ambil record purpose `tracking` dari GET /v1/me/consents (array atau `{ items }`); hanya boolean `true` = aktif. */
export function pickTracking(data: unknown): { granted: boolean; version: string | null; updated_at: string | null } {
  const list: unknown[] = Array.isArray(data)
    ? data
    : data && typeof data === 'object' && Array.isArray((data as { items?: unknown }).items)
      ? (data as { items: unknown[] }).items
      : [];
  for (const it of list) {
    if (!it || typeof it !== 'object') continue;
    const r = it as Record<string, unknown>;
    if (r.purpose === 'tracking') {
      return {
        granted: r.granted === true,
        version: typeof r.version === 'string' ? r.version : null,
        updated_at: typeof r.updated_at === 'string' ? r.updated_at : null,
      };
    }
  }
  return { granted: false, version: null, updated_at: null };
}

// ---------- features dari GET /v1/config ----------

export interface Features {
  tracking: boolean;
  wishlist: boolean;
  restock: boolean;
  priceAlert: boolean;
  /** Kotak masuk & voucher kampanye (`features.campaign_inbox`, G50). */
  campaignInbox: boolean;
  /** Log kunjungan anonim tanpa consent (`features.visit_log`, V-01). Server hanya true bila tracking juga aktif. */
  visitLog: boolean;
  /** Kanal notifikasi yang boleh dipilih member untuk notify-me. */
  channels: { push: boolean; email: boolean; wa_unofficial: boolean };
}

export const NO_FEATURES: Features = {
  tracking: false,
  wishlist: false,
  restock: false,
  priceAlert: false,
  campaignInbox: false,
  visitLog: false,
  channels: { push: false, email: false, wa_unofficial: false },
};

function flag(o: Record<string, unknown>, ...keys: string[]): boolean | undefined {
  for (const k of keys) if (typeof o[k] === 'boolean') return o[k] as boolean;
  return undefined;
}

/**
 * ASUMSI bentuk: `{ features: { tracking, wishlist, restock_alert, price_alert, push?, email?, wa_unofficial? } }`.
 * Nama alternatif diterima (`restock`, `restock_alerts`, `price_alerts`). Hanya boolean `true` yang menyalakan fitur;
 * kunci hilang = mati. Khusus `push`: tersedia kecuali `false` eksplisit (kanal bawaan alert; email/WA harus `true`).
 */
export function parseFeatures(data: unknown): Features {
  const root = data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
  const f = root.features && typeof root.features === 'object' ? (root.features as Record<string, unknown>) : {};
  const notifyChannels = f.notify_channels && typeof f.notify_channels === 'object' ? (f.notify_channels as Record<string, unknown>) : {};
  return {
    tracking: flag(f, 'tracking') === true,
    wishlist: flag(f, 'wishlist') === true,
    restock: flag(f, 'restock_alert', 'restock_alerts', 'restock') === true,
    priceAlert: flag(f, 'price_alert', 'price_alerts') === true,
    campaignInbox: flag(f, 'campaign_inbox') === true,
    visitLog: flag(f, 'visit_log') === true,
    channels: {
      push: (flag(f, 'push') ?? flag(notifyChannels, 'push')) !== false,
      email: (flag(f, 'email') ?? flag(notifyChannels, 'email')) === true,
      wa_unofficial: (flag(f, 'wa_unofficial') ?? flag(notifyChannels, 'wa_unofficial')) === true,
    },
  };
}

/** Validasi bentuk TERNORMALISASI (respons `/api/config` milik kita, mis. dari cache sessionStorage). Rusak -> semua mati. */
export function coerceFeatures(data: unknown): Features {
  const o = data && typeof data === 'object' ? (data as Record<string, unknown>) : null;
  const ch = o && o.channels && typeof o.channels === 'object' ? (o.channels as Record<string, unknown>) : {};
  if (!o) return NO_FEATURES;
  return {
    tracking: o.tracking === true,
    wishlist: o.wishlist === true,
    restock: o.restock === true,
    priceAlert: o.priceAlert === true,
    campaignInbox: o.campaignInbox === true,
    visitLog: o.visitLog === true,
    channels: { push: ch.push === true, email: ch.email === true, wa_unofficial: ch.wa_unofficial === true },
  };
}

// ---------- event ----------

export const EVENT_TYPES = [
  'product_view',
  'wishlist_add',
  'wishlist_remove',
  'cart_add',
  'cart_remove',
  'checkout_start',
  'search',
  'notify_me_request',
  'price_alert_set',
  'page_view',
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

/** Fitur member: tidak pernah dicatat anonim (kontrak V-01; BFF menolaknya `invalid`), hanya bila consent aktif. */
export const MEMBER_ONLY_EVENT_TYPES: readonly EventType[] = ['wishlist_add', 'wishlist_remove', 'notify_me_request', 'price_alert_set'];

export function isMemberOnlyEvent(type: EventType): boolean {
  return MEMBER_ONLY_EVENT_TYPES.includes(type);
}

/** Enum `page` kontrak V-01. Hanya nilai ini yang boleh dikirim; URL/path/query mentah TIDAK PERNAH. */
export const PAGES = ['home', 'category', 'product', 'search', 'cart', 'checkout', 'promo', 'store', 'account', 'order', 'other'] as const;
export type Page = (typeof PAGES)[number];

/**
 * Pemetaan route -> enum `page`. Hanya segmen pertama/kedua path yang dibaca; hasilnya selalu anggota `PAGES`.
 * `hasSearchQuery`: halaman daftar `/belanja` dengan kata pencarian = `search`, selain itu `category`.
 *   /                      -> home          /keranjang            -> cart
 *   /belanja               -> category|search  /pesanan, /pesanan/x -> order
 *   /belanja/[id]          -> product       /akun*, /masuk, /keluar, /inbox, /wishlist -> account
 *   /meja*, /reservasi*    -> store         /promo*               -> promo
 *   /checkout*             -> checkout (cadangan; checkout saat ini di dalam /keranjang)   lainnya -> other
 */
export function pageForPath(pathname: string, hasSearchQuery = false): Page {
  const segs = (typeof pathname === 'string' ? pathname : '').split('?')[0]!.split('/').filter(Boolean);
  const first = segs[0]?.toLowerCase();
  switch (first) {
    case undefined:
      return 'home';
    case 'belanja':
      return segs.length >= 2 ? 'product' : hasSearchQuery ? 'search' : 'category';
    case 'keranjang':
      return 'cart';
    case 'checkout':
      return 'checkout';
    case 'promo':
      return 'promo';
    case 'pesanan':
      return 'order';
    case 'akun':
    case 'masuk':
    case 'keluar':
    case 'inbox':
    case 'wishlist':
      return 'account';
    case 'meja':
    case 'reservasi':
      return 'store';
    default:
      return 'other';
  }
}

/**
 * Pilih `channel_code` untuk `page_view`: `?channel=` bila bentuknya valid (kode kanal, <= 8 karakter sesuai kontrak),
 * selain itu kode kanal brand HANYA bila brand punya tepat satu kanal (`brand_code` != kode kanal untuk brand multi-kanal,
 * mis. Bacha). Selain itu `null` (event tidak dikirim).
 */
export function pickEventChannel(requested: string | null | undefined, brandChannels: readonly string[]): string | null {
  const ok = (v: unknown): v is string => isChannelCode(v) && v.length <= 8;
  if (ok(requested)) return requested;
  const only = brandChannels.length === 1 ? brandChannels[0] : undefined;
  return ok(only) ? only : null;
}

/** Mode respons `POST /v1/events` (V-01): `consented` | `anonymous`; nilai lain/hilang -> null. */
export function parseMode(data: unknown): 'consented' | 'anonymous' | null {
  const m = data && typeof data === 'object' ? (data as Record<string, unknown>).mode : undefined;
  return m === 'consented' || m === 'anonymous' ? m : null;
}

/** Pertahanan: klien mengirim anonim tetapi server memperlakukan sebagai consent -> ID anonim harus diganti. */
export function anonymousModeLeaked(sent: 'consented' | 'anonymous', received: 'consented' | 'anonymous' | null): boolean {
  return sent === 'anonymous' && received === 'consented';
}

export function isPage(v: unknown): v is Page {
  return typeof v === 'string' && (PAGES as readonly string[]).includes(v);
}

export const MAX_BATCH_EVENTS = 50;
export const MAX_BODY_BYTES = 32 * 1024;
export const SCHEMA_VERSION = 1;

export interface TrackedEvent {
  event_id: string;
  type: EventType;
  client_ts: string;
  channel_code: string;
  outlet_code: string | null;
  product_id?: number;
  duration_s?: number;
  qty?: number;
  query?: string;
  result_count?: number;
  /** Hanya `page_view`. */
  page?: Page;
}

export interface EventInput {
  type: EventType;
  channelCode: string;
  outletCode?: string | null;
  productId?: number;
  durationS?: number;
  qty?: number;
  query?: string;
  resultCount?: number;
  page?: Page;
}

const ALLOWED_EVENT_KEYS: readonly string[] = [
  'event_id',
  'type',
  'client_ts',
  'channel_code',
  'outlet_code',
  'product_id',
  'duration_s',
  'qty',
  'query',
  'result_count',
  'page',
];

function posInt(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isSafeInteger(v) && v > 0 ? v : undefined;
}
function nonNegInt(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 ? v : undefined;
}

/**
 * Bangun event sesuai kontrak; `null` bila tidak valid (event dibuang, tidak dipotong menjadi data yang salah).
 * `channel_code` ≤ 8 dan `outlet_code` ≤ 16 menurut §28.6; outlet terlalu panjang -> null (event tetap sah).
 */
export function buildEvent(input: EventInput, eventId: string, now: Date): TrackedEvent | null {
  if (!EVENT_TYPES.includes(input.type) || !isUuidV4(eventId)) return null;
  if (typeof input.channelCode !== 'string' || input.channelCode.length < 1 || input.channelCode.length > 8) return null;
  const outlet = typeof input.outletCode === 'string' && input.outletCode.length >= 1 && input.outletCode.length <= 16 ? input.outletCode : null;
  const ev: TrackedEvent = {
    event_id: eventId,
    type: input.type,
    client_ts: now.toISOString(),
    channel_code: input.channelCode,
    outlet_code: outlet,
  };
  if (input.type === 'page_view') {
    if (!isPage(input.page)) return null;
    ev.page = input.page; // product_id sengaja tidak dikirim: `product_view` tetap event utama produk
    return ev;
  }
  if (input.page !== undefined) return null; // `page` dilarang untuk tipe lain
  const needsProduct = input.type !== 'checkout_start' && input.type !== 'search';
  if (needsProduct) {
    const pid = posInt(input.productId);
    if (pid === undefined) return null;
    ev.product_id = pid;
  }
  if (input.type === 'product_view') {
    ev.duration_s = Math.min(600, Math.max(0, Math.round(nonNegInt(Math.round(input.durationS ?? 0)) ?? 0)));
  }
  if (input.type === 'cart_add' || input.type === 'cart_remove') {
    const q = posInt(input.qty);
    if (q === undefined) return null;
    ev.qty = q;
  }
  if (input.type === 'search') {
    const query = typeof input.query === 'string' ? input.query.trim().slice(0, 64) : '';
    if (!query) return null;
    ev.query = query; // apa adanya; NFKC + redaksi dilakukan server
    const rc = nonNegInt(input.resultCount);
    if (rc !== undefined) ev.result_count = rc;
  }
  return ev;
}

/** Validasi ulang event dari browser di Route Handler: hanya kunci allowlist, tipe & batas sesuai kontrak. */
export function sanitizeEvents(raw: unknown): TrackedEvent[] | null {
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > MAX_BATCH_EVENTS) return null;
  const out: TrackedEvent[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return null;
    const o = item as Record<string, unknown>;
    if (Object.keys(o).some((k) => !ALLOWED_EVENT_KEYS.includes(k))) return null;
    if (typeof o.client_ts !== 'string' || Number.isNaN(Date.parse(o.client_ts))) return null;
    const ev = buildEvent(
      {
        type: o.type as EventType,
        channelCode: o.channel_code as string,
        outletCode: (o.outlet_code ?? null) as string | null,
        productId: o.product_id as number | undefined,
        durationS: o.duration_s as number | undefined,
        qty: o.qty as number | undefined,
        query: o.query as string | undefined,
        resultCount: o.result_count as number | undefined,
        page: o.page as Page | undefined,
      },
      o.event_id as string,
      new Date(o.client_ts),
    );
    if (!ev) return null;
    out.push(ev);
  }
  return out;
}

export function buildBatchBody(deviceId: string, events: TrackedEvent[], channel: 'web' | 'app' = 'web'): string {
  return JSON.stringify({ schema_version: SCHEMA_VERSION, device_id: deviceId, channel, events });
}

export function byteLength(s: string): number {
  return new TextEncoder().encode(s).byteLength;
}

// ---------- respons ----------

export interface BatchOutcome {
  disabled: boolean;
  accepted: number;
}

/** 202 `{ accepted, duplicates, rejected[], disabled? }`. Bentuk asing -> aman (tidak disabled). */
export function parseBatchResponse(data: unknown): BatchOutcome {
  const o = data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
  return {
    disabled: o.disabled === true,
    accepted: typeof o.accepted === 'number' && Number.isFinite(o.accepted) ? o.accepted : 0,
  };
}

/** `Retry-After` dalam detik, dibatasi 1..900 (15 menit). Tanpa/invalid -> 60. */
export function clampRetryAfter(seconds: number | undefined | null): number {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return 60;
  return Math.min(900, Math.max(1, Math.floor(seconds)));
}

// ---------- antrean + pengirim ----------

export const MAX_QUEUE = 200;

export class EventQueue {
  private items: TrackedEvent[] = [];
  constructor(private readonly max: number = MAX_QUEUE) {}

  size(): number {
    return this.items.length;
  }

  push(e: TrackedEvent): void {
    this.items.push(e);
    if (this.items.length > this.max) this.items.splice(0, this.items.length - this.max); // buang yang terlama
  }

  /** Ambil ≤ 50 event dan pastikan body ≤ 32 KB (kurangi bila perlu). */
  take(deviceId: string, max: number = MAX_BATCH_EVENTS): TrackedEvent[] {
    let n = Math.min(max, MAX_BATCH_EVENTS, this.items.length);
    while (n > 1 && byteLength(buildBatchBody(deviceId, this.items.slice(0, n))) > MAX_BODY_BYTES - 512) n = Math.floor(n / 2);
    return this.items.splice(0, n);
  }

  /** Kembalikan ke depan (urutan terjaga) setelah gagal kirim. */
  requeue(events: TrackedEvent[]): void {
    this.items.unshift(...events);
    if (this.items.length > this.max) this.items.length = this.max;
  }

  clear(): void {
    this.items = [];
  }
}

export type SendResult =
  | { kind: 'ok'; disabled: boolean }
  | { kind: 'rate_limited'; retryAfterS?: number }
  | { kind: 'consent_required' }
  | { kind: 'rejected' } // 400/413/422: batch tidak akan pernah diterima -> dibuang
  | { kind: 'retry' }; // jaringan/5xx

export interface SenderDeps {
  getDeviceId: () => string | null;
  send: (body: string) => Promise<SendResult>;
  now: () => number;
}

/**
 * Mesin status pengiriman: hormati 429 `Retry-After`, berhenti permanen bila `disabled`, berhenti (kosongkan antrean) bila
 * `tracking_consent_required` sampai `resume()`, backoff eksponensial untuk gangguan jaringan (3 kali lalu batch dibuang).
 */
export class TrackingSender {
  readonly queue = new EventQueue();
  private pausedUntil = 0;
  private stopped = false;
  private blocked = false;
  private failures = 0;
  private inFlight = false;

  constructor(private readonly deps: SenderDeps) {}

  isStopped(): boolean {
    return this.stopped || this.blocked;
  }

  enqueue(e: TrackedEvent | null): void {
    if (!e || this.isStopped()) return;
    this.queue.push(e);
  }

  /** Dipanggil saat consent berubah (mis. setelah consent akun aktif). */
  resume(): void {
    this.blocked = false;
    this.failures = 0;
    this.pausedUntil = 0;
  }

  /** Consent dicabut: kosongkan antrean dan jangan kirim apa pun. */
  reset(): void {
    this.queue.clear();
    this.resume();
  }

  /** Kirim satu batch. Mengembalikan jumlah event yang dilepas dari antrean (terkirim atau dibuang). */
  async flush(): Promise<number> {
    if (this.inFlight || this.isStopped() || this.queue.size() === 0) return 0;
    if (this.deps.now() < this.pausedUntil) return 0;
    const deviceId = this.deps.getDeviceId();
    if (!isUuidV4(deviceId)) {
      this.queue.clear();
      return 0;
    }
    const batch = this.queue.take(deviceId);
    if (batch.length === 0) return 0;
    this.inFlight = true;
    let result: SendResult;
    try {
      result = await this.deps.send(buildBatchBody(deviceId, batch));
    } catch {
      result = { kind: 'retry' };
    } finally {
      this.inFlight = false;
    }
    switch (result.kind) {
      case 'ok':
        this.failures = 0;
        if (result.disabled) {
          this.stopped = true;
          this.queue.clear();
        }
        return batch.length;
      case 'rate_limited':
        this.queue.requeue(batch);
        this.pausedUntil = this.deps.now() + clampRetryAfter(result.retryAfterS) * 1000;
        return 0;
      case 'consent_required':
        this.blocked = true;
        this.queue.clear();
        return batch.length;
      case 'rejected':
        return batch.length;
      case 'retry':
      default:
        this.failures += 1;
        if (this.failures >= 3) {
          this.failures = 0;
          return batch.length; // batch dibuang
        }
        this.queue.requeue(batch);
        this.pausedUntil = this.deps.now() + Math.min(300, 5 * 2 ** this.failures) * 1000;
        return 0;
    }
  }

  /** Untuk `sendBeacon` saat halaman ditutup: ambil batch tanpa menunggu respons. Hormati jeda & status berhenti. */
  takeForBeacon(): string | null {
    if (this.isStopped() || this.queue.size() === 0 || this.deps.now() < this.pausedUntil) return null;
    const deviceId = this.deps.getDeviceId();
    if (!isUuidV4(deviceId)) return null;
    const batch = this.queue.take(deviceId);
    if (batch.length === 0) return null;
    this.beaconBatch = batch;
    return buildBatchBody(deviceId, batch);
  }

  private beaconBatch: TrackedEvent[] = [];

  /** Beacon ditolak browser (mengembalikan false) -> kembalikan batch ke antrean. */
  beaconFailed(): void {
    this.queue.requeue(this.beaconBatch);
    this.beaconBatch = [];
  }
}

// ---------- durasi lihat produk ----------

/** Akumulator waktu terlihat (detik) yang berhenti saat tab tersembunyi. */
export class VisibleTimer {
  private acc = 0;
  private since: number | null = null;
  constructor(private readonly now: () => number, visible: boolean) {
    if (visible) this.since = now();
  }
  setVisible(v: boolean): void {
    if (v && this.since === null) this.since = this.now();
    if (!v && this.since !== null) {
      this.acc += this.now() - this.since;
      this.since = null;
    }
  }
  seconds(): number {
    const live = this.since !== null ? this.now() - this.since : 0;
    return Math.min(600, Math.max(0, Math.round((this.acc + live) / 1000)));
  }
}
