/**
 * Slot ambil pre-order (docs/04 §15.4, G23 W-36) — tipe + parser + helper MURNI (client, Route Handler, test).
 *
 * - Daftar slot & sisa kapasitas dihitung Odoo (lewat BFF `GET /v1/catalog/slots`); klien hanya memilih.
 * - `pickup_slot` disimpan di grup keranjang (ISO UTC, menit bulat). Kapasitas final diperiksa saat checkout
 *   (`409 slot_unavailable` -> klien meminta jadwal lain).
 * - Tanpa slot = "ambil secepatnya" (perilaku keranjang lama tidak berubah).
 */
import { isChannelCode, isOutletCode } from './catalog';

export const SLOT_ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z$/;
export const MAX_SLOTS_PER_DAY = 48;

export interface PickupSlot {
  start_at: string;
  end_at: string;
  remaining: number;
}

export interface SlotDay {
  outlet_code: string;
  date: string;
  timezone: string;
  slot_minutes: number;
  slots: PickupSlot[];
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

export function isSlotIso(v: unknown): v is string {
  return typeof v === 'string' && SLOT_ISO_RE.test(v) && !Number.isNaN(Date.parse(v));
}

/** Allowlist respons slot dari BFF. Bentuk rusak -> null; slot rusak dibuang satu per satu. */
export function parseSlotDay(v: unknown): SlotDay | null {
  if (!isObj(v) || !isOutletCode(v.outlet_code) || typeof v.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v.date)) return null;
  if (!Array.isArray(v.slots)) return null;
  const minutes = v.slot_minutes === 30 || v.slot_minutes === 60 ? v.slot_minutes : null;
  if (minutes === null) return null;
  const tz = typeof v.timezone === 'string' && /^[A-Za-z_]+(?:\/[A-Za-z0-9_+-]+){0,2}$/.test(v.timezone) ? v.timezone : 'Asia/Jakarta';
  const slots: PickupSlot[] = [];
  const seen = new Set<string>();
  for (const s of v.slots) {
    if (!isObj(s) || !isSlotIso(s.start_at) || !isSlotIso(s.end_at) || seen.has(s.start_at)) continue;
    const remaining = typeof s.remaining === 'number' && Number.isInteger(s.remaining) && s.remaining >= 0 ? s.remaining : null;
    if (remaining === null) continue;
    seen.add(s.start_at);
    slots.push({ start_at: s.start_at, end_at: s.end_at, remaining });
    if (slots.length >= MAX_SLOTS_PER_DAY) break;
  }
  slots.sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
  return { outlet_code: v.outlet_code, date: v.date, timezone: tz, slot_minutes: minutes, slots };
}

/** Query string BFF `channel, outlet, date`; tidak valid -> null. */
export function slotsQuery(channel: unknown, outlet: unknown, date: unknown): URLSearchParams | null {
  if (!isChannelCode(channel) || !isOutletCode(outlet) || typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  const d = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== date) return null;
  return new URLSearchParams({ channel, outlet, date });
}

/** "10.00–11.00" di zona outlet. */
export function slotRangeLabel(s: Pick<PickupSlot, 'start_at' | 'end_at'>, tz: string): string {
  const f = new Intl.DateTimeFormat('id-ID', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz });
  return `${f.format(Date.parse(s.start_at))}–${f.format(Date.parse(s.end_at))}`;
}

/** "Sabtu, 25 Okt · 10.00" (untuk ringkasan keranjang/pesanan). */
export function slotLongLabel(iso: string, tz = 'Asia/Jakarta'): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const d = new Intl.DateTimeFormat('id-ID', { weekday: 'long', day: 'numeric', month: 'short', timeZone: tz }).format(t);
  const h = new Intl.DateTimeFormat('id-ID', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz }).format(t);
  return `${d} · ${h}`;
}

/** Slot masih bisa dipilih (ada sisa kapasitas). */
export function slotSelectable(s: Pick<PickupSlot, 'remaining'>): boolean {
  return s.remaining > 0;
}

/** Label sisa kapasitas: tidak menampilkan angka besar (cukup "Tersedia"), hanya peringatan saat menipis. */
export function remainingLabel(remaining: number): string {
  if (remaining <= 0) return 'Penuh';
  if (remaining <= 3) return `Sisa ${remaining}`;
  return 'Tersedia';
}
