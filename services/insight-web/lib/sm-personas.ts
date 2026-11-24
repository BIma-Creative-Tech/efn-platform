/**
 * Persona supermarket: rute, label, urutan, pemeriksaan akses, navigasi, dan chip peran + cakupan
 * (docs/04 §28.13.11, docs/design/insight-supermarket.md §2). Murni (tanpa I/O) agar bisa diuji; dipakai proxy.ts,
 * halaman (lapis kedua), dan layout.
 */
import { esbCodesOf, isPersona, personasOf, smCodesOf, type Persona } from './claims';
import { FNB_NAV, fnbNavItems, isBranchScoped } from './fnb-personas';
import { g67NavItems } from './g67-access';
import { ppNavItems } from './prodplan-access';
import { FNB_ITEMS, NAV, SHARED_HREFS, SUMMARY_ITEMS, type NavItem } from './page-filters';

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
  v: 1 | 2 | 3;
  roles: readonly string[];
  codes: readonly string[];
}

/**
 * Keputusan akses halaman persona (null = boleh). Urutan: flag INSIGHT_SM_ENABLED -> sesi eis1 (tanpa wids/persona; minta
 * buka ulang dari Odoo) -> kode SM ada (G52: sesi F&B berpersona, mis. kepala toko F&B, bukan sesi supermarket) -> persona
 * ada di `roles`. Q-INS-S9 (exec membuka halaman persona lain): default TIDAK.
 */
export function smRouteDenial(pathname: string, session: SmSessionLike, smEnabled: boolean): SmDenial | null {
  const persona = personaOfPath(pathname);
  if (persona === null) return null;
  if (!smEnabled) return 'sm-belum-aktif';
  if (session.v < 2) return 'buka-ulang';
  if (smCodesOf(session.codes).length === 0) return 'tidak-berwenang';
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
  s: { v: 1 | 2 | 3; codes: readonly string[]; roles: readonly string[] },
  smEnabled: boolean,
): boolean {
  return s.v >= 2 && execSmd(s, smEnabled);
}

/** Syarat inti (tanpa versi sesi; eis1 tidak pernah membawa kode SM): flag SM, persona exec, kode SM. */
function execSmd(s: { codes: readonly string[]; roles: readonly string[] }, smEnabled: boolean): boolean {
  return smEnabled && s.roles.includes('exec') && smCodesOf(s.codes).length > 0;
}

/** Halaman F&B yang mendukung SMD (docs/04 §28.14.1); sesi exec tanpa PT F&B hanya melihat menu ini. */
export const SMD_NAV_HREFS: readonly string[] = ['/', '/sales', '/payments', '/distribution', '/forecast'];

export interface NavGroup {
  /** Kunci stabil (dipakai untuk menyimpan status lipat di localStorage). */
  id: 'summary' | 'fnb' | 'sm' | 'prod';
  title: string;
  items: readonly NavItem[];
  /** Kode ESB sesi (terurut) untuk tautan ber-scope `esb` saat `pt` kosong / hanya SMD. Hanya di grup F&B sesi exec ber-SMD. */
  esbCodes?: readonly string[];
}

/** Urutan menu persona di grup Supermarket (keputusan pemilik proyek 21 Nov 2026): Eksekutif, Toko, DC, Kategori, Marketing. */
const SM_ORDER: readonly Persona[] = ['exec', 'store', 'dc', 'category', 'marketing'];

/**
 * Sidebar tiga grup yang bisa dilipat (keputusan pemilik proyek 21 Nov 2026 Usulan A, direvisi 22 Nov 2026):
 *  - Ringkasan: Ikhtisar. Tampil bila ada PT F&B ATAU sesi exec ber-SMD.
 *  - Food & Beverage: Penjualan, Pembayaran, Produksi vs Penjualan, Distribusi CK, Forecast, Kualitas Data ESB. Hanya bila ada
 *    kode ESB; sesi exec ber-SMD tanpa PT F&B hanya mendapat Distribusi (isinya bagian DC supermarket, docs/04 §28.14.1).
 *  - Supermarket: menu persona (∈ roles; hanya bila INSIGHT_SM_ENABLED) + Penjualan, Pembayaran, Forecast (hanya exec ber-SMD).
 * G67: Laba Rugi Outlet, Waste & Pemakaian Internal, Fill Rate Outlet ke CK masuk grup F&B (sebelum Forecast) bila `g67Enabled`.
 * Sesi exec ber-SMD + PT F&B: halaman bersama tampil di dua grup, dibedakan `scope` (tautan F&B tanpa SMD, tautan Supermarket
 * `pt=SMD`). Eksekutif/Toko/Marketing versi F&B menyusul (gelombang terpisah). Grup kosong disembunyikan.
 */
