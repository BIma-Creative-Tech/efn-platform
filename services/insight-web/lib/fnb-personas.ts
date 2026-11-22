/**
 * Persona F&B (Eksekutif, Toko, Marketing dari data ESB): rute, gerbang akses, item menu, dan chip peran
 * (docs/04 §28.15.9, G52; ADR-040, ADR-041 §3.6, Proposed). Murni (tanpa I/O): dipakai proxy.ts, halaman (lapis kedua),
 * navGroups, dan test.
 *
 * Gerbang ini TIDAK membaca INSIGHT_SM_ENABLED: flag F&B (INSIGHT_FNB_PERSONA_ENABLED) terpisah (L-238).
 * Sesi ber-cabang (`brs` tidak kosong; kepala toko F&B store-only) hanya boleh `/fnb/store` dan `/glosarium`; halaman F&B lain
 * ditolak di sini (lapis kedua; lapis ketiga = row policy kelas PT di ClickHouse, §28.15.7). Beranda `/` dialihkan ke
 * `/fnb/store` oleh halamannya (lib/page-ctx.ts), bukan ditolak.
 */
import { esbCodesOf } from './claims';
import type { NavItem } from './page-filters';

export type FnbPersona = 'exec' | 'store' | 'marketing';

export const FNB_ROUTES: Readonly<Record<FnbPersona, string>> = {
  exec: '/fnb/exec',
  store: '/fnb/store',
  marketing: '/fnb/marketing',
};

/** Urutan item menu F&B persona di sidebar (§28.15.9): Eksekutif, Toko, Marketing. */
export const FNB_ORDER: readonly FnbPersona[] = ['exec', 'store', 'marketing'];

export const FNB_NAV: Readonly<Record<FnbPersona, NavItem>> = {
  exec: { href: FNB_ROUTES.exec, label: 'Eksekutif', icon: 'briefcase' },
  store: { href: FNB_ROUTES.store, label: 'Toko', icon: 'store' },
  marketing: { href: FNB_ROUTES.marketing, label: 'Marketing', icon: 'megaphone' },
};

export type FnbDenial = 'fnb-belum-aktif' | 'tidak-berwenang' | 'buka-ulang';

/** Halaman informasi publik tujuan rewrite/redirect untuk tiap penolakan. */
export const FNB_DENIAL_PATH: Readonly<Record<FnbDenial, string>> = {
  'fnb-belum-aktif': '/fnb-belum-aktif',
  'tidak-berwenang': '/tidak-berwenang',
  'buka-ulang': '/buka-ulang',
};

export interface FnbSessionLike {
  v: number;
  roles: readonly string[];
  codes: readonly string[];
  /** Cabang ESB sesi; tidak ada/kosong = tanpa pembatasan cabang. */
  brs?: readonly string[];
}

/** Halaman F&B non-persona yang ditutup untuk sesi ber-cabang (`/` ditangani pageCtx: dialihkan ke /fnb/store). */
export const BRANCH_BLOCKED_PATHS: readonly string[] = ['/sales', '/payments', '/flow', '/distribution', '/forecast', '/data-quality'];

/** Persona F&B untuk path `/fnb/<persona>` (persis atau awalan segmen), selain itu null. */
export function fnbPersonaOfPath(pathname: string): FnbPersona | null {
  for (const p of FNB_ORDER) {
    const r = FNB_ROUTES[p];
    if (pathname === r || pathname.startsWith(`${r}/`)) return p;
  }
  return null;
}

export const isBranchScoped = (s: { brs?: readonly string[] }): boolean => (s.brs?.length ?? 0) > 0;

/**
 * Keputusan akses (null = boleh). Urutan untuk rute persona: flag F&B -> sesi < eis3 (minta buka ulang dari Odoo) -> kode ESB
 * dan persona ada di `roles` -> sesi ber-cabang hanya Toko. Untuk halaman F&B non-persona: sesi ber-cabang ditolak
 * (`tidak-berwenang`) walau flag mati (fail closed); selain itu tidak dibatasi di sini.
 */
export function fnbRouteDenial(pathname: string, session: FnbSessionLike, fnbEnabled: boolean): FnbDenial | null {
  const persona = fnbPersonaOfPath(pathname);
  if (persona === null) {
    if (isBranchScoped(session) && BRANCH_BLOCKED_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
      return 'tidak-berwenang';
    }
    return null;
  }
  if (!fnbEnabled) return 'fnb-belum-aktif';
  if (session.v < 3) return 'buka-ulang';
  if (esbCodesOf(session.codes).length === 0 || !session.roles.includes(persona)) return 'tidak-berwenang';
  if (isBranchScoped(session) && persona !== 'store') return 'tidak-berwenang';
  return null;
}

/** Item menu persona F&B yang dimiliki sesi (urutan Eksekutif, Toko, Marketing); kosong bila sesi tanpa kode ESB. */
export function fnbNavItems(s: { codes: readonly string[]; roles: readonly string[]; v?: number }): NavItem[] {
  if (esbCodesOf(s.codes).length === 0) return [];
  if (s.v !== undefined && s.v < 3) return [];
  return FNB_ORDER.filter((p) => s.roles.includes(p)).map((p) => FNB_NAV[p]);
}

/** Tautan cabang `<brand>:<id>` -> id numerik; null bila bentuk tidak sah. */
export function branchKeyParts(key: string): { brand: string; id: number } | null {
  const m = /^([a-z]+):(\d+)$/.exec(key);
  return m ? { brand: m[1], id: Number(m[2]) } : null;
}

/**
 * Chip peran kepala toko F&B (§28.15.9): "Kepala toko · <nama cabang>" bila tepat satu cabang dan namanya dikenal,
 * selain itu "Kepala toko · N cabang". `names` = peta `<brand>:<id>` -> nama cabang (boleh kosong).
 */
export function fnbStoreChip(brs: readonly string[], names: ReadonlyMap<string, string>): string {
  if (brs.length === 1) {
    const n = names.get(brs[0]);
    return n ? `Kepala toko · ${n}` : 'Kepala toko · 1 cabang';
  }
  return `Kepala toko · ${brs.length} cabang`;
}
