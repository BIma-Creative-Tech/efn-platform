/**
 * Pencarian dan filter tabel di sisi klien (poin 4, review 21 Nov 2026). Murni: dipakai DataTable (React, data nyata)
 * dan TableTools (menyaring baris tabel SM yang dirender server lewat atribut data-*). Hanya untuk UX; data sudah
 * dibatasi filter server, jadi tidak ada keputusan keamanan di sini.
 */
export const norm = (s: string): string => s.toLocaleLowerCase('id').normalize('NFKD').replace(/[̀-ͯ]/g, '').trim();

/** Semua kata pada `q` (dipisah spasi) harus muncul di `haystack` (sudah dinormalisasi atau belum). Kueri kosong = cocok. */
export function matchesQuery(haystack: string, q: string): boolean {
  const tokens = norm(q).split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return true;
  const h = norm(haystack);
  return tokens.every((t) => h.includes(t));
}

/** `sel` = {kunci: nilai terpilih}; nilai kosong = tidak difilter. Semua pilihan harus sama persis dengan `values`. */
export function matchesSelects(values: Readonly<Record<string, string>>, sel: Readonly<Record<string, string>>): boolean {
  return Object.entries(sel).every(([k, v]) => v === '' || (values[k] ?? '') === v);
}

/** Nilai unik tidak kosong, urut abjad Indonesia. */
export function distinct(values: readonly string[]): string[] {
  return Array.from(new Set(values.filter((v) => v !== ''))).sort((a, b) => a.localeCompare(b, 'id'));
}

/** "12 dari 340 baris" (bila tidak ada penyaringan: "340 baris"). */
export function countText(shown: number, total: number): string {
  return shown === total ? `${total} baris` : `${shown} dari ${total} baris`;
}
