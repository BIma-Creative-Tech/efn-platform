/**
 * Persona supermarket: rute, label, urutan, pemeriksaan akses, navigasi, dan chip peran + cakupan
 * (docs/04 §28.13.11, docs/design/insight-supermarket.md §2). Murni (tanpa I/O) agar bisa diuji; dipakai proxy.ts,
 * halaman (lapis kedua), dan layout.
 */
import { esbCodesOf, isPersona, personasOf, smCodesOf, type Persona } from './claims';
import { FNB_ITEMS, NAV, SUMMARY_ITEMS, type NavItem } from './page-filters';

export const PERSONA_ROUTES: Readonly<Record<Persona, string>> = {
  store: '/store',
  dc: '/dc',
  category: '/category',
  marketing: '/marketing',
  exec: '/exec',
};

/** Urutan prioritas beranda dan sidebar (§2.1). */
export const PERSONA_PRIORITY: readonly Persona[] = ['store', 'dc', 'category', 'marketing', 'exec'];

export const PERSONA_LABEL: Readonly<Record<Persona, string>> = {
  store: 'Kepala toko',
  dc: 'DC',
  category: 'Category manager',
  marketing: 'Marketing',
  exec: 'Direksi',
};

const NAV_LABEL: Readonly<Record<Persona, { label: string; icon: string }>> = {
  store: { label: 'Toko', icon: 'store' },
  dc: { label: 'DC', icon: 'truck' },
  category: { label: 'Kategori', icon: 'grid' },
  marketing: { label: 'Marketing', icon: 'megaphone' },
  exec: { label: 'Eksekutif', icon: 'briefcase' },
};

/** Rute persona yang dicocokkan persis atau sebagai awalan segmen (`/store`, `/store/...`). */
export function personaOfPath(pathname: string): Persona | null {
  for (const p of PERSONA_PRIORITY) {
    const r = PERSONA_ROUTES[p];
    if (pathname === r || pathname.startsWith(`${r}/`)) return p;
  }
  return null;
}

export type SmDenial = 'sm-belum-aktif' | 'tidak-berwenang' | 'buka-ulang';

/** Halaman informasi publik tujuan rewrite/redirect untuk tiap penolakan. */
export const DENIAL_PATH: Readonly<Record<SmDenial, string>> = {
  'sm-belum-aktif': '/sm-belum-aktif',
  'tidak-berwenang': '/tidak-berwenang',
  'buka-ulang': '/buka-ulang',
};

export interface SmSessionLike {
  v: 1 | 2;
  roles: readonly string[];
  codes: readonly string[];
}

/**
 * Keputusan akses halaman persona (null = boleh). Urutan: flag INSIGHT_SM_ENABLED -> sesi eis1 (tanpa wids/persona; minta
 * buka ulang dari Odoo) -> persona ada di `roles`. Q-INS-S9 (exec membuka halaman persona lain): default TIDAK.
 */
export function smRouteDenial(pathname: string, session: SmSessionLike, smEnabled: boolean): SmDenial | null {
  const persona = personaOfPath(pathname);
  if (persona === null) return null;
  if (!smEnabled) return 'sm-belum-aktif';
  if (session.v !== 2) return 'buka-ulang';
  return session.roles.includes(persona) ? null : 'tidak-berwenang';
}

export const hasEsbAccess = (s: { codes: readonly string[] }): boolean => esbCodesOf(s.codes).length > 0;
export const hasSmAccess = (s: { codes: readonly string[]; roles: readonly string[] }): boolean =>
  smCodesOf(s.codes).length > 0 && personasOf(s.roles).length > 0;

/** Persona sesi dalam urutan prioritas. */
export function sessionPersonas(roles: readonly string[]): Persona[] {
  const have = new Set(roles.filter(isPersona));
  return PERSONA_PRIORITY.filter((p) => have.has(p));
}

/**
 * Tujuan beranda `/`. Sesi dengan kode ESB tetap memakai Ringkasan ESB (`/`); sesi SM-saja diarahkan ke persona pertama
 * (store, dc, category, marketing, exec). Tanpa persona dan tanpa ESB (tidak mungkin pada eis2 sah) -> /belum-aktif.
 */
export function homePath(s: { codes: readonly string[]; roles: readonly string[] }): string {
  if (hasEsbAccess(s)) return '/';
  const first = sessionPersonas(s.roles)[0];
  return first ? PERSONA_ROUTES[first] : '/belum-aktif';
}

/**
 * G51 B1: SMD menjadi filter sungguhan di "Brand / PT" halaman F&B (tingkat PT) HANYA untuk sesi eis2 berpersona `exec` dengan kode SM
 * dan INSIGHT_SM_ENABLED. Sesi SM tanpa exec tetap lewat sidebar Supermarket. Satu-satunya penentu; dipakai pageCtx (validasi `pt`
 * di server), layout (chip + daftar cabang), dan halaman. Menggantikan chip tautan `smFilterLinks` (PR #133).
 */
