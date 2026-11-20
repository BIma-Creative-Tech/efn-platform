/**
 * Klien tracking di BROWSER (G50; docs/04 §28.6, ADR-047 Proposed). Tanpa token/secret; semua panggilan ke Route Handler
 * origin sendiri (`/api/events`, `/api/events/consent`, `/api/me/tracking-consent`).
 *
 * - Pilihan consent perangkat disimpan di cookie first-party `efn_tc` (+ cermin localStorage). ID perangkat (UUID v4 acak)
 *   di cookie `efn_did` (+ cermin localStorage), HANYA dibuat/disimpan setelah BFF mencatat persetujuan.
 * - Menolak -> tidak ada ID, tidak ada panggilan server (kontrak `POST /v1/events/consent` butuh `device_id`).
 * - Semua akses storage dibungkus try/catch (mode privat/diblokir). Tanpa console.* (UU PDP).
 * - Akun login: event dikirim dengan `?link=1` hanya bila consent akun `tracking` aktif (tombol eksplisit member).
 */
import {
  buildEvent,
  isUuidV4,
  parseChoice,
  serializeChoice,
  TRACKING_TEXT,
  trackingActive,
  TrackingSender,
  type DeviceChoice,
  type EventInput,
  type SendResult,
} from './tracking';

const DID_KEY = 'efn_did';
const CHOICE_KEY = 'efn_tc';
const DID_MAX_AGE = 180 * 24 * 3600; // = retensi device_link (ADR-047 §3.2)
const CHOICE_MAX_AGE = 365 * 24 * 3600;
const FLUSH_INTERVAL_MS = 10_000;

// ---------- storage ----------

function cookieGet(name: string): string | null {
  try {
    for (const part of document.cookie.split('; ')) {
      const i = part.indexOf('=');
      if (i > 0 && part.slice(0, i) === name) return decodeURIComponent(part.slice(i + 1));
    }
  } catch {
    // diabaikan
  }
  return null;
}

function cookieSet(name: string, value: string, maxAge: number): void {
  try {
    const secure = window.location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=${maxAge}; SameSite=Lax${secure}`;
  } catch {
    // diabaikan
  }
}

function storeGet(key: string): string | null {
  const c = cookieGet(key);
  if (c) return c;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storeSet(key: string, value: string, maxAge: number): void {
  cookieSet(key, value, maxAge);
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // diabaikan
  }
}

function storeDel(key: string): void {
  cookieSet(key, '', 0);
  try {
    window.localStorage.removeItem(key);
  } catch {
    // diabaikan
  }
}

// ---------- status (dapat diamati React lewat useSyncExternalStore) ----------

const listeners = new Set<() => void>();
function notify(): void {
  for (const l of listeners) l();
}
export function subscribeTracking(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/** Snapshot primitif (stabil) untuk useSyncExternalStore: nilai cookie pilihan, atau '' bila belum memilih. */
export function choiceSnapshot(): string {
  return storeGet(CHOICE_KEY) ?? '';
}

export function readChoice(): DeviceChoice | null {
  return parseChoice(storeGet(CHOICE_KEY));
}

export function readDeviceId(): string | null {
  const v = storeGet(DID_KEY);
  return isUuidV4(v) ? v : null;
}

/** UUID v4 acak; `null` bila browser tidak punya CSPRNG (tracking dimatikan, bukan diganti Math.random). */
export function newUuid(): string | null {
  try {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    if (c && typeof c.getRandomValues === 'function') {
      const b = c.getRandomValues(new Uint8Array(16));
      b[6] = (b[6]! & 0x0f) | 0x40;
      b[8] = (b[8]! & 0x3f) | 0x80;
      const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
      return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
    }
  } catch {
    // diabaikan
  }
  return null;
}

export function isTrackingActive(): boolean {
  return trackingActive(readChoice(), readDeviceId());
}

// ---------- pengirim ----------

let linked = false;
let sender: TrackingSender | null = null;

async function sendBody(body: string): Promise<SendResult> {
  let res: Response;
  try {
    res = await fetch(`/api/events${linked ? '?link=1' : ''}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body,
      credentials: 'same-origin',
      cache: 'no-store',
      keepalive: body.length < 60_000,
    });
  } catch {
    return { kind: 'retry' };
  }
  if (res.status === 202 || res.status === 200) {
    const data = (await res.json().catch(() => null)) as { disabled?: unknown } | null;
    return { kind: 'ok', disabled: data?.disabled === true };
  }
  if (res.status === 429) {
    const ra = Number(res.headers.get('retry-after'));
    return { kind: 'rate_limited', ...(Number.isFinite(ra) && ra > 0 ? { retryAfterS: ra } : {}) };
  }
  if (res.status === 403) {
    const data = (await res.json().catch(() => null)) as { error?: { code?: unknown } } | null;
    return data?.error?.code === 'tracking_consent_required' ? { kind: 'consent_required' } : { kind: 'rejected' };
  }
  if (res.status >= 400 && res.status < 500) return { kind: 'rejected' };
  return { kind: 'retry' };
}