export function navGroups(
  s: { codes: readonly string[]; roles: readonly string[]; v?: number; brs?: readonly string[] },
  smEnabled: boolean,
  fnbEnabled = false,
  ppEnabled = false,
  g67Enabled = false,
): NavGroup[] {
  const esb = hasEsbAccess(s);
  // G52 (§28.15.9): sesi ber-cabang (kepala toko F&B) hanya melihat grup F&B berisi "Toko"; Ringkasan dan grup lain disembunyikan.
  // Flag F&B mati -> tidak ada menu sama sekali (halamannya pun ditolak proxy).
  if (isBranchScoped(s)) {
    return fnbEnabled && esb ? [{ id: 'fnb', title: 'Food & Beverage', items: [FNB_NAV.store] }] : [];
  }
  const smd = execSmd(s, smEnabled);
  const groups: NavGroup[] = [];

  if (esb || smd) groups.push({ id: 'summary', title: 'Ringkasan', items: SUMMARY_ITEMS });

  if (esb) {
    // G52: item persona F&B (Eksekutif, Toko, Marketing) di urutan teratas grup F&B bila flag aktif dan sesi memilikinya.
    const persona = fnbEnabled ? fnbNavItems(s) : [];
    const base = FNB_ITEMS.map((i): NavItem => (smd && SHARED_HREFS.includes(i.href) ? { ...i, scope: 'esb' } : i));
    // G67 (§31.8): Laba Rugi (exec), Waste, Fill Rate disisipkan sebelum Forecast, hanya bila INSIGHT_G67_ENABLED aktif.
    const g67 = g67Enabled ? g67NavItems({ v: s.v ?? 3, roles: s.roles, codes: s.codes, brs: s.brs }, { g67Enabled, fnbEnabled }) : [];
    const at = base.findIndex((i) => i.href === NAV.forecast.href);
    const withG67 = g67.length === 0 || at < 0 ? base : [...base.slice(0, at), ...g67, ...base.slice(at)];
    const items = [...persona, ...withG67];
    groups.push(smd ? { id: 'fnb', title: 'Food & Beverage', items, esbCodes: esbCodesOf(s.codes).sort() } : { id: 'fnb', title: 'Food & Beverage', items });
  } else if (smd) {
    groups.push({ id: 'fnb', title: 'Food & Beverage', items: [{ ...NAV.distribution, label: 'Distribusi CK / DC' }] });
  }

  // Menu persona SM hanya untuk sesi yang punya kode SM (G52: sesi F&B berpersona tidak mendapat menu supermarket).
  const have = new Set(smCodesOf(s.codes).length > 0 ? sessionPersonas(s.roles) : []);
  const smItems: NavItem[] = smEnabled
    ? SM_ORDER.filter((p) => have.has(p)).map((p) => ({ href: PERSONA_ROUTES[p], label: NAV_LABEL[p].label, icon: NAV_LABEL[p].icon }))
    : [];
  if (smd) smItems.push(...[NAV.sales, NAV.payments, NAV.forecast].map((i): NavItem => ({ ...i, scope: 'smd' })));
  if (smItems.length > 0) groups.push({ id: 'sm', title: 'Supermarket', items: smItems });
  // G66 (§30.12): grup "Produksi" hanya untuk sesi berperan PP dan bila INSIGHT_PRODPLAN_ENABLED aktif.
  const pp = ppNavItems({ v: s.v ?? 3, roles: s.roles, codes: s.codes, brs: s.brs }, ppEnabled);
  if (pp.length > 0) groups.push({ id: 'prod', title: 'Produksi', items: pp });
  return groups;
}

const csvParam = (q: URLSearchParams, k: string): string[] =>
  (q.get(k) ?? '')
    .split(',')
    .map((x) => x.trim())
    .filter((x) => x.length > 0);

/**
 * Query tautan menu ber-scope (murni; dipakai components/Chrome.tsx). `carry` = filter yang dibawa antar halaman F&B.
 *  - `smd`: `pt=SMD`, cabang hanya `sm:<id>`, filter kategori/produk (ESB-saja) dibuang.
 *  - `esb`: SMD dan cabang `sm:` dibuang; `pt` kosong -> semua kode ESB sesi (tanpa `pt` sesi exec juga memuat SMD).
 */
export function scopedQuery(scope: 'esb' | 'smd', carry: URLSearchParams, esbCodes: readonly string[]): URLSearchParams {
  const q = new URLSearchParams(carry.toString());
  const branch = csvParam(q, 'branch');
  const keepBranch = scope === 'smd' ? branch.filter((b) => b.startsWith('sm:')) : branch.filter((b) => !b.startsWith('sm:'));
  if (keepBranch.length > 0) q.set('branch', keepBranch.join(','));
  else q.delete('branch');
  if (scope === 'smd') {
    q.set('pt', 'SMD');
    q.delete('cat');
    q.delete('product');
    return q;
  }
  const pt = csvParam(q, 'pt').filter((c) => c.toUpperCase() !== 'SMD');
  const next = pt.length > 0 ? pt : [...esbCodes];
  if (next.length > 0) q.set('pt', next.join(','));
  else q.delete('pt');
  return q;
}

/** Item menu aktif: path sama; untuk item ber-scope, `smd` aktif hanya bila `pt` persis SMD, `esb` selain itu. */
export function navItemActive(item: NavItem, pathname: string, sp: URLSearchParams): boolean {
  if (pathname !== item.href) return false;
  if (!item.scope) return true;
  const pt = csvParam(sp, 'pt');
  const onlySmd = pt.length > 0 && pt.every((c) => c.toUpperCase() === 'SMD');
  return item.scope === 'smd' ? onlySmd : !onlySmd;
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
