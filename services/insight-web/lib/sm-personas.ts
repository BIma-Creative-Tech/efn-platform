/**
 * Persona supermarket: rute, label, urutan, pemeriksaan akses, navigasi, dan chip peran + cakupan
 * (docs/04 §28.13.11, docs/design/insight-supermarket.md §2). Murni (tanpa I/O) agar bisa diuji; dipakai proxy.ts,
 * halaman (lapis kedua), dan layout.
 */
import { esbCodesOf, isPersona, personasOf, smCodesOf, type Persona } from './claims';
import { NAV_ITEMS, type NavItem } from './page-filters';

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
  store: { label: 'Toko saya', icon: 'store' },
  dc: { label: 'Distribution Center', icon: 'truck' },
  category: { label: 'Kategori', icon: 'grid' },
  marketing: { label: 'Marketing', icon: 'megaphone' },
  exec: { label: 'Direksi', icon: 'briefcase' },
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
 * Chip PT supermarket di filter "Brand / PT" halaman F&B (sesi gabungan ESB + SM). Data SMD tidak ada di view ESB, jadi
 * chip ini tautan ke dashboard supermarket (Direksi bila persona itu ada, selain itu persona pertama), bukan filter ESB.
 * Kosong bila INSIGHT_SM_ENABLED mati atau sesi tanpa persona.
 */
export function smFilterLinks(
  s: { codes: readonly string[]; roles: readonly string[] },
  smEnabled: boolean,
): { code: string; href: string }[] {
  if (!smEnabled || !hasSmAccess(s)) return [];
  const personas = sessionPersonas(s.roles);
  const href = PERSONA_ROUTES[personas.includes('exec') ? 'exec' : personas[0]];
  return smCodesOf(s.codes).map((code) => ({ code, href }));
}

export interface NavGroup {
  title?: string;
  items: readonly NavItem[];
}

/**
 * Sidebar: grup F&B (halaman ESB; hanya bila ada kode ESB) dan grup Supermarket (persona ∈ roles; hanya bila
 * INSIGHT_SM_ENABLED). Judul grup hanya muncul bila ada dua grup atau lebih dari satu persona (§2.1).
 */
export function navGroups(s: { codes: readonly string[]; roles: readonly string[] }, smEnabled: boolean): NavGroup[] {
  const esb = hasEsbAccess(s) ? NAV_ITEMS : [];
  const smItems: NavItem[] = smEnabled
    ? sessionPersonas(s.roles).map((p) => ({ href: PERSONA_ROUTES[p], label: NAV_LABEL[p].label, icon: NAV_LABEL[p].icon }))
    : [];
  const groups: NavGroup[] = [];
  const both = esb.length > 0 && smItems.length > 0;
  if (smItems.length > 0 && !both) {
    groups.push({ title: smItems.length > 1 ? 'Supermarket' : undefined, items: smItems });
    return groups;
  }
  if (esb.length > 0) groups.push({ title: both ? 'Food & Beverage' : undefined, items: esb });
  if (smItems.length > 0) groups.push({ title: 'Supermarket', items: smItems });
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
