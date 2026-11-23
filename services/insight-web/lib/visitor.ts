/**
 * Halaman "Pengunjung" lintas PT (V-01, keputusan pemilik proyek 23 Nov 2026: log kunjungan anonim tanpa consent, tampil di
 * Insight; demo SMD dan PB). Murni (tanpa I/O) agar bisa diuji; dipakai layout, halaman, dan lib/clickhouse.ts.
 *
 * Hak akses = aturan sesi Insight yang SUDAH ada (tanpa flag baru, tidak bergantung INSIGHT_FNB_PERSONA_ENABLED):
 *  - kode ESB sesi (PB, WTZ, ...) bila sesi tidak dibatasi cabang (`brs` kosong) = sama dengan halaman F&B tingkat PT;
 *  - kode SM sesi (SMD) bila INSIGHT_SM_ENABLED dan persona category/exec/marketing = sama dengan v_sm_visitor_funnel_daily.
 * Kode ini dihitung dari sesi terverifikasi dan ditandatangani (lib/scope.ts scopeSettingsVisitor); filter `pt` di URL hanya
 * mempersempit (lapis 1), row policy ClickHouse = lapis 2 (infra/clickhouse/access/insight-visitor-policy.sh).
 */
import { esbCodesOf, smCodesOf } from './claims';
import { addDays, diffDays, isIsoDate, wibYesterday } from './dates';
import type { RawParams } from './filters';
import type { NavGroup } from './sm-personas';
import type { NavItem } from './page-filters';

export const VISITOR_PATH = '/visitors';
export const VISITOR_NAV: NavItem = { href: VISITOR_PATH, label: 'Pengunjung', icon: 'users' };
/** Persona SM yang boleh melihat data pengunjung (= persona v_sm_visitor_funnel_daily). */
export const VISITOR_SM_PERSONAS: readonly string[] = ['category', 'exec', 'marketing'];
export const VISITOR_DEFAULT_DAYS = 30;
const MAX_DAYS = 366;

/** Label halaman (enum `page` kontrak V-01). Nilai di luar daftar -> "Lainnya". */
export const PAGE_LABEL: Readonly<Record<string, string>> = {
  home: 'Beranda',
  category: 'Kategori',
  product: 'Detail produk',
  search: 'Pencarian',
  cart: 'Keranjang',
  checkout: 'Checkout',
  promo: 'Promo',
  store: 'Toko / outlet',
  account: 'Akun',
  order: 'Pesanan',
  other: 'Lainnya',
};

export const pageLabel = (p: string): string => PAGE_LABEL[p] ?? PAGE_LABEL.other;

export interface VisitorSessionLike {
  codes: readonly string[];
  roles: readonly string[];
  brs?: readonly string[];
}

/** Kode PT yang boleh dilihat di halaman Pengunjung (urut naik, unik). Kosong = tidak berwenang. */
export function visitorCodes(s: VisitorSessionLike, smEnabled: boolean): string[] {
  const out = new Set<string>();
  if ((s.brs?.length ?? 0) === 0) for (const c of esbCodesOf(s.codes)) out.add(c);
  if (smEnabled && s.roles.some((r) => VISITOR_SM_PERSONAS.includes(r))) for (const c of smCodesOf(s.codes)) out.add(c);
  return Array.from(out).sort();
}

/**
 * Tambah item "Pengunjung" ke grup Ringkasan (dibuat bila belum ada) bila sesi punya kode pengunjung. Fungsi terpisah dari
 * navGroups agar urutan/isi grup lain tidak berubah.
 */
export function withVisitorNav(groups: readonly NavGroup[], s: VisitorSessionLike, smEnabled: boolean): NavGroup[] {
  if (visitorCodes(s, smEnabled).length === 0) return [...groups];
  const i = groups.findIndex((g) => g.id === 'summary');
  if (i < 0) return [{ id: 'summary', title: 'Ringkasan', items: [VISITOR_NAV] }, ...groups];
  return groups.map((g, j) => (j === i ? { ...g, items: [...g.items, VISITOR_NAV] } : g));
}

export type VisitorChannel = '' | 'web' | 'app';

export interface VisitorFilters {
  from: string;
  to: string;
  prev: { from: string; to: string };
  /** Kode PT terpilih (⊆ kode sesi, urut naik). Tidak pernah kosong. */
  codes: string[];
  channel: VisitorChannel;
}

