/**
 * Akses modul Production Order (`/produksi*`, docs/04 §30.7 + §30.12, G66, ADR-051 Proposed). Murni (tanpa I/O): dipakai
 * proxy.ts (lapis pertama), halaman/route handler (lapis kedua), navGroups, dan test.
 *
 * Aturan: flag INSIGHT_PRODPLAN_ENABLED -> sesi eis3 -> peran PP + kode ESB + tanpa pembatasan cabang (T9 membuat sesi sah
 * selalu memenuhi dua syarat terakhir; diperiksa lagi di sini sebagai pertahanan berlapis).
 */
import { esbCodesOf, ppRolesOf, type PpRole } from './claims';
import type { NavItem } from './page-filters';

export const PP_PREFIX = '/produksi';

export type PpDenial = 'pp-belum-aktif' | 'tidak-berwenang' | 'buka-ulang';

/** Halaman informasi publik tujuan rewrite/redirect untuk tiap penolakan. */
export const PP_DENIAL_PATH: Readonly<Record<PpDenial, string>> = {
  'pp-belum-aktif': '/pp-belum-aktif',
  'tidak-berwenang': '/tidak-berwenang',
  'buka-ulang': '/buka-ulang',
};

export interface PpSessionLike {
  v: number;
  roles: readonly string[];
  codes: readonly string[];
  brs?: readonly string[];
}

/** `/produksi` persis atau awalan segmen (`/produksi/...`); `/produksix` bukan. */
export function isProduksiPath(pathname: string): boolean {
  return pathname === PP_PREFIX || pathname.startsWith(`${PP_PREFIX}/`);
}

export const hasPpRole = (roles: readonly string[]): boolean => ppRolesOf(roles).length > 0;

/** Keputusan akses (null = boleh). Selain rute /produksi selalu null. */
export function ppRouteDenial(pathname: string, session: PpSessionLike, ppEnabled: boolean): PpDenial | null {
  if (!isProduksiPath(pathname)) return null;
  return ppAccessDenial(session, ppEnabled);
}

/** Sama seperti ppRouteDenial tetapi tanpa pemeriksaan path (halaman dan route handler API). */
export function ppAccessDenial(session: PpSessionLike, ppEnabled: boolean): PpDenial | null {
  if (!ppEnabled) return 'pp-belum-aktif';
  if (session.v < 3) return 'buka-ulang';
  if (!hasPpRole(session.roles)) return 'tidak-berwenang';
  if (esbCodesOf(session.codes).length === 0) return 'tidak-berwenang';
  if ((session.brs?.length ?? 0) > 0) return 'tidak-berwenang';
  return null;
}

export interface PpCaps {
  /** pp_planner: buat/ubah rencana, hitung, ajukan, batalkan, master kemasan, kirim ulang. */
  planner: boolean;
  /** pp_warehouse: setujui/tolak transfer dan rencana. */
  warehouse: boolean;
}

export function ppCaps(roles: readonly string[]): PpCaps {
  const r = new Set<PpRole>(ppRolesOf(roles));
  return { planner: r.has('pp_planner'), warehouse: r.has('pp_warehouse') };
}

export const PP_ROLE_LABEL: Readonly<Record<PpRole, string>> = {
  pp_planner: 'Planner CK',
  pp_warehouse: 'Kepala gudang',
};

/** Chip peran di topbar: "Planner CK", "Kepala gudang", atau "Planner CK + Kepala gudang". */
export function ppRoleLabel(roles: readonly string[]): string {
  const have = ppRolesOf(roles);
  return have.length === 0 ? 'Produksi' : have.map((r) => PP_ROLE_LABEL[r]).join(' + ');
}

export const PP_NAV_ITEMS: readonly NavItem[] = [
  { href: '/produksi', label: 'Rencana produksi', icon: 'flow' },
  { href: '/produksi/kemasan', label: 'Kemasan utuh', icon: 'grid' },
];

/**
 * Item menu "Produksi": hanya sesi yang lolos ppAccessDenial (flag aktif + peran PP). Sesi ber-cabang tidak mendapat menu.
 */
export function ppNavItems(s: PpSessionLike, ppEnabled: boolean): readonly NavItem[] {
  return ppAccessDenial(s, ppEnabled) === null ? PP_NAV_ITEMS : [];
}

/** Item menu aktif: "Rencana produksi" juga aktif di /produksi/baru dan /produksi/<id>; "Kemasan utuh" hanya di rutenya. */
export function ppNavActive(href: string, pathname: string): boolean {
  if (href === '/produksi/kemasan') return pathname === href || pathname.startsWith(`${href}/`);
  if (href === PP_PREFIX) return isProduksiPath(pathname) && !(pathname === '/produksi/kemasan' || pathname.startsWith('/produksi/kemasan/'));
  return false;
}
