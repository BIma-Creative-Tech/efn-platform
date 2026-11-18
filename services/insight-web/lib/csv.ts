/**
 * Ekspor CSV (docs/04 §26.6, §26.13 C3).
 * - Sel teks yang diawali `=`, `+`, `-`, `@`, TAB, atau CR diberi awalan `'` (cegah formula injection;
 *   nama menu/cabang/produk berasal dari ESB = data eksternal).
 * - Angka ditulis mentah (titik desimal, tanpa pemisah ribuan); null -> sel kosong; BOM UTF-8; koma; CRLF.
 */
const FORMULA_START = /^[=+\-@\t\r]/;

export type CsvCell = string | number | boolean | null | undefined;

export function escapeCsvCell(v: CsvCell): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : '';
  if (typeof v === 'boolean') return v ? '1' : '0';
  let s = v;
  if (FORMULA_START.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export interface CsvColumn {
  key: string;
  header: string;
}

export function toCsv(columns: readonly CsvColumn[], rows: readonly Record<string, CsvCell>[]): string {
  const lines = [columns.map((c) => escapeCsvCell(c.header)).join(',')];
  for (const r of rows) lines.push(columns.map((c) => escapeCsvCell(r[c.key])).join(','));
  return `﻿${lines.join('\r\n')}\r\n`;
}