function first(raw: RawParams, key: string): string | undefined {
  if (raw instanceof URLSearchParams) return raw.get(key) ?? undefined;
  const v = raw[key];
  return Array.isArray(v) ? v[0] : v;
}

/**
 * Filter URL divalidasi server: `from`,`to` (<= kemarin WIB, <= 366 hari; bawaan 30 hari), `pt` (koma, dipotong ke kode sesi;
 * kosong/tidak sah = semua kode sesi), `channel` (`web`|`app`|kosong). Nilai tidak sah -> bawaan + `adjusted`.
 */
export function parseVisitorFilters(
  raw: RawParams,
  allowed: readonly string[],
  now: Date,
): { filters: VisitorFilters; adjusted: boolean } {
  let adjusted = false;
  const yesterday = wibYesterday(now);
  let to = yesterday;
  let from = addDays(to, -(VISITOR_DEFAULT_DAYS - 1));
  const fromIn = first(raw, 'from');
  const toIn = first(raw, 'to');
  if (fromIn !== undefined || toIn !== undefined) {
    if (isIsoDate(fromIn) && isIsoDate(toIn) && fromIn <= toIn && toIn <= yesterday && diffDays(fromIn, toIn) + 1 <= MAX_DAYS) {
      from = fromIn;
      to = toIn;
    } else {
      adjusted = true;
    }
  }
  const ptIn = (first(raw, 'pt') ?? '')
    .split(',')
    .map((x) => x.trim().toUpperCase())
    .filter((x) => x !== '');
  let codes = Array.from(new Set(ptIn.filter((c) => allowed.includes(c)))).sort();
  if (codes.length !== new Set(ptIn).size) adjusted = true;
  if (codes.length === 0) codes = [...allowed].sort();
  const chIn = first(raw, 'channel') ?? '';
  const channel: VisitorChannel = chIn === 'web' || chIn === 'app' ? chIn : '';
  if (chIn !== channel) adjusted = true;
  const len = diffDays(from, to) + 1;
  return { filters: { from, to, prev: { from: addDays(from, -len), to: addDays(from, -1) }, codes, channel }, adjusted };
}

/** Query string tautan filter halaman (tanggal + PT + kanal), untuk tab kanal dan chip PT. */
export function visitorHref(f: Pick<VisitorFilters, 'from' | 'to'>, codes: readonly string[], channel: VisitorChannel): string {
  const p = new URLSearchParams();
  p.set('from', f.from);
  p.set('to', f.to);
  if (codes.length > 0) p.set('pt', codes.join(','));
  if (channel) p.set('channel', channel);
  return `${VISITOR_PATH}?${p.toString()}`;
}

export interface ProductConversion {
  productId: number;
  name: string;
  code: string;
  views: number;
  viewers: number;
  carts: number;
  buys: number;
  /** Pembelian / perangkat-hari yang melihat (%); null bila tidak ada yang melihat. */
  conversionPct: number | null;
}

const n = (v: unknown): number => {
  const x = Number(v ?? 0);
  return Number.isFinite(x) ? x : 0;
};

/** Baris produk dari query vis_products (sudah k-anonim di SQL); nama kosong -> "Produk #<id>". */
export function toProductConversion(r: Record<string, unknown>): ProductConversion {
  const viewers = n(r.viewers_n);
  const buys = n(r.buys_n);
  const id = n(r.product_id);
  const name = typeof r.name_text === 'string' && r.name_text.trim() !== '' ? r.name_text : `Produk #${id}`;
  return {
    productId: id,
    name,
    code: typeof r.code_text === 'string' ? r.code_text : '',
    views: n(r.views_n),
    viewers,
    carts: n(r.carts_n),
    buys,
    conversionPct: viewers > 0 ? (buys / viewers) * 100 : null,
  };
}

/** Deret harian per PT (tanggal lengkap di rentang, hari tanpa data = 0) untuk chart garis. */
export function dailySeries(
  rows: readonly Record<string, unknown>[],
  days: readonly string[],
  field: 'visitors_n' | 'views_n',
): Map<string, number[]> {
  const idx = new Map(days.map((d, i) => [d, i]));
  const out = new Map<string, number[]>();
  for (const r of rows) {
    const code = String(r.company_code ?? '');
    const i = idx.get(String(r.day ?? ''));
    if (code === '' || i === undefined) continue;
    const arr = out.get(code) ?? new Array<number>(days.length).fill(0);
    arr[i] += n(r[field]);
    out.set(code, arr);
  }
  return out;
}
