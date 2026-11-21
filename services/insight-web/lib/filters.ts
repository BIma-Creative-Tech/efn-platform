/**
 * Filter global dari URL search params, divalidasi di SERVER (docs/04 §26.9; format kunci = katalog query
 * `docs/design/insight-web-queries.md` §1.5). Nilai tidak valid -> default (bukan error); `adjusted=true`
 * memberi tahu UI untuk menampilkan "Filter tidak valid, dikembalikan ke bawaan".
 *
 * Format:
 *   from, to   YYYY-MM-DD; from <= to <= H-1 (WIB); rentang <= 366 hari; default 30 hari s.d. H-1
 *   pt         kode PT dipisah koma, dipotong ke kode dalam sesi; kosong = semua kode ESB sesi. `SMD` (G51) hanya bila
 *              `opts.smd` (sesi exec); SMD tidak pernah masuk `Filters.pt` melainkan `sel`
 *   branch     "<brand>:<branch_id>" dipisah koma (maks 50); brand dalam {pb,wtz,bacha,imd,cup}; `sm:<warehouse_id>` = outlet SMD
 *   cat        parameter BERULANG (?cat=A&cat=B; nama kategori boleh memuat koma), maks 20, <= 64 karakter
 *   product    "<brand>:<product_code>" dipisah koma (maks 20); termasuk "<brand>:NAME:<alias>"
 */
import { addDays, diffDays, isIsoDate, wibYesterday } from './dates';

export const DEFAULT_DAYS = 30;
export const MAX_DAYS = 366;
export const MAX_BRANCH = 50;
export const MAX_CAT = 20;
export const MAX_PRODUCT = 20;

export interface Filters {
  from: string;
  to: string;
  pt: string[];
  branch: string[];
  cat: string[];
  product: string[];
}

/**
 * Pilihan PT pada halaman F&B (G51 B1). `Filters.pt`/`Filters.branch` SELALU hanya berisi brand ESB (aman dikirim ke query ESB);
 * SMD dibawa terpisah di sini. Hanya sesi dengan persona exec yang boleh `allowed`.
 */
export interface PtSelection {
  /** SMD boleh dipilih (sesi exec + SM aktif). false -> `smd` selalu false, `sm:` pada branch ditolak. */
  allowed: boolean;
  /** Query ESB boleh dikirim (ada brand ESB terpilih setelah menimbang filter cabang). */
  esb: boolean;
  /** SMD terpilih. */
  smd: boolean;
  /** Outlet SMD terpilih (id warehouse, urut naik); kosong = semua outlet sesi. */
  wh: number[];
}

export interface FilterParse {
  filters: Filters;
  /** Rentang pembanding: panjang sama, tepat sebelum `from`. */
  prev: { from: string; to: string };
  /** true bila ada param yang ditolak/diganti. */
  adjusted: boolean;
  sel: PtSelection;
}

export interface ParseOpts {
  /** SMD boleh dipilih (lihat `smdAllowed` di lib/sm-personas.ts). */
  smd?: boolean;
  /** Warehouse SMD yang boleh dipilih (wids sesi tanpa 0). Bila diisi, id lain ditolak. */
  smWids?: readonly number[];
}

export const SMD_CODE = 'SMD';

export type RawParams = URLSearchParams | Record<string, string | string[] | undefined>;

const BRANCH_RE = /^(pb|wtz|bacha|imd|cup):(\d{1,10})$/;
/** Outlet SMD: `sm:<warehouse_id>` (id warehouse Odoo, bukan id cabang ESB; nilai tidak bisa bentrok dengan BRANCH_RE). */
const SM_BRANCH_RE = /^sm:(\d{1,10})$/;
const PRODUCT_RE = /^(pb|wtz|bacha|imd|cup):[A-Za-z0-9:_ .-]{1,64}$/;
const BAD_CAT_RE = /[\u0000-\u001f\u007f\\]/;

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

function unique<T>(a: T[]): T[] {
  return Array.from(new Set(a));
}

