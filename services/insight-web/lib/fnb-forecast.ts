/**
 * Forecast F&B (ESB) - fungsi murni (tanpa I/O). Sumber: view `efn_insight.v_esb_forecast_daily` (run forecast OK terbaru per PT).
 *
 * Gudang pusat: baris gudang DC adalah AGREGAT semua outlet (aggregate_dc worker). Kodenya diatur lewat env
 * `INSIGHT_FNB_DC_CODES` (dipisah koma). Baris DC TIDAK ikut total/grafik/top produk (menghindari hitung ganda); di tabel
 * per cabang DC diberi label sendiri.
 */

/** Format kode gudang yang sah; entri lain di env diabaikan. */
export const DC_CODE_RE = /^[A-Za-z0-9_-]{1,16}$/;

export const DC_LABEL = 'Gudang pusat (agregat outlet)';

/** Parse `INSIGHT_FNB_DC_CODES`: pisah koma, trim, buang yang tidak valid dan duplikat. Kosong/undefined -> []. */
export function parseDcCodes(raw: string | undefined | null): string[] {
  if (!raw) return [];
  const out: string[] = [];
  for (const part of raw.split(',')) {
    const c = part.trim();
    if (DC_CODE_RE.test(c) && !out.includes(c)) out.push(c);
  }
  return out;
}

export function isDcCode(code: unknown, dcCodes: readonly string[]): boolean {
  return typeof code === 'string' && dcCodes.includes(code.trim());
}

export interface WhRow {
  wh_code?: unknown;
  yhat_sum?: unknown;
}

/** Pisahkan baris per gudang menjadi outlet (ikut total) dan DC (ditampilkan terpisah, tidak dijumlah). */
export function splitDc<T extends WhRow>(rows: readonly T[], dcCodes: readonly string[]): { outlets: T[]; dc: T[] } {
  const outlets: T[] = [];
  const dc: T[] = [];
  for (const r of rows) (isDcCode(r.wh_code, dcCodes) ? dc : outlets).push(r);
  return { outlets, dc };
}

/** Jumlah nilai numerik `yhat_sum` baris NON-DC saja. */
export function sumNonDc<T extends WhRow>(rows: readonly T[], dcCodes: readonly string[], toNum: (v: unknown) => number): number {
  return splitDc(rows, dcCodes).outlets.reduce((s, r) => s + toNum(r.yhat_sum), 0);
}
