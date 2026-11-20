/**
 * Filter halaman supermarket dari URL, divalidasi di SERVER (docs/04 §28.13.11): `from`,`to`, `wh` (id warehouse ⊆ `wids`
 * sesi, tanpa 0, maks 50), `cat` (id kategori, maks 20; default = `cats` sesi bila tidak ada param). Nilai tidak sah ->
 * dibuang/diganti bawaan dan `adjusted = true` (pemberitahuan di UI), bukan galat. `pt` tidak dipakai (selalu codes ∩ SM_CODES).
 * Murni.
 */
import { addDays, diffDays, isIsoDate, isoWeekday, wibToday, wibYesterday } from './dates';
import type { RawParams } from './filters';

export const SM_MAX_WH = 50;
export const SM_MAX_CAT = 20;
const MAX_DAYS = 366;
const ID_RE = /^\d{1,10}$/;

export interface SmFilters {
  from: string;
  to: string;
  /** Pembanding: panjang sama, tepat sebelum `from`. */
  prev: { from: string; to: string };
  /** Tahun lalu: tanggal yang sama setahun sebelumnya (29 Feb -> 28 Feb). */
  yoy: { from: string; to: string };
  /** Outlet yang dipilih eksplisit (urut naik); kosong = semua outlet sesi. */
  wh: number[];
  /** Kategori terpilih; kosong = semua. */
  cat: number[];
  /** true bila `cat` berasal dari default `cats` sesi (bukan pilihan pengguna). */
  catDefaulted: boolean;
}

export interface SmFilterParse {
  filters: SmFilters;
  adjusted: boolean;
}

function all(raw: RawParams, key: string): string[] {
  if (raw instanceof URLSearchParams) return raw.getAll(key);
  const v = raw[key];
  if (v === undefined) return [];
  return Array.isArray(v) ? v : [v];
}

function csv(raw: RawParams, key: string): string[] {
  return all(raw, key)
    .flatMap((s) => s.split(','))
    .map((s) => s.trim())
    .filter((s) => s !== '');
}

/** Tanggal yang sama tahun lalu; 29 Februari -> 28 Februari. */
export function addYears(iso: string, n: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const ty = y + n;
  const dt = new Date(Date.UTC(ty, m - 1, d));
  // 29 Feb di tahun non-kabisat meluap ke Mar: ambil hari terakhir bulan itu (hari ke-0 bulan berikutnya).
  if (dt.getUTCMonth() !== m - 1) return new Date(Date.UTC(ty, m, 0)).toISOString().slice(0, 10);
  return dt.toISOString().slice(0, 10);
}

export function parseSmFilters(
  raw: RawParams,
  session: { wids: readonly number[]; cats: readonly number[] },
  now: Date,
  opts: { defaultDays: number },
): SmFilterParse {
  let adjusted = false;
  const yesterday = wibYesterday(now);

  const fromIn = all(raw, 'from')[0];
  const toIn = all(raw, 'to')[0];
  let to = yesterday;
  let from = addDays(to, -(opts.defaultDays - 1));
  if (fromIn !== undefined || toIn !== undefined) {
    const ok = isIsoDate(fromIn) && isIsoDate(toIn) && fromIn <= toIn && toIn <= yesterday && diffDays(fromIn, toIn) + 1 <= MAX_DAYS;
    if (ok) {
      from = fromIn;
      to = toIn;
    } else {
      adjusted = true;
    }
  }

  // Outlet: hanya id yang ada di wids sesi (tanpa 0).
  const allowed = new Set(session.wids.filter((w) => w > 0));
  const whIn = Array.from(new Set(csv(raw, 'wh')));
  let wh = whIn.filter((s) => ID_RE.test(s)).map(Number).filter((n) => allowed.has(n));
  if (wh.length !== whIn.length) adjusted = true;
  if (wh.length > SM_MAX_WH) {
    wh = wh.slice(0, SM_MAX_WH);
    adjusted = true;
  }
  wh = Array.from(new Set(wh)).sort((a, b) => a - b);

  // Kategori: param ada (walau kosong) = pilihan pengguna; tidak ada = default `cats` sesi (L-189: bukan pembatas data).
  const catKeyGiven = all(raw, 'cat').length > 0;
  let cat: number[];
  let catDefaulted = false;
  if (catKeyGiven) {
    const catIn = Array.from(new Set(csv(raw, 'cat')));
    cat = catIn.filter((s) => ID_RE.test(s)).map(Number).filter((n) => n > 0 && n <= 4_294_967_295);
    if (cat.length !== catIn.length) adjusted = true;
    if (cat.length > SM_MAX_CAT) {
      cat = cat.slice(0, SM_MAX_CAT);
      adjusted = true;
    }
    cat = Array.from(new Set(cat)).sort((a, b) => a - b);
  } else {
    cat = session.cats.slice(0, SM_MAX_CAT);
    catDefaulted = cat.length > 0;
  }

  const len = diffDays(from, to) + 1;
  return {
    filters: {
      from,
      to,
      prev: { from: addDays(from, -len), to: addDays(from, -1) },
      yoy: { from: addYears(from, -1), to: addYears(to, -1) },
      wh,
      cat,
      catDefaulted,
    },
    adjusted,
  };
}

/** Hari pertama bulan, `n` bulan sebelum bulan dari `iso` (awal jendela GMROI: n = 11 untuk periode tahun). */
export function firstOfMonthMinus(iso: string, n: number): string {
  const [y, m] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1 - n, 1)).toISOString().slice(0, 10);
}

/**
 * Jendela halaman Kepala toko (tanpa filter periode, §3): "minggu berjalan" Senin s.d. hari ini (WIB) vs minggu lalu pada
 * rentang hari yang sama panjang.
 */
export function storeWindow(now: Date): { from: string; to: string; prev: { from: string; to: string } } {
  const today = wibToday(now);
  const from = addDays(today, -(isoWeekday(today) - 1));
  return { from, to: today, prev: { from: addDays(from, -7), to: addDays(today, -7) } };
}

/** Outlet efektif untuk query: pilihan eksplisit, atau semua `wids` sesi tanpa 0. */
export function effectiveWh(filters: Pick<SmFilters, 'wh'>, session: { wids: readonly number[] }): number[] {
  if (filters.wh.length > 0) return filters.wh;
  return session.wids.filter((w) => w > 0);
}

/** Semua warehouse sesi tanpa 0 (mengabaikan filter outlet): stok & PO DC. */
export const allWh = (session: { wids: readonly number[] }): number[] => session.wids.filter((w) => w > 0);

/** Filter outlet untuk Marketing: pilihan eksplisit, atau seluruh `wids` termasuk 0 (event tanpa outlet). */
export function effectiveWhz(filters: Pick<SmFilters, 'wh'>, session: { wids: readonly number[] }): number[] {
  return filters.wh.length > 0 ? filters.wh : [...session.wids];
}
