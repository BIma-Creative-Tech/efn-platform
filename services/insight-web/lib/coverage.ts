/**
 * Cakupan data ESB per entitas (view `v_esb_coverage`, review 21 Nov 2026). Murni (tanpa I/O): menyusun kalimat banner
 * "Data produksi tersedia 10-16 Nov 2026; rentang dipilih 22 Okt-20 Nov 2026" dan menentukan apakah perbandingan periode
 * lalu bermakna. Tujuannya: angka nol karena data BELUM MASUK tidak boleh terlihat seperti "tidak ada penjualan".
 */
import { diffDays } from './dates';
import { fmtDate, fmtDayShort } from './format';
import { pctDelta as basePctDelta, ppDelta as basePpDelta } from './vm';

export interface CoverageRow {
  brand: string;
  company_code: string;
  entity: string;
  first_day: string;
  last_day: string;
  days: number;
}

export const ENTITY_NOUN: Readonly<Record<string, string>> = {
  sales: 'penjualan',
  payment: 'pembayaran',
  production: 'produksi',
  delivery: 'pengiriman',
  transfer: 'transfer',
  fin_sold: 'penjualan produk FIN',
};

export interface Span {
  first: string;
  last: string;
}

/** "10-16 Nov 2026" (bulan sama), "22 Okt-20 Nov 2026", atau dengan tahun penuh bila beda tahun. */
export function fmtSpan(a: string, b: string): string {
  if (a === b) return fmtDate(a);
  const [ay, am] = a.split('-');
  const [by, bm] = b.split('-');
  if (ay === by && am === bm) return `${Number(a.split('-')[2])}-${fmtDate(b)}`;
  if (ay === by) return `${fmtDayShort(a)}-${fmtDate(b)}`;
  return `${fmtDate(a)}-${fmtDate(b)}`;
}

/** Rentang gabungan (terawal s.d. terakhir) entitas di seluruh brand/PT dalam cakupan; null = tidak ada data sama sekali. */
export function entitySpan(rows: readonly CoverageRow[], entity: string): Span | null {
  const mine = rows.filter((r) => r.entity === entity && r.first_day && r.last_day);
  if (mine.length === 0) return null;
  return {
    first: mine.reduce((m, r) => (r.first_day < m ? r.first_day : m), mine[0].first_day),
    last: mine.reduce((m, r) => (r.last_day > m ? r.last_day : m), mine[0].last_day),
  };
}

/** true bila seluruh `range` ada di dalam `span`. */
export function covered(span: Span | null, range: { from: string; to: string }): boolean {
  return span !== null && range.from >= span.first && range.to <= span.last;
}

/**
 * Kalimat banner per entitas yang rentangnya melebihi data tersedia (atau tidak punya data). Kosong = tidak perlu banner.
 * Hari bolong (days < rentang) disebut terpisah.
 */
export function coverageNotices(rows: readonly CoverageRow[], entities: readonly string[], range: { from: string; to: string }): string[] {
  const out: string[] = [];
  for (const e of entities) {
    const noun = ENTITY_NOUN[e] ?? e;
    const span = entitySpan(rows, e);
    if (span === null) {
      out.push(`Belum ada data ${noun} untuk brand/PT yang dipilih. Angka ${noun} bernilai nol karena data belum masuk, bukan karena tidak ada ${noun}.`);
      continue;
    }
    const chosen = fmtSpan(range.from, range.to);
    if (range.from < span.first || range.to > span.last) {
      out.push(
        `Data ${noun} tersedia ${fmtSpan(span.first, span.last)}; rentang dipilih ${chosen}. Hari di luar data tersedia bernilai nol karena data belum masuk, bukan karena tidak ada ${noun}.`,
      );
      continue;
    }
    const gappy = rows.filter((r) => r.entity === e && r.days < diffDays(r.first_day, r.last_day) + 1);
    if (gappy.length > 0) {
      const g = gappy[0];
      out.push(`Data ${noun} tidak lengkap: ${g.days} dari ${diffDays(g.first_day, g.last_day) + 1} hari ada pada ${fmtSpan(g.first_day, g.last_day)}; hari yang kosong terlihat sebagai nol.`);
    }
  }
  return out;
}

/**
 * Perbandingan periode lalu bermakna hanya bila periode ini DAN periode lalu seluruhnya ada di data (semua `entities`).
 * Tanpa baris cakupan sama sekali (view belum ada/gagal dimuat) -> dianggap bermakna agar perilaku lama tidak berubah.
 */
export function comparable(
  rows: readonly CoverageRow[] | null,
  entities: readonly string[],
  cur: { from: string; to: string },
  prev: { from: string; to: string },
): boolean {
  if (rows === null || rows.length === 0) return true;
  return entities.every((e) => {
    const span = entitySpan(rows, e);
    return covered(span, cur) && covered(span, prev);
  });
}

/** Pembuat delta yang menghasilkan null ("n/a, tidak ada data pembanding") saat perbandingan tidak bermakna. */
export function deltaGuards(ok: boolean): {
  pctDelta: (cur: number, prev: number) => number | null;
  ppDelta: (cur: number | null, prev: number | null) => number | null;
} {
  return {
    pctDelta: (cur, prev) => (ok ? basePctDelta(cur, prev) : null),
    ppDelta: (cur, prev) => (ok ? basePpDelta(cur, prev) : null),
  };
}
