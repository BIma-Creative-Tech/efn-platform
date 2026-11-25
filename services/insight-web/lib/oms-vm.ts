/**
 * View-model penjualan level struk ESB OMS (G69; brand PB; kontrak docs/04 §33.5 dan §33.7).
 * Murni (tanpa I/O): kumulatif per jam, persen terjual pada jam patok, perkiraan jam habis, bauran kanal, label kesegaran,
 * dan porsi tak terpetakan. Semua fungsi menerima baris mentah dari ClickHouse (angka bisa berupa string) dan mengembalikan
 * `null` / daftar kosong bila tidak ada data, sehingga halaman dapat menyembunyikan bagian ini dengan rapi.
 */

/** Jam patok persen terjual: "terjual sebelum pukul HH:00" (bucket jam < HH). */
export const PATOK_HOURS = [12, 15, 18] as const;

const num = (v: unknown): number => {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
};

const validHour = (v: unknown): number | null => {
  // null / '' bukan jam 00 (Number(null) = Number('') = 0): baris tanpa jam diabaikan.
  if (v === null || v === undefined || (typeof v === 'string' && v.trim() === '')) return null;
  const n = Number(v);
  return Number.isInteger(n) && n >= 0 && n <= 23 ? n : null;
};

export const hourLabel = (h: number): string => `${String(h).padStart(2, '0')}:00`;
export const hourRangeLabel = (h: number): string => `${String(h).padStart(2, '0')}:00-${String(h).padStart(2, '0')}:59`;

// ---------- /fnb/store: penjualan per jam & kanal ----------

export interface HourlyRaw {
  hour: number | string;
  channel_name: string;
  bills_sum: number | string;
  pax_sum: number | string;
  gross_sum: number | string;
  net_sum: number | string;
  items_sum: number | string;
}

export interface ChannelShare {
  name: string;
  bills: number;
  net: number;
  /** Porsi penjualan bersih (0-100); null bila total bersih <= 0. */
  sharePct: number | null;
}

export interface StoreHourlyVm {
  /** Jam berurutan dari jam pertama sampai terakhir yang ada data (jam di tengah tanpa data = 0). */
  hours: number[];
  bills: number[];
  net: number[];
  channels: ChannelShare[];
  totals: { bills: number; pax: number; net: number; items: number; avgBillValue: number | null; itemsPerBill: number | null };
}

export const NO_CHANNEL = 'Tidak diketahui';

/** Null bila tidak ada baris sah atau tidak ada struk sama sekali (OMS belum aktif / tanpa data hari itu). */
export function buildStoreHourly(rows: readonly HourlyRaw[]): StoreHourlyVm | null {
  const byHour = new Map<number, { bills: number; net: number }>();
  const byChannel = new Map<string, { bills: number; net: number }>();
  const totals = { bills: 0, pax: 0, net: 0, items: 0 };
  for (const r of rows) {
    const h = validHour(r.hour);
    if (h === null) continue;
    const bills = num(r.bills_sum);
    const net = num(r.net_sum);
    const slot = byHour.get(h) ?? { bills: 0, net: 0 };
    slot.bills += bills;
    slot.net += net;
    byHour.set(h, slot);
    const name = (r.channel_name ?? '').trim() === '' ? NO_CHANNEL : r.channel_name.trim();
    const ch = byChannel.get(name) ?? { bills: 0, net: 0 };
    ch.bills += bills;
    ch.net += net;
    byChannel.set(name, ch);
    totals.bills += bills;
    totals.pax += num(r.pax_sum);
    totals.net += net;
    totals.items += num(r.items_sum);
  }
  if (byHour.size === 0 || totals.bills <= 0) return null;

  const keys = Array.from(byHour.keys());
  const lo = Math.min(...keys);
  const hi = Math.max(...keys);
  const hours: number[] = [];
  for (let h = lo; h <= hi; h++) hours.push(h);

  const channels = Array.from(byChannel, ([name, v]): ChannelShare => ({
    name,
    bills: v.bills,
    net: v.net,
    sharePct: totals.net > 0 ? (v.net / totals.net) * 100 : null,
  })).sort((a, b) => b.net - a.net || b.bills - a.bills || a.name.localeCompare(b.name));

  return {
    hours,
    bills: hours.map((h) => byHour.get(h)?.bills ?? 0),
    net: hours.map((h) => byHour.get(h)?.net ?? 0),
    channels,
    totals: {
      ...totals,
      avgBillValue: totals.bills > 0 ? totals.net / totals.bills : null,
      itemsPerBill: totals.bills > 0 ? totals.items / totals.bills : null,
    },
  };
}