export function smdAllowed(
  s: { v: 1 | 2; codes: readonly string[]; roles: readonly string[] },
  smEnabled: boolean,
): boolean {
  return s.v === 2 && execSmd(s, smEnabled);
}

/** Syarat inti (tanpa versi sesi; eis1 tidak pernah membawa kode SM): flag SM, persona exec, kode SM. */
function execSmd(s: { codes: readonly string[]; roles: readonly string[] }, smEnabled: boolean): boolean {
  return smEnabled && s.roles.includes('exec') && smCodesOf(s.codes).length > 0;
}

/** Halaman F&B yang mendukung SMD (docs/04 §28.14.1); sesi exec tanpa PT F&B hanya melihat menu ini. */
export const SMD_NAV_HREFS: readonly string[] = ['/', '/sales', '/payments', '/distribution', '/forecast'];

export interface NavGroup {
  /** Kunci stabil (dipakai untuk menyimpan status lipat di localStorage). */
  id: 'summary' | 'fnb' | 'sm';
  title: string;
  items: readonly NavItem[];
}

/** Urutan menu persona di grup Supermarket (keputusan pemilik proyek 21 Nov 2026): Eksekutif, Toko, DC, Kategori, Marketing. */
const SM_ORDER: readonly Persona[] = ['exec', 'store', 'dc', 'category', 'marketing'];

/**
 * Sidebar tiga grup yang bisa dilipat (keputusan pemilik proyek 21 Nov 2026, Usulan A):
 *  - Ringkasan (F&B + Supermarket): Ikhtisar, Penjualan, Pembayaran. Tampil bila ada PT F&B ATAU sesi exec ber-SMD.
 *  - Food & Beverage: Produksi vs Penjualan, Distribusi CK, Kualitas Data ESB. Hanya bila ada kode ESB; sesi exec ber-SMD
 *    tanpa PT F&B hanya mendapat Distribusi (isinya bagian DC supermarket, docs/04 §28.14.1).
 *  - Supermarket: menu persona (∈ roles; hanya bila INSIGHT_SM_ENABLED) + Forecast (hanya exec ber-SMD).
 * Aturan visibilitas = aturan sesi yang ada; grup kosong disembunyikan.
 */
export function navGroups(s: { codes: readonly string[]; roles: readonly string[] }, smEnabled: boolean): NavGroup[] {
  const esb = hasEsbAccess(s);
  const smd = execSmd(s, smEnabled);
  const groups: NavGroup[] = [];

  const summary: readonly NavItem[] = esb || smd ? SUMMARY_ITEMS : [];
  if (summary.length > 0) groups.push({ id: 'summary', title: 'Ringkasan', items: summary });

  const fnb: NavItem[] = esb ? [...FNB_ITEMS] : smd ? [{ ...NAV.distribution, label: 'Distribusi CK / DC' }] : [];
  if (fnb.length > 0) groups.push({ id: 'fnb', title: 'Food & Beverage', items: fnb });

  const have = new Set(sessionPersonas(s.roles));
  const smItems: NavItem[] = smEnabled
    ? SM_ORDER.filter((p) => have.has(p)).map((p) => ({ href: PERSONA_ROUTES[p], label: NAV_LABEL[p].label, icon: NAV_LABEL[p].icon }))
    : [];
  if (smd) smItems.push(NAV.forecast);
  if (smItems.length > 0) groups.push({ id: 'sm', title: 'Supermarket', items: smItems });
  return groups;
}

export interface WarehouseInfo {
  id: number;
  name: string;
  type: 'outlet' | 'dc' | 'none';
}

/**
 * Chip peran + cakupan (§2.2): "Kepala toko · Outlet Kelapa Gading", "Direksi · semua outlet (5)". Teks, bukan warna.
 * `wids` = wids sesi; 0 diabaikan. Nama hanya bila tepat satu outlet dikenal.
 */
export function personaChip(persona: Persona, wids: readonly number[], warehouses: readonly WarehouseInfo[]): string {
  const ids = wids.filter((w) => w > 0);
  const known = new Map(warehouses.map((w) => [w.id, w]));
  const outlets = ids.filter((id) => known.get(id)?.type !== 'dc');
  const label = PERSONA_LABEL[persona];
  if (persona === 'store' || outlets.length === 1) {
    if (outlets.length === 1) {
      const n = known.get(outlets[0])?.name;
      return n ? `${label} · ${n}` : `${label} · 1 outlet`;
    }
    return `${label} · ${outlets.length} outlet`;
  }
  return outlets.length > 0 ? `${label} · semua outlet (${outlets.length})` : label;
}