function getSender(): TrackingSender {
  if (!sender) {
    sender = new TrackingSender({
      getDeviceId: readDeviceId,
      send: sendBody,
      now: () => Date.now(),
    });
  }
  return sender;
}

/** Catat satu event (no-op bila tracking tidak aktif). Tidak pernah melempar. */
export function trackEvent(input: EventInput): void {
  try {
    if (!isTrackingActive()) return;
    const id = newUuid();
    if (!id) return;
    const s = getSender();
    s.enqueue(buildEvent(input, id, new Date()));
    if (s.queue.size() >= 50) void s.flush();
  } catch {
    // tracking tidak boleh mengganggu belanja
  }
}

export function flushNow(): Promise<number> {
  return getSender().flush();
}

/** Saat halaman tersembunyi/ditutup: `sendBeacon` (tanpa menunggu), jatuh ke fetch keepalive bila tidak tersedia. */
export function flushOnHide(): void {
  try {
    if (!isTrackingActive()) return;
    const s = getSender();
    const body = s.takeForBeacon();
    if (!body) return;
    const url = `/api/events${linked ? '?link=1' : ''}`;
    const ok = typeof navigator.sendBeacon === 'function' && navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }));
    if (!ok) s.beaconFailed();
  } catch {
    // diabaikan
  }
}

/** Setel status tautan akun (consent akun `tracking` aktif). Mengaktifkan kembali pengirim yang berhenti karena 403. */
export function setLinked(v: boolean): void {
  if (linked !== v) {
    linked = v;
    getSender().resume();
  }
}

/** Mulai flush periodik + `visibilitychange`/`pagehide`. Mengembalikan fungsi pembersih. */
export function startFlushing(): () => void {
  const timer = window.setInterval(() => {
    if (isTrackingActive()) void getSender().flush();
  }, FLUSH_INTERVAL_MS);
  const onVis = () => {
    if (document.visibilityState === 'hidden') flushOnHide();
  };
  document.addEventListener('visibilitychange', onVis);
  window.addEventListener('pagehide', flushOnHide);
  return () => {
    window.clearInterval(timer);
    document.removeEventListener('visibilitychange', onVis);
    window.removeEventListener('pagehide', flushOnHide);
  };
}

// ---------- aksi consent perangkat ----------

export type ConsentActionResult = { ok: true } | { ok: false; disabled?: boolean; message: string };

async function postDeviceConsent(deviceId: string, granted: boolean): Promise<{ ok: boolean; disabled: boolean; status: number }> {
  try {
    const res = await fetch('/api/events/consent', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ device_id: deviceId, granted, version: TRACKING_TEXT.version }),
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!res.ok) return { ok: false, disabled: false, status: res.status };
    const data = (await res.json().catch(() => null)) as { disabled?: unknown } | null;
    return { ok: true, disabled: data?.disabled === true, status: res.status };
  } catch {
    return { ok: false, disabled: false, status: 0 };
  }
}

const SAVE_ERROR = 'Pilihan belum tersimpan. Periksa koneksi lalu coba lagi.';

/** Setuju: server mencatat dulu; ID perangkat baru disimpan SETELAH berhasil. */
export async function grantDeviceConsent(): Promise<ConsentActionResult> {
  const id = readDeviceId() ?? newUuid();
  if (!id) return { ok: false, message: 'Peramban ini tidak mendukung pelacakan yang aman, jadi tetap dimatikan.' };
  const r = await postDeviceConsent(id, true);
  if (r.disabled) return { ok: false, disabled: true, message: 'Fitur ini sedang tidak aktif.' };
  if (!r.ok) return { ok: false, message: SAVE_ERROR };
  storeSet(DID_KEY, id, DID_MAX_AGE);
  storeSet(CHOICE_KEY, serializeChoice({ granted: true, version: TRACKING_TEXT.version }), CHOICE_MAX_AGE);
  getSender().reset();
  notify();
  return { ok: true };
}

/**
 * Tolak/tarik: pilihan lokal berlaku SEGERA (ID dihapus, antrean dikosongkan). Bila sebelumnya ada ID, penarikan dicatat
 * ke server (upaya terbaik; kegagalan server tidak membatalkan penarikan lokal). Tanpa ID -> tidak ada panggilan server.
 */
export async function declineDeviceConsent(): Promise<ConsentActionResult> {
  const id = readDeviceId();
  storeDel(DID_KEY);
  storeSet(CHOICE_KEY, serializeChoice({ granted: false, version: TRACKING_TEXT.version }), CHOICE_MAX_AGE);
  getSender().reset();
  notify();
  if (id) {
    const r = await postDeviceConsent(id, false);
    if (!r.ok && !r.disabled) {
      return { ok: false, message: 'Pelacakan sudah dimatikan di perangkat ini, tetapi pencatatan di server belum berhasil. Coba lagi nanti.' };
    }
  }
  return { ok: true };
}

/** Hapus pilihan (mis. fitur dimatikan di server): banner dapat tampil lagi bila fitur menyala kembali. */
export function forgetChoice(): void {
  storeDel(CHOICE_KEY);
  storeDel(DID_KEY);
  getSender().reset();
  notify();
}