// ---------- /flow: sell-through per jam ----------

export interface FinHourlyRaw {
  brand: string;
  /** Kode produk polos (= product_code mart Core); kunci gabungan dengan pasokan = `<brand>:<product_key>`. */
  product_key: string;
  product_label: string;
  hour: number | string;
  qty_sum: number | string;
  /** Satuan resep (view OMS). Kosong/tidak ada = dianggap EA (FIN selalu EA di pasokan Core). */
  uom?: string | null;
}

/** Satuan yang setara EA (pasokan Core dalam EA); huruf besar-kecil tidak dibedakan. */
export const isEaUom = (u: string | null | undefined): boolean => {
  const s = (u ?? '').trim().toUpperCase();
  return s === '' || s === 'EA' || s === 'PCS' || s === 'PC';
};

export interface SupplyRaw {
  /** `<brand>:<product_code>` (dirakit di query `foms_fin_supply`). */
  product_key: string;
  product_label: string;
  supply_qty: number | string;
  store_prod_qty?: number | string;
}

export interface SellThroughRow {
  key: string;
  label: string;
  /** Pasokan toko hari itu; null bila produk tidak ada di data arus barang. */
  supply: number | null;
  /** Total terjual (OMS) sepanjang hari. */
  sold: number;
  /** Terjual per jam (indeks 0-23). */
  perHour: number[];
  /** % terjual sebelum pukul HH:00 untuk tiap jam patok; null bila pasokan <= 0 / tidak ada. */
  pctAt: Record<number, number | null>;
  /** % terjual sampai tutup (seluruh hari). */
  pctClose: number | null;
  /** Jam pertama (bucket) saat kumulatif terjual >= pasokan; null bila tidak habis atau pasokan tidak diketahui. */
  soldOutHour: number | null;
  /** Satuan resep non-EA (mis. KG): persen dan jam habis tidak dihitung karena satuan beda dengan pasokan Core; null bila EA. */
  uomMismatch: string | null;
}

/** Jumlah terjual dengan bucket jam < `before`. */
export const soldBefore = (perHour: readonly number[], before: number): number => perHour.slice(0, Math.max(0, before)).reduce((s, v) => s + v, 0);

export function soldOutHour(perHour: readonly number[], supply: number | null): number | null {
  if (supply === null || !(supply > 0)) return null;
  let cum = 0;
  for (let h = 0; h < 24; h++) {
    cum += perHour[h] ?? 0;
    if (cum >= supply) return h;
  }
  return null;
}

const pctOfSupply = (sold: number, supply: number | null): number | null => (supply === null || !(supply > 0) ? null : (sold / supply) * 100);

/**
 * Gabungkan terjual per jam (OMS) dengan pasokan (mart Core) per produk FIN. Produk tanpa penjualan dan tanpa pasokan dibuang.
 * Urut: terjual terbanyak dulu; `limit` membatasi jumlah baris (bawaan 40). Kembali [] bila OMS tidak punya baris terjual.
 */
