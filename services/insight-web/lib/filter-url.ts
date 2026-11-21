/**
 * Perakit URL filter (auto-apply, poin 3 review 21 Nov 2026). Murni: toolbar klien memanggil fungsi ini setiap filter
 * berubah lalu `router.push`. Server TETAP memvalidasi ulang semua param (lib/filters.ts, lib/sm-filters.ts).
 */
export interface FilterDraft {
  from: string;
  to: string;
  pt: string[];
  branch: string[];
  cat: string[];
  product: string[];
}

const ESB_KEYS = ['from', 'to', 'pt', 'branch', 'cat', 'product'] as const;

export const sameSet = (a: readonly string[], b: readonly string[]): boolean => a.length === b.length && a.every((x) => b.includes(x));

/** Klik chip brand: dari "Semua" memilih satu brand; selain itu toggle; kosong atau seluruh brand = "Semua" ([]). */
export function nextPt(cur: readonly string[], code: string, all: readonly string[]): string[] {
  const base = cur.length === 0 ? [] : [...cur];
  const next = base.includes(code) ? base.filter((c) => c !== code) : [...base, code];
  return next.length === 0 || sameSet(next, all) ? [] : next;
}

/**
 * Query string halaman ESB. Param filter lama dibuang lalu ditulis ulang dari `next`; param lain (mis. sort) dipertahankan.
 * Tanggal ditulis bila berbeda dari bawaan, bila URL sudah memuatnya, atau bila `forceDate` (pengguna memilih tanggal:
 * dibawa eksplisit agar halaman lain, termasuk Supermarket yang punya bawaan sendiri, memakai rentang yang sama).
 */
export function buildEsbQuery(
  current: URLSearchParams,
  next: FilterDraft,
  o: { defaults: { from: string; to: string }; allCodes: readonly string[]; forceDate?: boolean },
): string {
  const p = new URLSearchParams(current.toString());
  for (const k of ESB_KEYS) p.delete(k);
  const differs = next.from !== o.defaults.from || next.to !== o.defaults.to;
  if (differs || current.has('from') || o.forceDate === true) {
    p.set('from', next.from);
    p.set('to', next.to);
  }
  // Tanpa param = semua PT sesi (bawaan). Subset apa pun ditulis eksplisit.
  if (next.pt.length > 0 && !sameSet(next.pt, o.allCodes)) p.set('pt', [...next.pt].sort().join(','));
  if (next.branch.length > 0) p.set('branch', next.branch.join(','));
  for (const c of next.cat) p.append('cat', c);
  if (next.product.length > 0) p.set('product', next.product.join(','));
  return p.toString();
}

/**
 * Query string halaman Supermarket (param: from, to, wh, cat). `cat` undefined = jangan sentuh (pertahankan bawaan `cats` sesi);
 * array (walau kosong) = pilihan pengguna, ditulis sebagai `cat=` (kosong = semua kategori).
 */
export function buildSmQuery(
  current: URLSearchParams,
  next: { from?: string; to?: string; wh: readonly number[]; cat?: readonly number[] },
): string {
  const p = new URLSearchParams(current.toString());
  p.delete('wh');
  if (next.from !== undefined && next.to !== undefined) {
    p.set('from', next.from);
    p.set('to', next.to);
  }
  if (next.wh.length > 0) p.set('wh', [...next.wh].sort((a, b) => a - b).join(','));
  if (next.cat !== undefined) p.set('cat', [...next.cat].sort((a, b) => a - b).join(','));
  return p.toString();
}
