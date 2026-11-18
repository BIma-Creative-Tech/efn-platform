/**
 * Pemetaan brand/PT -> nama, warna data, penanda (docs/design/insight-web.md §2.3). Warna = warna DATA yang
 * konsisten lintas halaman, bukan warna identitas brand. Brand di view ESB berupa huruf kecil (pb, wtz, bacha, imd, cup);
 * kode PT = PB, WTZ, BCF, BCR, IMD, CUP.
 */
import type { EsbCode } from './claims';

export type SeriesColor = 'c1' | 'c2' | 'c3' | 'c4' | 'c5' | 'c6' | 'c7';
export type Marker = 'circle' | 'rect' | 'triangle' | 'diamond' | 'arrow' | 'pin' | 'roundRect';

export const BRAND_KEYS = ['pb', 'wtz', 'bacha', 'imd', 'cup'] as const;

const CODE_INFO: Record<EsbCode, { name: string; color: SeriesColor; brand: string }> = {
  PB: { name: 'Paris Baguette', color: 'c1', brand: 'pb' },
  WTZ: { name: "Wetzel's Pretzels", color: 'c2', brand: 'wtz' },
  BCF: { name: 'Bacha Coffee (kafe)', color: 'c3', brand: 'bacha' },
  BCR: { name: 'Bacha Coffee (butik)', color: 'c4', brand: 'bacha' },
  IMD: { name: 'Im Donut', color: 'c5', brand: 'imd' },
  CUP: { name: 'Curry Up', color: 'c6', brand: 'cup' },
};

const BRAND_INFO: Record<string, { name: string; color: SeriesColor }> = {
  pb: { name: 'Paris Baguette', color: 'c1' },
  wtz: { name: "Wetzel's Pretzels", color: 'c2' },
  bacha: { name: 'Bacha Coffee', color: 'c3' },
  imd: { name: 'Im Donut', color: 'c5' },
  cup: { name: 'Curry Up', color: 'c6' },
};

export function codeName(code: string): string {
  return CODE_INFO[code as EsbCode]?.name ?? code;
}
export function codeColor(code: string): SeriesColor {
  return CODE_INFO[code as EsbCode]?.color ?? 'c7';
}
export function brandName(brand: string): string {
  return BRAND_INFO[brand]?.name ?? brand.toUpperCase();
}
export function brandColor(brand: string): SeriesColor {
  return BRAND_INFO[brand]?.color ?? 'c7';
}
export function brandOfCode(code: string): string | null {
  return CODE_INFO[code as EsbCode]?.brand ?? null;
}

const MARKERS: Marker[] = ['circle', 'rect', 'triangle', 'diamond', 'arrow', 'pin', 'roundRect'];
/** Penanda bentuk per warna (c1..c7) agar series tidak dibedakan oleh warna saja. */
export function markerFor(color: SeriesColor): Marker {
  return MARKERS[Number(color.slice(1)) - 1] ?? 'circle';
}
