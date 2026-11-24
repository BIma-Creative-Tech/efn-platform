/**
 * Akses halaman G67 (`/pnl`, `/waste`, `/fill-rate`; docs/04 §31.8, ADR-040 amandemen G67 + ADR-041, Proposed). Murni (tanpa I/O):
 * dipakai proxy.ts (lapis pertama), halaman (lapis kedua, lib/g67-page.ts), navGroups, dan test.
 *
 * Aturan (urutan): flag INSIGHT_G67_ENABLED -> sesi eis3 -> kode ESB -> tanpa pembatasan cabang. `/pnl` tambahan: persona exec
 * lewat `fnbRouteDenial` (flag INSIGHT_FNB_PERSONA_ENABLED ikut berlaku; lihat fnb-personas.ts). `/waste` dan `/fill-rate`
 * terbuka untuk semua pengguna F&B ber-cakupan PT (pola `/flow`). Lapis ketiga = row policy kelas PT di ClickHouse (§31.7).
 */
import { esbCodesOf } from './claims';
import { fnbRouteDenial, type FnbDenial, type FnbSessionLike } from './fnb-personas';
import type { NavItem } from './page-filters';

export const G67_PATHS = ['/pnl', '/waste', '/fill-rate'] as const;
export type G67Path = (typeof G67_PATHS)[number];

export type G67Denial = 'g67-belum-aktif' | FnbDenial;

/** Halaman informasi publik tujuan rewrite/redirect untuk penolakan khas G67 (selebihnya memakai FNB_DENIAL_PATH). */
export const G67_DISABLED_PATH = '/g67-belum-aktif';

export const G67_NAV: Readonly<Record<G67Path, NavItem>> = {
  '/pnl': { href: '/pnl', label: 'Laba Rugi Outlet', icon: 'card' },
  '/waste': { href: '/waste', label: 'Waste & Pemakaian Internal', icon: 'warn' },
  '/fill-rate': { href: '/fill-rate', label: 'Fill Rate Outlet ke CK', icon: 'truck' },
};

/** Path G67 persis atau awalan segmen (`/pnl/x`); `/pnlx` bukan. null bila bukan rute G67. */
export function g67PathOf(pathname: string): G67Path | null {
  for (const p of G67_PATHS) if (pathname === p || pathname.startsWith(`${p}/`)) return p;
  return null;
}

/** Keputusan akses (null = boleh). Selain rute G67 selalu null. */
export function g67RouteDenial(
  pathname: string,
  session: FnbSessionLike,
  cfg: { g67Enabled: boolean; fnbEnabled: boolean },
): G67Denial | null {
  const path = g67PathOf(pathname);
  if (path === null) return null;
  if (!cfg.g67Enabled) return 'g67-belum-aktif';
  if (session.v < 3) return 'buka-ulang';
  if (esbCodesOf(session.codes).length === 0) return 'tidak-berwenang';
  if ((session.brs?.length ?? 0) > 0) return 'tidak-berwenang';
  // `/pnl` = persona exec (fnbRouteDenial memetakan /pnl ke exec): flag F&B, peran exec.
  if (path === '/pnl') return fnbRouteDenial('/pnl', session, cfg.fnbEnabled);
  return null;
}

/** Item menu G67 yang boleh dilihat sesi (kosong bila flag mati). Urutan: Laba Rugi, Waste, Fill Rate. */
export function g67NavItems(s: FnbSessionLike, cfg: { g67Enabled: boolean; fnbEnabled: boolean }): NavItem[] {
  return G67_PATHS.filter((p) => g67RouteDenial(p, s, cfg) === null).map((p) => G67_NAV[p]);
}
