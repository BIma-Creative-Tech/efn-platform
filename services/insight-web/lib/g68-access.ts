/**
 * Akses halaman G68 (`/pnl`, `/waste`, `/fill-rate`; docs/04 §32.8, ADR-040 amandemen G68 + ADR-041, Proposed). Murni (tanpa I/O):
 * dipakai proxy.ts (lapis pertama), halaman (lapis kedua, lib/g68-page.ts), navGroups, dan test.
 *
 * Aturan (urutan): flag INSIGHT_G68_ENABLED -> sesi eis3 -> kode ESB -> tanpa pembatasan cabang. `/pnl` tambahan: persona exec
 * lewat `fnbRouteDenial` (flag INSIGHT_FNB_PERSONA_ENABLED ikut berlaku; lihat fnb-personas.ts). `/waste` dan `/fill-rate`
 * terbuka untuk semua pengguna F&B ber-cakupan PT (pola `/flow`). Lapis ketiga = row policy kelas PT di ClickHouse (§32.7).
 */
import { esbCodesOf } from './claims';
import { fnbRouteDenial, type FnbDenial, type FnbSessionLike } from './fnb-personas';
import type { NavItem } from './page-filters';

export const G68_PATHS = ['/pnl', '/waste', '/fill-rate'] as const;
export type G68Path = (typeof G68_PATHS)[number];

export type G68Denial = 'g68-belum-aktif' | FnbDenial;

/** Halaman informasi publik tujuan rewrite/redirect untuk penolakan khas G68 (selebihnya memakai FNB_DENIAL_PATH). */
export const G68_DISABLED_PATH = '/g68-belum-aktif';

export const G68_NAV: Readonly<Record<G68Path, NavItem>> = {
  '/pnl': { href: '/pnl', label: 'Laba Rugi Outlet', icon: 'card' },
  '/waste': { href: '/waste', label: 'Waste & Pemakaian Internal', icon: 'warn' },
  '/fill-rate': { href: '/fill-rate', label: 'Fill Rate Outlet ke CK', icon: 'truck' },
};

/** Path G68 persis atau awalan segmen (`/pnl/x`); `/pnlx` bukan. null bila bukan rute G68. */
export function g68PathOf(pathname: string): G68Path | null {
  for (const p of G68_PATHS) if (pathname === p || pathname.startsWith(`${p}/`)) return p;
  return null;
}

/** Keputusan akses (null = boleh). Selain rute G68 selalu null. */
export function g68RouteDenial(
  pathname: string,
  session: FnbSessionLike,
  cfg: { g68Enabled: boolean; fnbEnabled: boolean },
): G68Denial | null {
  const path = g68PathOf(pathname);
  if (path === null) return null;
  if (!cfg.g68Enabled) return 'g68-belum-aktif';
  if (session.v < 3) return 'buka-ulang';
  if (esbCodesOf(session.codes).length === 0) return 'tidak-berwenang';
  if ((session.brs?.length ?? 0) > 0) return 'tidak-berwenang';
  // `/pnl` = persona exec (fnbRouteDenial memetakan /pnl ke exec): flag F&B, peran exec.
  if (path === '/pnl') return fnbRouteDenial('/pnl', session, cfg.fnbEnabled);
  return null;
}

/** Item menu G68 yang boleh dilihat sesi (kosong bila flag mati). Urutan: Laba Rugi, Waste, Fill Rate. */
export function g68NavItems(s: FnbSessionLike, cfg: { g68Enabled: boolean; fnbEnabled: boolean }): NavItem[] {
  return G68_PATHS.filter((p) => g68RouteDenial(p, s, cfg) === null).map((p) => G68_NAV[p]);
}
