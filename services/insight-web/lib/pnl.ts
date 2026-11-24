/**
 * Logika murni halaman `/pnl` (Laba Rugi Outlet; docs/04 §31.9-§31.10, G67): filter bulan `mfrom`/`mto`, pemetaan nama pos
 * ringkasan ESB ke kunci tetap, rasio (persen terhadap penjualan bersih), dan penyusunan tabel pos x bulan beserta baris
 * "Tidak teralokasi ke outlet" (total brand dikurangi jumlah cabang). Basis = angka laporan ESB, tanpa penyesuaian EFN (§31.10).
 * Tanpa I/O; dapat diuji. Daftar sinonim nama pos dipakai juga oleh katalog query (lib/queries.ts) agar SQL dan TS selaras.
 */
import { addYears, startOfMonthShift, wibToday } from './dates';
import { fmtMonth } from './format';
import type { ColumnDef } from './table-defs';

/** Nama pos `summary` ESB (dinormalkan: huruf kecil, hanya a-z0-9) -> kunci tetap. Nama sebenarnya belum terverifikasi [B] (§31.13). */
export const PL_SYNONYMS = {
  net_sales: ['netsales', 'penjualanbersih', 'nettsales'],
  gross_profit: ['grossprofit', 'labakotor'],
  opex: ['operationalexpenses', 'operatingexpense', 'operatingexpenses', 'totaloperatingexpense', 'totaloperatingexpenses', 'opex', 'biayaoperasional'],
  ebitda: ['ebitda', 'earningbeforeinteresttaxdepreciationamortization'],
  net_profit: ['netprofit', 'netincome', 'netprofitloss', 'lababersih'],
} as const;

export type PlKey = keyof typeof PL_SYNONYMS;
export const PL_KEYS: readonly PlKey[] = ['net_sales', 'gross_profit', 'opex', 'ebitda', 'net_profit'];

export const PL_LABEL: Readonly<Record<PlKey, string>> = {
  net_sales: 'Penjualan bersih',
  gross_profit: 'Laba kotor',
  opex: 'Biaya operasional',
  ebitda: 'EBITDA',
  net_profit: 'Laba bersih',
};

export const plNorm = (s: string): string => s.toLowerCase().replace(/[^a-z0-9]/g, '');

export function plKeyOf(section: string): PlKey | null {
  const n = plNorm(section);
  for (const k of PL_KEYS) if ((PL_SYNONYMS[k] as readonly string[]).includes(n)) return k;
  return null;
}

/** Ekspresi SQL pemetaan kolom `section` -> kunci (konstanta; hanya karakter a-z0-9 dari daftar di atas). */
export function plKeySql(): string {
  const norm = "lowerUTF8(replaceRegexpAll(section, '[^A-Za-z0-9]', ''))";
  const arms = PL_KEYS.map((k) => `${norm} IN (${PL_SYNONYMS[k].map((s) => `'${s}'`).join(', ')}), '${k}'`);
  return `multiIf(\n    ${arms.join(',\n    ')},\n    '')`;
}

// ---------- filter bulan ----------

export const PNL_MAX_MONTHS = 24;
export const PNL_DEFAULT_MONTHS = 12;
const MONTH_RE = /^(\d{4})-(0[1-9]|1[0-2])$/;

export interface MonthRange {
  /** `YYYY-MM` untuk input bulan. */
  mfrom: string;
  mto: string;
  /** Tanggal 1 tiap ujung (parameter m_from / m_to). */
  from: string;
  to: string;
  /** Rentang yang sama setahun sebelumnya (parameter yoy_from / yoy_to). */
  ly: { from: string; to: string };
  /** Tanggal 1 setiap bulan dalam rentang, urut naik. */
  months: string[];
  /** Bulan berjalan (tanggal 1), berlabel "sementara". */
  current: string;
  /** true bila param ditolak/diganti default. */
  adjusted: boolean;
}

const one = (v: string | string[] | undefined): string | undefined => (Array.isArray(v) ? v[0] : v);

function monthsBetween(from: string, to: string): string[] {
  const out: string[] = [];
  for (let m = from; m <= to; m = startOfMonthShift(m, 1)) out.push(m);
  return out;
}

/**
 * `mfrom <= mto <= bulan ini`, rentang <= 24 bulan, bawaan 12 bulan terakhir s.d. bulan ini. Nilai tidak valid -> bawaan
 * (adjusted = true). Hanya salah satu param terisi -> param lain bawaan dari yang terisi bila sah, selain itu seluruhnya bawaan.
 */
export function parseMonthRange(raw: Record<string, string | string[] | undefined>, now: Date): MonthRange {
  const current = `${wibToday(now).slice(0, 7)}-01`;
  let to = current;
  let from = startOfMonthShift(current, -(PNL_DEFAULT_MONTHS - 1));
  let adjusted = false;
  const fIn = one(raw.mfrom);
  const tIn = one(raw.mto);
  if (fIn !== undefined || tIn !== undefined) {
    const f = typeof fIn === 'string' && MONTH_RE.test(fIn) ? `${fIn}-01` : null;
    const t = typeof tIn === 'string' && MONTH_RE.test(tIn) ? `${tIn}-01` : null;
    const toOk = t ?? current;
    const fromOk = f ?? startOfMonthShift(toOk, -(PNL_DEFAULT_MONTHS - 1));
    const n = monthsBetween(fromOk, toOk).length;
    if ((fIn !== undefined && f === null) || (tIn !== undefined && t === null) || fromOk > toOk || toOk > current || n > PNL_MAX_MONTHS) {
      adjusted = true;
    } else {
      from = fromOk;
      to = toOk;
    }
  }
  return {
    mfrom: from.slice(0, 7),
    mto: to.slice(0, 7),
    from,
    to,
    ly: { from: addYears(from, -1), to: addYears(to, -1) },
    months: monthsBetween(from, to),
    current,
    adjusted,
  };
}

