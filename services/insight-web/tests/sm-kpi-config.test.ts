/**
 * Parity default KPI supermarket (docs/04 §28.13.8c, L-200): `lib/sm-defaults.ts` harus sama dengan tabel kontrak dan dengan
 * `docs/contracts/insight_sm_kpi_defaults.json` (normatif, dibuat builder Odoo/agent data) bila file itu ada di checkout.
 * Bentuk JSON yang diterima: {key: nilai} | {defaults: {key: nilai}} | [{key, default|value}] | {keys: [...]}.
 * Bila file belum ada, test parity DILEWATI dan lead wajib menjalankannya setelah file masuk (dicatat di laporan).
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { SM_KPI_DEFAULTS, SM_KPI_ENUMS, SM_KPI_RANGES } from '@/lib/sm-defaults';

const here = dirname(fileURLToPath(import.meta.url));
const JSON_PATH = resolve(here, '../../../docs/contracts/insight_sm_kpi_defaults.json');

describe('konsistensi internal default', () => {
  it('39 kunci kontrak; setiap kunci punya pilihan enum, rentang, atau bool; default berada di dalamnya', () => {
    const keys = Object.keys(SM_KPI_DEFAULTS);
    expect(keys).toHaveLength(39);
    for (const k of keys) {
      const v = SM_KPI_DEFAULTS[k];
      const e = SM_KPI_ENUMS[k];
      const r = SM_KPI_RANGES[k];
      expect(e !== undefined || r !== undefined, k).toBe(true);
      if (e) expect(e).toContain(v);
      if (r) {
        expect(typeof v, k).toBe('number');
        expect(v as number, k).toBeGreaterThanOrEqual(r[0]);
        expect(v as number, k).toBeLessThanOrEqual(r[1]);
      }
    }
  });
  it('nilai default pemilik proyek 20 Nov 2026 (L-200/L-201)', () => {
    expect(SM_KPI_DEFAULTS['gmroi.period']).toBe('month');
    expect(SM_KPI_DEFAULTS['oos.universe']).toBe('orderpoint');
    expect(SM_KPI_DEFAULTS['critical.rule']).toBe('min_or_cover');
    expect(SM_KPI_DEFAULTS['critical.cover_days']).toBe(2);
    expect(SM_KPI_DEFAULTS['fill.basis']).toBe('shipped');
    expect(SM_KPI_DEFAULTS['waste.basis']).toBe('sales_excl_tax');
    expect(SM_KPI_DEFAULTS['fresh.stock.warn_min']).toBe(75);
    expect(SM_KPI_DEFAULTS['fresh.stock.stale_min']).toBe(135);
  });
});

function normalize(raw: unknown): Record<string, unknown> {
  if (Array.isArray(raw)) {
    const out: Record<string, unknown> = {};
    for (const it of raw as Record<string, unknown>[]) out[String(it.key)] = 'default' in it ? it.default : it.value;
    return out;
  }
  const o = raw as Record<string, unknown>;
  if (o && typeof o === 'object') {
    for (const wrap of ['defaults', 'keys', 'kpi']) {
      if (wrap in o) return normalize(o[wrap]);
    }
  }
  return o;
}

describe.skipIf(!existsSync(JSON_PATH))('parity dengan docs/contracts/insight_sm_kpi_defaults.json', () => {
  it('kunci dan nilai identik', () => {
    const normalized = normalize(JSON.parse(readFileSync(JSON_PATH, 'utf8')));
    const coerce = (v: unknown): unknown => (typeof v === 'boolean' ? (v ? 1 : 0) : typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v) ? Number(v) : v);
    const fromJson = Object.fromEntries(Object.entries(normalized).map(([k, v]) => [k, coerce(v)]));
    expect(Object.keys(fromJson).sort()).toEqual(Object.keys(SM_KPI_DEFAULTS).sort());
    expect(fromJson).toEqual({ ...SM_KPI_DEFAULTS });
  });
});
