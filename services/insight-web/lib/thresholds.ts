/**
 * Aturan peringatan terpusat (docs/design/insight-web.md §2.5). Ambang = proposal; konfirmasi planner CK (Q-INS1).
 */
export type Status = 'good' | 'warn' | 'bad' | 'neutral' | 'unknown';

export const STATUS_LABEL: Record<Status, string> = {
  good: 'Baik',
  warn: 'Perlu dicek',
  bad: 'Bermasalah',
  neutral: '-',
  unknown: 'Tidak diketahui',
};

export const T = {
  sellThroughGood: 85,
  sellThroughWarn: 70,
  freshnessWarnHours: 26,
  freshnessBadHours: 48,
  discountWarnPct: 15,
  gapWarnPct: 10,
  gapBadPct: 20,
  mdrWarnPp: 0.3,
  unmappedWarn: 1,
  unmappedBad: 10,
} as const;

export function sellThroughStatus(pct: number | null | undefined): Status {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return 'neutral';
  if (pct >= T.sellThroughGood) return 'good';
  if (pct >= T.sellThroughWarn) return 'warn';
  return 'bad';
}

/** Umur data (jam). null = tidak diketahui (BUKAN baik). */
export function freshnessStatus(hours: number | null | undefined): Status {
  if (hours === null || hours === undefined || !Number.isFinite(hours)) return 'unknown';
  if (hours <= T.freshnessWarnHours) return 'good';
  if (hours <= T.freshnessBadHours) return 'warn';
  return 'bad';
}

export function discountStatus(discountPct: number | null): Status {
  if (discountPct === null) return 'neutral';
  return discountPct > T.discountWarnPct ? 'warn' : 'good';
}

export function marginStatus(marginPct: number | null): Status {
  if (marginPct === null) return 'neutral';
  return marginPct < 0 ? 'bad' : 'neutral';
}

/** |selisih| relatif terhadap kirim CK (%). */
export function gapStatus(gapPct: number | null): Status {
  if (gapPct === null) return 'neutral';
  const a = Math.abs(gapPct);
  if (a > T.gapBadPct) return 'bad';
  if (a > T.gapWarnPct) return 'warn';
  return 'good';
}

export function unmappedStatus(count: number): Status {
  if (count > T.unmappedBad) return 'bad';
  if (count >= T.unmappedWarn) return 'warn';
  return 'good';
}

/** Status terburuk dari beberapa status (unknown dianggap setara warn). */
export function worst(statuses: Status[]): Status {
  const rank: Record<Status, number> = { good: 0, neutral: 0, unknown: 1, warn: 2, bad: 3 };
  return statuses.reduce<Status>((a, b) => (rank[b] > rank[a] ? b : a), 'good');
}