// ---------- rasio dan KPI ----------

/** Persen a terhadap b; null bila b tidak positif atau salah satu bukan angka terhingga. */
export function pctOf(a: number, b: number): number | null {
  if (!Number.isFinite(a) || !Number.isFinite(b) || b <= 0) return null;
  return (a / b) * 100;
}

export interface PnlSide {
  net: number;
  gp: number;
  gpPct: number | null;
  opex: number;
  ebitda: number;
  ebitdaPct: number | null;
  np: number;
  npPct: number | null;
}

export interface PnlKpiRaw {
  net_sales: number;
  gross_profit: number;
  opex: number;
  ebitda: number;
  net_profit: number;
  net_sales_ly: number;
  gross_profit_ly: number;
  opex_ly: number;
  ebitda_ly: number;
  net_profit_ly: number;
}

const side = (net: number, gp: number, opex: number, ebitda: number, np: number): PnlSide => ({
  net,
  gp,
  gpPct: pctOf(gp, net),
  opex,
  ebitda,
  ebitdaPct: pctOf(ebitda, net),
  np,
  npPct: pctOf(np, net),
});

export function pnlSides(r: PnlKpiRaw): { cur: PnlSide; ly: PnlSide | null } {
  const cur = side(r.net_sales, r.gross_profit, r.opex, r.ebitda, r.net_profit);
  // Periode tahun lalu "tersedia" bila ada penjualan bersih (bukan nol) di sana; selain itu pembanding n/a.
  const lyHas = r.net_sales_ly !== 0 || r.net_profit_ly !== 0 || r.ebitda_ly !== 0;
  return { cur, ly: lyHas ? side(r.net_sales_ly, r.gross_profit_ly, r.opex_ly, r.ebitda_ly, r.net_profit_ly) : null };
}

/** Perubahan relatif (%), null bila pembanding nol/tidak ada. Pembanding negatif memakai |pembanding| agar tanda tetap bermakna. */
export function changePct(cur: number, prev: number | null): number | null {
  if (prev === null || prev === 0 || !Number.isFinite(prev) || !Number.isFinite(cur)) return null;
  return ((cur - prev) / Math.abs(prev)) * 100;
}

// ---------- tabel pos x bulan ----------

export interface StatementRaw {
  period_month: string;
  scope: string;
  section: string;
  amount_sum: number;
}

export const UNALLOCATED_LABEL = 'Tidak teralokasi ke outlet';
export const monthKey = (iso: string): string => `m_${iso.slice(0, 7).replace('-', '_')}`;

/**
 * Pos ringkasan x bulan. Nilai utama = jumlah cabang (scope `branch`). Bila ada baris total brand (filter cabang kosong),
 * setiap pos diikuti baris "Tidak teralokasi ke outlet" = brand - jumlah cabang, hanya bila selisihnya >= Rp1 di salah satu bulan.
 * Pos dikenal tampil lebih dulu (urutan PL_KEYS), pos lain menurut abjad. Kolom Total = jumlah semua bulan.
 */
export function buildStatement(rows: readonly StatementRaw[], months: readonly string[]): Record<string, string | number | null>[] {
  const branch = new Map<string, Map<string, number>>();
  const brand = new Map<string, Map<string, number>>();
  const put = (m: Map<string, Map<string, number>>, section: string, month: string, v: number): void => {
    let inner = m.get(section);
    if (!inner) {
      inner = new Map();
      m.set(section, inner);
    }
    inner.set(month, (inner.get(month) ?? 0) + v);
  };
  for (const r of rows) {
    const v = Number(r.amount_sum);
    if (!Number.isFinite(v)) continue;
    if (r.scope === 'brand') put(brand, r.section, r.period_month, v);
    else put(branch, r.section, r.period_month, v);
  }
  const rank = (s: string): number => {
    const k = plKeyOf(s);
    return k === null ? PL_KEYS.length : PL_KEYS.indexOf(k);
  };
  const sections = Array.from(new Set([...branch.keys(), ...brand.keys()])).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b, 'id'));
  const out: Record<string, string | number | null>[] = [];
  for (const s of sections) {
    const b = branch.get(s);
    const row: Record<string, string | number | null> = { label: s };
    let total = 0;
    let any = false;
    for (const m of months) {
      const v = b?.get(m);
      row[monthKey(m)] = v ?? null;
      if (v !== undefined) {
        total += v;
        any = true;
      }
    }
    row.total = any ? total : null;
    out.push(row);

    const br = brand.get(s);
    if (br) {
      const diff: Record<string, string | number | null> = { label: `${UNALLOCATED_LABEL}: ${s}` };
      let dTotal = 0;
      let material = false;
      for (const m of months) {
        const bv = br.get(m);
        if (bv === undefined) {
          diff[monthKey(m)] = null;
          continue;
        }
        const d = bv - (b?.get(m) ?? 0);
        diff[monthKey(m)] = d;
        dTotal += d;
        if (Math.abs(d) >= 1) material = true;
      }
      diff.total = dTotal;
      if (material) out.push(diff);
    }
  }
  return out;
}

export function statementColumns(months: readonly string[]): ColumnDef[] {
  return [
    { key: 'label', header: 'Pos', align: 'left' },
    ...months.map((m): ColumnDef => ({ key: monthKey(m), header: fmtMonth(m), fmt: 'idr', align: 'right' })),
    { key: 'total', header: 'Total periode', fmt: 'idr', align: 'right' },
  ];
}