export function buildSellThrough(fin: readonly FinHourlyRaw[], supply: readonly SupplyRaw[], limit = 40): SellThroughRow[] {
  const perHour = new Map<string, number[]>();
  const labels = new Map<string, string>();
  const badUom = new Map<string, string>();
  for (const r of fin) {
    const h = validHour(r.hour);
    if (h === null || !r.product_key || !r.brand) continue;
    const key = `${r.brand}:${r.product_key}`;
    if (!isEaUom(r.uom) && !badUom.has(key)) badUom.set(key, (r.uom ?? '').trim());
    const arr = perHour.get(key) ?? new Array<number>(24).fill(0);
    arr[h] += num(r.qty_sum);
    perHour.set(key, arr);
    if (r.product_label) labels.set(key, r.product_label);
  }
  if (perHour.size === 0 || !Array.from(perHour.values()).some((a) => a.some((v) => v > 0))) return [];

  const sup = new Map<string, number>();
  for (const r of supply) {
    if (!r.product_key) continue;
    sup.set(r.product_key, (sup.get(r.product_key) ?? 0) + num(r.supply_qty));
    if (r.product_label && !labels.has(r.product_key)) labels.set(r.product_key, r.product_label);
  }

  const rows: SellThroughRow[] = [];
  for (const key of new Set([...perHour.keys(), ...sup.keys()])) {
    const hours = perHour.get(key) ?? new Array<number>(24).fill(0);
    const sold = hours.reduce((s, v) => s + v, 0);
    const mismatch = badUom.get(key) ?? null;
    // Satuan beda: pasokan tidak sebanding dengan terjual, jadi dianggap tidak diketahui (tanpa persen/jam habis).
    const s = mismatch === null && sup.has(key) ? (sup.get(key) as number) : null;
    if (sold <= 0 && !(s !== null && s > 0)) continue;
    const label = labels.get(key) ?? key;
    rows.push({
      key,
      uomMismatch: mismatch,
      label: label.startsWith('NAME:') ? label.slice(5) : label,
      supply: s,
      sold,
      perHour: hours,
      pctAt: Object.fromEntries(PATOK_HOURS.map((h) => [h, pctOfSupply(soldBefore(hours, h), s)])),
      pctClose: pctOfSupply(sold, s),
      soldOutHour: soldOutHour(hours, s),
    });
  }
  rows.sort((a, b) => b.sold - a.sold || a.label.localeCompare(b.label));
  return rows.slice(0, Math.max(0, limit));
}

// ---------- pilihan hari bagian OMS di /fnb/store ----------

export type OmsDayMode = 'today' | 'date';

/** Search param `oms_day`: hanya `today` atau `date` yang diterima; selain itu (termasuk array/aneh) = tidak ada. */
export function parseOmsDay(v: string | string[] | undefined): OmsDayMode | null {
  const s = Array.isArray(v) ? v[0] : v;
  return s === 'today' || s === 'date' ? s : null;
}

/**
 * Hari yang ditampilkan bagian OMS. "Hari ini (berjalan)" hanya tersedia bila ada tarikan hari ini (`freshLabel` terisi);
 * bawaan = hari ini bila tersedia, selain itu tanggal filter halaman. `today` dari server (WIB), bukan dari input.
 */
export function resolveOmsDay(p: { param: OmsDayMode | null; freshLabel: string | null; today: string; filterDay: string }): {
  mode: OmsDayMode;
  day: string;
  todayAvailable: boolean;
} {
  const todayAvailable = p.freshLabel !== null;
  const mode: OmsDayMode = todayAvailable && p.param !== 'date' ? 'today' : 'date';
  return { mode, day: mode === 'today' ? p.today : p.filterDay, todayAvailable };
}

// ---------- label kesegaran & kualitas pemetaan ----------

/** Baris `foms_freshness`: `max(last_finished_at)` di cabang terpilih/terlihat (null bila tidak ada baris hari ini). */
export interface FreshnessRaw {
  finished_at: string | null;
}

/** "HH:MM" (WIB, bagian jam dari 'YYYY-MM-DD HH:MM:SS') dari `finished_at` terbaru; null bila tidak ada yang dapat dibaca. */
export function freshnessLabel(rows: readonly FreshnessRaw[]): string | null {
  const stamps = rows
    .map((r) => r.finished_at)
    .filter((v): v is string => typeof v === 'string' && /\d{2}:\d{2}/.test(v))
    .sort();
  if (stamps.length === 0) return null;
  const m = /(\d{2}):(\d{2})(?::\d{2})?(?:\D|$)/.exec(stamps[stamps.length - 1].replace(/^\d{4}-\d{2}-\d{2}[T ]/, ''));
  return m ? `${m[1]}:${m[2]}` : null;
}

/** Baris `foms_fin_quality` (per cabang-hari, sudah terfilter cakupan). */
export interface QualityRaw {
  unmapped_qty_f: number | string;
  menu_qty_f: number | string;
}

/** Porsi qty menu tak terpetakan (0-100) = Σunmapped_qty / Σmenu_qty x 100; null bila tidak ada baris atau total menu 0. */
export function unmappedSharePct(rows: readonly QualityRaw[]): number | null {
  const un = rows.reduce((s, r) => s + num(r.unmapped_qty_f), 0);
  const tot = rows.reduce((s, r) => s + num(r.menu_qty_f), 0);
  return tot > 0 ? Math.min(100, Math.max(0, (un / tot) * 100)) : null;
}
