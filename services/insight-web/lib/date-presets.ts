/**
 * Preset rentang tanggal toolbar filter. Murni (berbasis ISO date, lib/dates.ts); `yesterday` = H-1 WIB dari server.
 */
import { addDays, diffDays, isIsoDate } from './dates';

export type PresetId = '7' | '30' | '90' | 'mtd' | 'prev-month' | 'ytd';

export const PRESETS: readonly { id: PresetId; label: string }[] = [
  { id: '7', label: '7 hari terakhir' },
  { id: '30', label: '30 hari terakhir' },
  { id: 'mtd', label: 'Bulan ini' },
  { id: 'prev-month', label: 'Bulan lalu' },
  { id: '90', label: '90 hari terakhir' },
  { id: 'ytd', label: 'Tahun ini' },
];

export function presetRange(p: PresetId, yesterday: string): { from: string; to: string } {
  const [y, m] = yesterday.split('-').map(Number);
  const pad = (n: number): string => String(n).padStart(2, '0');
  switch (p) {
    case '7':
      return { from: addDays(yesterday, -6), to: yesterday };
    case '30':
      return { from: addDays(yesterday, -29), to: yesterday };
    case '90':
      return { from: addDays(yesterday, -89), to: yesterday };
    case 'mtd':
      return { from: `${y}-${pad(m)}-01`, to: yesterday };
    case 'prev-month': {
      const to = addDays(`${y}-${pad(m)}-01`, -1);
      return { from: `${to.slice(0, 7)}-01`, to };
    }
    case 'ytd':
      return { from: `${y}-01-01`, to: yesterday };
  }
}

/** Preset yang persis sama dengan rentang ini (null = kustom). */
export function detectPreset(from: string, to: string, yesterday: string): PresetId | null {
  for (const p of PRESETS) {
    const r = presetRange(p.id, yesterday);
    if (r.from === from && r.to === to) return p.id;
  }
  return null;
}

/** Rentang kustom sah: tanggal ISO, from <= to <= kemarin, maks 366 hari (sama dengan validasi server). */
export function isValidRange(from: string, to: string, yesterday: string): boolean {
  return isIsoDate(from) && isIsoDate(to) && from <= to && to <= yesterday && diffDays(from, to) + 1 <= 366;
}