export function parseFilters(raw: RawParams, sessionCodes: readonly string[], now: Date, opts: ParseOpts = {}): FilterParse {
  let adjusted = false;
  const smdAllowed = opts.smd === true;
  const yesterday = wibYesterday(now);

  // Rentang tanggal
  const fromIn = all(raw, 'from')[0];
  const toIn = all(raw, 'to')[0];
  let to = addDays(yesterday, 0);
  let from = addDays(to, -(DEFAULT_DAYS - 1));
  if (fromIn !== undefined || toIn !== undefined) {
    const ok =
      isIsoDate(fromIn) &&
      isIsoDate(toIn) &&
      fromIn <= toIn &&
      toIn <= yesterday &&
      diffDays(fromIn, toIn) + 1 <= MAX_DAYS;
    if (ok) {
      from = fromIn;
      to = toIn;
    } else {
      adjusted = true;
    }
  }

  // PT: subset dari kode sesi
  const sessionSet = new Set(sessionCodes);
  // SMD (G51 B1) hanya diterima bila `opts.smd`; selain itu ditolak persis seperti kode tak dikenal. `pt` hasil tetap hanya ESB.
  const ptIn = unique(csv(raw, 'pt').map((s) => s.toUpperCase()));
  const ptOk = ptIn.filter((c) => sessionSet.has(c) || (smdAllowed && c === SMD_CODE)).sort();
  if (ptIn.length > 0 && ptOk.length !== ptIn.length) adjusted = true;
  const ptEsb = ptOk.filter((c) => c !== SMD_CODE);
  const pt = ptEsb.length > 0 ? ptEsb : Array.from(sessionSet).sort();
  // Tanpa pt (atau pt tidak sah seluruhnya) = semua PT sesi, TERMASUK SMD untuk sesi exec, sama seperti brand lain
  // (keputusan pemilik proyek 21 Nov 2026: SMD berperilaku seperti brand lain di filter). Total gabungan diberi catatan
  // "basis campuran" di halaman (Q-SM46 default).
  const smdPicked = ptOk.includes(SMD_CODE) || (smdAllowed && ptOk.length === 0);
  // Hanya SMD dipilih -> tanpa query ESB. pt hanya ESB -> SMD tidak ikut.
  // Tanpa pt = semua PT: ESB ikut bila sesi punya kode ESB, SMD ikut bila boleh (smdPicked).
  let esbOn = ptEsb.length > 0 || (ptOk.length === 0 && sessionSet.size > 0) || !smdPicked;
  let smdOn = smdPicked;

  // Cabang: ESB `<brand>:<id>` dan SMD `sm:<warehouse_id>`
  const brIn = unique(csv(raw, 'branch').map((s) => s.toLowerCase()));
  const smAllowedIds = opts.smWids === undefined ? null : new Set(opts.smWids);
  let branch: string[] = [];
  let smWh: number[] = [];
  for (const s of brIn) {
    const m = BRANCH_RE.exec(s);
    if (m !== null && Number(m[2]) <= 4_294_967_295) {
      branch.push(s);
      continue;
    }
    const sm = SM_BRANCH_RE.exec(s);
    const id = sm === null ? NaN : Number(sm[1]);
    if (smdAllowed && smdOn && Number.isSafeInteger(id) && id > 0 && id <= 4_294_967_295 && (smAllowedIds === null || smAllowedIds.has(id))) {
      smWh.push(id);
      continue;
    }
    adjusted = true;
  }
  smWh = Array.from(new Set(smWh)).sort((a, b) => a - b);
  if (branch.length > MAX_BRANCH) {
    branch = branch.slice(0, MAX_BRANCH);
    adjusted = true;
  }
  if (smWh.length > MAX_BRANCH) {
    smWh = smWh.slice(0, MAX_BRANCH);
    adjusted = true;
  }
  // Filter cabang berlaku untuk semua PT terpilih: memilih cabang satu sisi mengecualikan sisi lain.
  if (esbOn && smdOn) {
    if (branch.length > 0 && smWh.length === 0) smdOn = false;
    else if (smWh.length > 0 && branch.length === 0) esbOn = false;
  } else if (smdOn && !esbOn && branch.length > 0) {
    // pt=SMD tetapi cabang ESB: tidak ada irisan -> abaikan cabang ESB.
    branch = [];
    adjusted = true;
  }
  if (!smdOn) smWh = [];

  // Kategori (param berulang)
  const catIn = unique(all(raw, 'cat').map((s) => s.trim()).filter((s) => s !== ''));
  let cat = catIn.filter((s) => s.length <= 64 && !BAD_CAT_RE.test(s));
  if (cat.length !== catIn.length) adjusted = true;
  if (cat.length > MAX_CAT) {
    cat = cat.slice(0, MAX_CAT);
    adjusted = true;
  }

  // Produk
  const prIn = unique(csv(raw, 'product').map((s) => s.replace(/^([A-Za-z]+):/, (_, b: string) => `${b.toLowerCase()}:`)));
  let product = prIn.filter((s) => PRODUCT_RE.test(s));
  if (product.length !== prIn.length) adjusted = true;
  if (product.length > MAX_PRODUCT) {
    product = product.slice(0, MAX_PRODUCT);
    adjusted = true;
  }

  const len = diffDays(from, to) + 1;
  return {
    filters: { from, to, pt, branch, cat, product },
    prev: { from: addDays(from, -len), to: addDays(from, -1) },
    adjusted,
    sel: { allowed: smdAllowed, esb: esbOn, smd: smdOn, wh: smWh },
  };
}

/** Filter -> URLSearchParams tanpa nilai bawaan; `defaults` = hasil parseFilters({}) untuk perbandingan. */
export function filtersToParams(f: Filters, defaults: Filters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.from !== defaults.from || f.to !== defaults.to) {
    p.set('from', f.from);
    p.set('to', f.to);
  }
  if (f.pt.join(',') !== defaults.pt.join(',')) p.set('pt', f.pt.join(','));
  if (f.branch.length > 0) p.set('branch', f.branch.join(','));
  for (const c of f.cat) p.append('cat', c);
  if (f.product.length > 0) p.set('product', f.product.join(','));
  return p;
}
