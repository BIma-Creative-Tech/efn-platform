/**
 * Logika murni halaman supermarket: pengurai view, empat keadaan "tidak ada angka", kesegaran, format (Rupiah, hitung mundur,
 * k-anonim), KPI/status, filter, tautan Odoo, view-model. Tanpa PII.
 */
import { describe, expect, it } from 'vitest';
import { fmtIdr, fmtIdrShort, fmtPct } from '@/lib/format';
import { odooListUrl, odooRecordUrl } from '@/lib/odoo-links';
import { asOfText, emptyText, freshnessOf, martOff, resolveCardState, staleBanner, worstLevel } from '@/lib/sm-freshness';
import { addYears, effectiveWh, effectiveWhz, firstOfMonthMinus, parseSmFilters, storeWindow } from '@/lib/sm-filters';
import { fmtAgeMin, fmtClockWib, fmtCountdown, fmtDaysLeft, fmtDeliveryDate, fmtRatioX, fmtSuppressed, fmtStampWib, wibDateOf } from '@/lib/sm-format';
import {
  computeGmroi,
  cutoffInfo,
  fillRate,
  fillStatus,
  marginPct,
  oosRate,
  oosStatus,
  poLateStatus,
  priceStatus,
  wapeBias,
  wasteStatus,
} from '@/lib/sm-kpi';
import {
  cleanText,
  parseChDate,
  parseChDateTime,
  parseCritical,
  parseExpiryLots,
  parseFreshness,
  parsePurchaseOrders,
  parseProposals,
  toNum,
  toNumOrNull,
} from '@/lib/sm-parse';
import { buildSmConfig, buildThresholds, DEFAULT_SM_CONFIG, cfgNum } from '@/lib/sm-thresholds';
import { buildFunnel, buildOosHeat, buildOutletRows, buildWasteVm, campaignTotals, expiryDelta, nextDeliveryGroup, summarizeExpiry, wasteBase } from '@/lib/sm-vm';

const NOW = new Date('2026-11-21T07:45:00Z'); // 14:45 WIB
const T = buildThresholds(DEFAULT_SM_CONFIG);

describe('format Rupiah (basis uang L-202) dan waktu WIB', () => {
  it('Rupiah id-ID', () => {
    expect(fmtIdr(54000)).toBe('Rp 54.000');
    expect(fmtIdr(-1250)).toBe('-Rp 1.250');
    expect(fmtIdrShort(4_200_000_000)).toBe('Rp 4,2 M');
    expect(fmtIdrShort(1_800_000)).toBe('Rp 1,8 jt');
    expect(fmtPct(94.2)).toBe('94,2%');
  });
  it('jam WIB dengan titik dua; tanggal WIB', () => {
    expect(fmtClockWib(NOW)).toBe('14:45 WIB');
    expect(wibDateOf(new Date('2026-11-20T18:00:00Z'))).toBe('2026-11-21'); // lewat tengah malam WIB
    expect(fmtStampWib(new Date('2026-11-20T01:05:00Z'), NOW)).toBe('20 Nov 08:05 WIB');
    expect(fmtStampWib(NOW, NOW)).toBe('14:45 WIB');
    expect(fmtDeliveryDate('2026-11-26')).toBe('Kam, 26 Nov 2026');
  });
  it('hitung mundur, umur, sisa umur lot, GMROI, k-anonim', () => {
    expect(fmtCountdown((3 * 60 + 15) * 60_000)).toBe('tersisa 3 j 15 m');
    expect(fmtCountdown(42 * 60_000)).toBe('tersisa 42 m');
    expect(fmtCountdown(30_000)).toBe('tersisa < 1 m');
    expect(fmtCountdown(-12 * 60_000)).toBe('batas lewat 12 m');
    expect(fmtCountdown(-90 * 60_000)).toBe('batas lewat 1 j 30 m');
    expect(fmtAgeMin(12)).toBe('12 menit lalu');
    expect(fmtAgeMin(185)).toBe('3 jam lalu');
    expect(fmtAgeMin(0.2)).toBe('baru saja');
    expect(fmtDaysLeft(0)).toBe('ED hari ini');
    expect(fmtDaysLeft(3)).toBe('sisa 3 hari');
    expect(fmtDaysLeft(-2)).toBe('lewat 2 hari');
    expect(fmtRatioX(2.84)).toBe('2,8x');
    expect(fmtSuppressed(9)).toBe('< 10');
    expect(fmtSuppressed(0)).toBe('< 10');
    expect(fmtSuppressed(12345)).toBe('12.345');
    expect(fmtSuppressed(null)).toBe('-');
  });
});

describe('pengurai view', () => {
  it('waktu ClickHouse = UTC; NULL/tanggal nol/format salah -> null', () => {
    expect(parseChDateTime('2026-11-21 07:45:00')?.toISOString()).toBe('2026-11-21T07:45:00.000Z');
    expect(parseChDateTime('2026-11-21 07:45:00.123456')?.toISOString()).toBe('2026-11-21T07:45:00.000Z');
    expect(parseChDateTime('1970-01-01 00:00:00')).toBeNull();
    expect(parseChDateTime('0000-00-00 00:00:00')).toBeNull();
    expect(parseChDateTime('2026-02-30 00:00:00')).toBeNull();
    expect(parseChDateTime(null)).toBeNull();
    expect(parseChDate('2026-11-21')).toBe('2026-11-21');
    expect(parseChDate('1970-01-01')).toBeNull();
    expect(parseChDate('21/11/2026')).toBeNull();
  });
  it('angka: string dari JSON ClickHouse; null tetap null', () => {
    expect(toNum('12.5')).toBe(12.5);
    expect(toNum(null)).toBe(0);
    expect(toNumOrNull(null)).toBeNull();
    expect(toNumOrNull('abc')).toBeNull();
  });
  it('cleanText membuang kontrol dan memotong', () => {
    expect(cleanText('Su\u0007su  UHT\n1 L')).toBe('Susu UHT 1 L');
    expect(cleanText('x'.repeat(300), 10)).toHaveLength(10);
  });
  it('freshness: kelas tak dikenal dibuang', () => {
    const rows = parseFreshness([
      { mart: 'stock', freshness_class: 'stock', data_as_of: '2026-11-21 07:00:00', source_as_of: '2026-11-21 06:00:00', last_run_at: '2026-11-21 07:00:00', last_status: 'ok' },
      { mart: 'x', freshness_class: 'weekly' },
      { freshness_class: 'daily' },
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].sourceAsOf?.toISOString()).toBe('2026-11-21T06:00:00.000Z');
  });
  it('usulan: state asing -> unknown; baris tanpa kunci dibuang', () => {
    const r = parseProposals([
      { proposal_id: 5, warehouse_id: 22, delivery_date: '2026-11-22', cutoff_at: '2026-11-21 08:00:00', state: 'proposed', line_count: '48', lines_changed: '12' },
      { proposal_id: 6, warehouse_id: 22, delivery_date: '2026-11-23', state: 'weird' },
      { proposal_id: 0, warehouse_id: 22, delivery_date: '2026-11-23' },
      { proposal_id: 7, warehouse_id: 22, delivery_date: 'bukan' },
    ]);
    expect(r.map((x) => x.state)).toEqual(['proposed', 'unknown']);
    expect(r[0].lineCount).toBe(48);
  });
  it('lot near-expiry: display_status asing -> pantau; markdown kosong -> null', () => {
    const [a, b] = parseExpiryLots([
      { alert_id: 1, warehouse_id: 22, days_left: '-1', qty: '2', value_cost: '54000', display_status: 'kedaluwarsa', product_name: 'Susu UHT 1 L', markdown_id: 0 },
      { alert_id: 2, warehouse_id: 22, days_left: 3, display_status: 'aneh', markdown_id: 9, markdown_effective_pct: '30', markdown_date_to: '2026-11-24' },
    ]);
    expect(a.display).toBe('kedaluwarsa');
    expect(a.daysLeft).toBe(-1);
    expect(a.markdownId).toBeNull();
    expect(b.display).toBe('pantau');
    expect(b.markdownId).toBe(9);
    expect(b.markdownPct).toBe(30);
  });
  it('stok kritis dan PO (supplier perorangan = "Supplier #id")', () => {
    const [c] = parseCritical([{ warehouse_id: 22, product_id: 8, product_name: 'Telur 10 btr', on_hand_qty: 0, min_qty: 24, is_out: 1, is_critical: 1, open_proposal_id: 0 }]);
    expect(c.isOut).toBe(true);
    expect(c.openProposalId).toBeNull();
    const [p] = parsePurchaseOrders([{ order_id: 3, supplier_key: 77, supplier_label: '', state_key: 'purchase', late_flag: 1, value_sum: '1000' }]);
    expect(p.supplier).toBe('Supplier #77');
    expect(p.late).toBe(true);
  });
});

describe('kesegaran dan empat keadaan "tidak ada angka"', () => {
  const mk = (over: Record<string, unknown> = {}) => {
    const [f] = parseFreshness([
      { mart: 'replenishment', freshness_class: 'near_live', data_as_of: '2026-11-21 07:33:00', source_as_of: null, last_run_at: '2026-11-21 07:33:00', last_status: 'ok', ...over },
    ]);
    return f;
  };
  it('near_live: ≤30 baik, 30-60 perlu dicek, >60 usang (ambang dari konfigurasi)', () => {
    expect(freshnessOf(mk(), NOW, T).level).toBe('good'); // 12 menit
    expect(freshnessOf(mk({ data_as_of: '2026-11-21 07:00:00' }), NOW, T).level).toBe('warn'); // 45
    expect(freshnessOf(mk({ data_as_of: '2026-11-21 06:00:00' }), NOW, T).level).toBe('bad'); // 105
    const strict = buildThresholds(buildSmConfig([{ company_id: 1, key: 'fresh.near_live.warn_min', value_num: 5, value_str: '', is_default: 0, effective_since: null }]));
    expect(freshnessOf(mk(), NOW, strict).level).toBe('warn');
  });
  it('data_as_of kosong = tidak diketahui, bukan baik; mart tak ada = tidak diketahui', () => {
    expect(freshnessOf(mk({ data_as_of: null }), NOW, T).level).toBe('unknown');
    expect(freshnessOf(undefined, NOW, T).level).toBe('unknown');
    expect(asOfText(freshnessOf(undefined, NOW, T), NOW)).toBe('Kesegaran data tidak diketahui');
    expect(asOfText(freshnessOf(mk(), NOW, T), NOW)).toBe('Data per 14:33 WIB (12 menit lalu)');
  });
  it('stok memakai waktu tertua antara data_as_of dan source_as_of', () => {
    const f = mk({ mart: 'stock', freshness_class: 'stock', data_as_of: '2026-11-21 07:40:00', source_as_of: '2026-11-21 05:00:00' });
    expect(freshnessOf(f, NOW, T).level).toBe('bad'); // 165 menit > 135
  });
  it('kelas daily: status job bukan ok = Bermasalah walau data baru', () => {
    const f = mk({ mart: 'waste', freshness_class: 'daily', last_status: 'error' });
    expect(freshnessOf(f, NOW, T).level).toBe('bad');
    expect(freshnessOf(mk({ mart: 'waste', freshness_class: 'daily' }), NOW, T).level).toBe('good');
  });
  it('worstLevel dan spanduk', () => {
    expect(worstLevel(['good', 'warn', 'unknown'])).toBe('unknown');
    expect(worstLevel(['good', 'bad', 'unknown'])).toBe('bad');
    expect(worstLevel([])).toBe('good');
    expect(staleBanner('good')).toBeNull();
    expect(staleBanner('warn')).toContain('cek di Odoo');
  });
  it('empat keadaan berbeda: fitur belum aktif, tidak diketahui, nol sungguhan, belum ada data, usang', () => {
    const fresh = mk();
    expect(martOff(undefined)).toBe(true);
    expect(martOff(mk({ data_as_of: null, last_run_at: null }))).toBe(true);
    expect(resolveCardState({ fresh: undefined, level: 'unknown', hasRows: false, zeroMeans: 'zero' })).toEqual({ kind: 'empty', empty: 'feature-off', level: 'unknown' });
    expect(resolveCardState({ fresh: mk({ data_as_of: null, last_run_at: null }), level: 'unknown', hasRows: true, zeroMeans: 'zero' })).toMatchObject({ empty: 'feature-off' });
    expect(resolveCardState({ fresh: mk({ data_as_of: null }), level: 'unknown', hasRows: false, zeroMeans: 'zero' })).toEqual({ kind: 'unknown' });
    expect(resolveCardState({ fresh, level: 'good', hasRows: false, zeroMeans: 'zero' })).toMatchObject({ empty: 'zero' });
    expect(resolveCardState({ fresh, level: 'good', hasRows: false, zeroMeans: 'no-data' })).toMatchObject({ empty: 'no-data' });
    expect(resolveCardState({ fresh, level: 'bad', hasRows: false, zeroMeans: 'zero' })).toMatchObject({ empty: 'stale' }); // jangan nol
    expect(resolveCardState({ fresh, level: 'bad', hasRows: true, zeroMeans: 'zero' })).toEqual({ kind: 'ready', level: 'bad' });
    expect(resolveCardState({ fresh, level: 'warn', hasRows: true, zeroMeans: 'zero' })).toEqual({ kind: 'ready', level: 'warn' });
  });
  it('teks keadaan baku berbeda satu sama lain dan ikon sesuai spesifikasi', () => {
    const texts = { zero: 'Tidak ada barang mendekati kedaluwarsa.', off: 'Pemantauan kedaluwarsa belum diaktifkan untuk outlet ini.' };
    const all = (['zero', 'no-data', 'feature-off', 'stale'] as const).map((k) => emptyText(k, texts));
    expect(new Set(all.map((a) => a.title)).size).toBe(4);
    expect(all.map((a) => a.icon)).toEqual(['check', 'clock', 'info', 'warn']);
    expect(emptyText('no-data', { ...texts, noData: 'Tidak ada usulan.' }).title).toBe('Tidak ada usulan.');
  });
});

describe('KPI dan status', () => {
  it('rasio dari jumlah mentah; penyebut 0 -> null (bukan 0)', () => {
    expect(oosRate(34, 1000)).toBeCloseTo(3.4);
    expect(oosRate(0, 0)).toBeNull();
    expect(marginPct(24.1, 100)).toBeCloseTo(24.1);
    expect(marginPct(5, 0)).toBeNull();
    expect(wapeBias(20, 100, 110)).toEqual({ wape: 20, bias: 10 });
    expect(wapeBias(0, 0, 5)).toEqual({ wape: null, bias: null });
  });
  it('margin dari omzet tanpa PPN, bukan termasuk PPN (L-202)', () => {
    const net = 1000;
    const incl = 1110;
    const gm = 240;
    expect(marginPct(gm, net)).toBeCloseTo(24);
    expect(marginPct(gm, incl) as number).toBeLessThan(22); // salah-basis: turun semu ~21,6%
  });
  it('status ambang default 2/5, 95/90, 25/40, 5/10, PO 1/3', () => {
    expect(oosStatus(1.9, T)).toBe('good');
    expect(oosStatus(3, T)).toBe('warn');
    expect(oosStatus(5.1, T)).toBe('bad');
    expect(oosStatus(null, T)).toBe('neutral');
    expect(wasteStatus(4.1, T)).toBe('bad');
    expect(fillStatus(95, T)).toBe('good');
    expect(fillStatus(92, T)).toBe('warn');
    expect(fillStatus(89.9, T)).toBe('bad');
    expect(poLateStatus(0, T)).toBe('good');
    expect(poLateStatus(1, T)).toBe('warn');
    expect(poLateStatus(3, T)).toBe('bad');
  });
  it('harga vs pasar: sampel minimum, di atas/di bawah, selalu berlabel kata', () => {
    expect(priceStatus(25, 2, T)).toEqual({ status: 'neutral', label: 'Sampel belum cukup' });
    expect(priceStatus(25, 5, T).status).toBe('bad');
    expect(priceStatus(12, 5, T)).toEqual({ status: 'warn', label: 'Di atas pasar' });
    expect(priceStatus(-15, 5, T).label).toBe('Di bawah pasar');
    expect(priceStatus(3, 5, T).status).toBe('good');
  });
  it('GMROI: bulan lengkap terakhir, DC ikut/tidak, kuartal, anualisasi, tanpa persediaan -> null', () => {
    const rows = [
      { month: '2026-09-01', outletType: 'outlet', gm: 100, stockDaily: 400, stockMonthEnd: 500, days: 30 },
      { month: '2026-10-01', outletType: 'outlet', gm: 120, stockDaily: 400, stockMonthEnd: 500, days: 31 },
      { month: '2026-10-01', outletType: 'dc', gm: 0, stockDaily: 200, stockMonthEnd: 200, days: 31 },
      { month: '2026-11-01', outletType: 'outlet', gm: 50, stockDaily: 300, stockMonthEnd: 300, days: 30 }, // belum lengkap
    ];
    const base = { period: 'month' as const, stockAvg: 'daily' as const, includeDc: true, annualize: false };
    const m = computeGmroi(rows, base, '2026-11-20');
    expect(m.lastMonth).toBe('2026-10-01');
    expect(m.value).toBeCloseTo(120 / 600);
    expect(computeGmroi(rows, { ...base, includeDc: false }, '2026-11-20').value).toBeCloseTo(120 / 400);
    expect(computeGmroi(rows, { ...base, stockAvg: 'month_end', includeDc: false }, '2026-11-20').value).toBeCloseTo(120 / 500);
    const q = computeGmroi(rows, { ...base, includeDc: false, period: 'quarter' }, '2026-11-20');
    expect(q.months).toBe(2);
    expect(q.value).toBeCloseTo(220 / 400);
    expect(computeGmroi(rows, { ...base, includeDc: false, annualize: true }, '2026-11-20').value).toBeCloseTo((120 / 400) * (365 / 31));
    expect(computeGmroi(rows, base, '2026-10-31').lastMonth).toBe('2026-10-01'); // 31 Okt = bulan lengkap
    expect(computeGmroi(rows, base, '2026-10-30').lastMonth).toBe('2026-09-01');
    expect(computeGmroi([], base, '2026-11-20')).toEqual({ value: null, lastMonth: null, months: 0 });
    expect(computeGmroi([{ ...rows[0], stockDaily: 0 }], base, '2026-11-20').value).toBeNull();
  });
  it('fill rate: qty vs baris', () => {
    const s = { sumLeast: 94, sumApproved: 100, linesFull: 8, lines: 10 };
    expect(fillRate(s, 'qty')).toBe(94);
    expect(fillRate(s, 'line')).toBe(80);
    expect(fillRate({ sumLeast: 0, sumApproved: 0, linesFull: 0, lines: 0 }, 'qty')).toBeNull();
  });
  it('usulan order: status & hitung mundur (ST-1)', () => {
    const cutoff = new Date(NOW.getTime() + 195 * 60_000); // 3 j 15 m
    expect(cutoffInfo('approved', cutoff, NOW, T, false)).toMatchObject({ status: 'good', countdown: 'tersisa 3 j 15 m' });
    expect(cutoffInfo('proposed', cutoff, NOW, T, false).status).toBe('warn');
    const soon = new Date(NOW.getTime() + 60 * 60_000);
    expect(cutoffInfo('proposed', soon, NOW, T, false)).toMatchObject({ status: 'bad', label: 'Segera: Diusulkan (belum disetujui)' });
    expect(cutoffInfo('expired', soon, NOW, T, false).label).toContain('tidak dikirim otomatis');
    expect(cutoffInfo('rejected', soon, NOW, T, false).status).toBe('warn');
    // data usang: tidak boleh menyatakan status
    expect(cutoffInfo('approved', soon, NOW, T, true)).toMatchObject({ status: 'unknown', label: 'Tidak pasti', uncertain: true });
    expect(cutoffInfo('proposed', null, NOW, T, false).countdown).toBe('');
  });
});

describe('konfigurasi KPI (v_sm_kpi_config)', () => {
  const row = (key: string, num: number | null, str = '', company = 1) => ({ company_id: company, key, value_num: num, value_str: str, is_default: 0, effective_since: '2026-11-20 03:00:00' });
  it('tanpa baris = default; fallback=true', () => {
    expect(DEFAULT_SM_CONFIG.fallback).toBe(true);
    expect(cfgNum(DEFAULT_SM_CONFIG, 'thr.oos.bad_pct')).toBe(5);
  });
  it('nilai sah menimpa, effective_since terbaru dibawa', () => {
    const c = buildSmConfig([row('thr.oos.warn_pct', 3), row('fill.basis', null, 'received')]);
    expect(buildThresholds(c).oosWarn).toBe(3);
    expect(c.values['fill.basis']).toBe('received');
    expect(c.fallback).toBe(false);
    expect(c.effectiveSince).toBe('2026-11-20 03:00:00');
  });
  it('di luar rentang/pilihan -> default; pasangan tidak konsisten -> default keduanya', () => {
    const c = buildSmConfig([row('thr.oos.warn_pct', 500), row('fill.basis', null, 'sembarang'), row('thr.waste.warn_pct', 6), row('thr.waste.bad_pct', 3)]);
    expect(c.values['thr.oos.warn_pct']).toBe(2);
    expect(c.values['fill.basis']).toBe('shipped');
    expect(c.values['thr.waste.warn_pct']).toBe(2);
    expect(c.values['thr.waste.bad_pct']).toBe(4);
  });
  it('fill: warn harus < good', () => {
    const c = buildSmConfig([row('thr.fill.warn_pct', 99), row('thr.fill.good_pct', 95)]);
    expect(c.values['thr.fill.warn_pct']).toBe(90);
  });
  it('beberapa PT SM: fresh.* paling ketat (terkecil), lainnya company ber-id terkecil', () => {
    const c = buildSmConfig([row('fresh.daily.warn_min', 1000, '', 12), row('fresh.daily.warn_min', 600, '', 11), row('thr.oos.warn_pct', 3, '', 12), row('thr.oos.warn_pct', 1, '', 11)]);
    expect(c.values['fresh.daily.warn_min']).toBe(600);
    expect(c.values['thr.oos.warn_pct']).toBe(1);
  });
  it('k-anonim tetap 10 dan tidak dapat diubah dari konfigurasi', () => {
    expect(buildThresholds(buildSmConfig([row('k_anonymity', 2)])).kAnonymity).toBe(10);
  });
});

describe('filter halaman SM', () => {
  const session = { wids: [0, 20, 21, 22], cats: [4, 9] };
  const parse = (raw: Record<string, string | string[]>, days = 30) => parseSmFilters(raw, session, NOW, { defaultDays: days });
  it('bawaan: 30 hari s.d. kemarin WIB; pembanding sama panjang; cats sesi jadi default kategori', () => {
    const { filters, adjusted } = parse({});
    expect(filters.to).toBe('2026-11-20');
    expect(filters.from).toBe('2026-10-22');
    expect(filters.prev).toEqual({ from: '2026-09-22', to: '2026-10-21' });
    expect(filters.cat).toEqual([4, 9]);
    expect(filters.catDefaulted).toBe(true);
    expect(adjusted).toBe(false);
  });
  it('wh hanya ⊆ wids tanpa 0; selebihnya dibuang + adjusted', () => {
    const r = parse({ wh: '21,22,99,0,abc' });
    expect(r.filters.wh).toEqual([21, 22]);
    expect(r.adjusted).toBe(true);
    expect(effectiveWh(parse({}).filters, session)).toEqual([20, 21, 22]);
    expect(effectiveWhz(parse({}).filters, session)).toEqual([0, 20, 21, 22]);
  });
  it('cat eksplisit kosong = semua; cat sah menimpa default', () => {
    expect(parse({ cat: '' }).filters.cat).toEqual([]);
    expect(parse({ cat: ['', '7'] }).filters.cat).toEqual([7]);
    expect(parse({ cat: '7,x' }).adjusted).toBe(true);
  });
  it('tanggal tidak sah / to > kemarin / rentang > 366 hari -> bawaan + adjusted', () => {
    for (const raw of <Record<string, string>[]>[{ from: '2026-11-01', to: '2026-11-21' }, { from: '2025-01-01', to: '2026-11-20' }, { from: 'x', to: 'y' }, { from: '2026-11-10' }]) {
      const r = parse(raw);
      expect(r.adjusted).toBe(true);
      expect(r.filters.to).toBe('2026-11-20');
    }
    expect(parse({ from: '2026-11-01', to: '2026-11-10' }).filters.from).toBe('2026-11-01');
  });
  it('YoY, awal jendela GMROI, jendela halaman toko', () => {
    expect(addYears('2028-02-29', -1)).toBe('2027-02-28');
    expect(addYears('2026-11-20', -1)).toBe('2025-11-20');
    expect(firstOfMonthMinus('2026-11-20', 11)).toBe('2025-12-01');
    expect(storeWindow(NOW)).toEqual({ from: '2026-11-16', to: '2026-11-21', prev: { from: '2026-11-09', to: '2026-11-14' } }); // Sabtu 21 Nov
  });
});

describe('tautan Odoo (pola action Odoo 19)', () => {
  const O = 'https://erp.test:9443';
  it('rekaman dan daftar memakai action-<xmlid>', () => {
    expect(odooRecordUrl(O, 'efn.replenishment.proposal', 5)).toBe(`${O}/odoo/action-efn_replenishment.efn_replenishment_proposal_action/5`);
    expect(odooRecordUrl(O, 'purchase.order', 3)).toBe(`${O}/odoo/action-purchase.purchase_rfq/3`);
    expect(odooRecordUrl(O, 'stock.lot', 9)).toBe(`${O}/odoo/action-stock.action_production_lot_form/9`);
    expect(odooListUrl(O, 'stock.scrap')).toBe(`${O}/odoo/action-stock.action_stock_scrap`);
  });
  it('null bila origin/model/id tidak sah (tanpa data selain id)', () => {
    expect(odooRecordUrl(null, 'stock.lot', 1)).toBeNull();
    expect(odooRecordUrl('javascript:alert(1)', 'stock.lot', 1)).toBeNull();
    expect(odooRecordUrl(`${O}/path`, 'stock.lot', 1)).toBeNull();
    expect(odooRecordUrl(O, 'res.users', 1)).toBeNull();
    expect(odooRecordUrl(O, 'stock.lot', 0)).toBeNull();
    expect(odooRecordUrl(O, 'stock.lot', null)).toBeNull();
    expect(odooRecordUrl(O, 'stock.lot', 1.5)).toBeNull();
    expect(odooListUrl(O, '__proto__')).toBeNull();
  });
});

describe('view-model', () => {
  it('usulan terdekat dan kelompok pengiriman DC-2', () => {
    const rows = parseProposals([
      { proposal_id: 1, warehouse_id: 21, delivery_date: '2026-11-22', state: 'approved' },
      { proposal_id: 2, warehouse_id: 22, delivery_date: '2026-11-22', state: 'proposed' },
      { proposal_id: 3, warehouse_id: 22, delivery_date: '2026-11-24', state: 'draft' },
    ]);
    expect(nextDeliveryGroup(rows, '2026-11-21')).toMatchObject({ date: '2026-11-22' });
    expect(nextDeliveryGroup(rows, '2026-11-21').rows).toHaveLength(2);
    expect(nextDeliveryGroup(rows, '2026-11-25')).toEqual({ date: null, rows: [] });
  });
  it('ringkasan near-expiry dan selisih kemarin (riwayat belum ada -> null)', () => {
    const lots = parseExpiryLots([
      { alert_id: 1, warehouse_id: 1, days_left: 0, value_cost: 100, display_status: 'kedaluwarsa' },
      { alert_id: 2, warehouse_id: 1, days_left: 2, value_cost: 50, display_status: 'markdown_aktif' },
    ]);
    expect(summarizeExpiry(lots)).toMatchObject({ total: 2, valueCost: 150, counts: { kedaluwarsa: 1, markdown_aktif: 1 } });
    expect(expiryDelta([{ day: '2026-11-21', lots_n: 5 }, { day: '2026-11-20', lots_n: 3 }], '2026-11-21', '2026-11-20').delta).toBe(2);
    expect(expiryDelta([{ day: '2026-11-21', lots_n: 5 }], '2026-11-21', '2026-11-20').delta).toBeNull();
  });
  it('waste: persentase dari omzet tanpa PPN; basis HPP tidak tersedia untuk kepala toko', () => {
    const base = wasteBase('sales_excl_tax', false, { net_cur: 1000, net_prev: 800 }, undefined);
    const w = buildWasteVm({ waste_cur: 21, waste_prev: 8 }, base, [{ reason_name: 'Kedaluwarsa', value_sum: 13 }, { reason_name: 'Rusak', value_sum: 8 }]);
    expect(w.pctCur).toBeCloseTo(2.1);
    expect(w.pctPrev).toBeCloseTo(1);
    expect(w.reasons[0]).toMatchObject({ name: 'Kedaluwarsa' });
    expect(w.reasons[0].sharePct).toBeCloseTo(61.9, 0);
    expect(wasteBase('cogs', false, { net_cur: 1 }, { cogs_cur: 5 })).toEqual({ cur: null, prev: null });
    expect(wasteBase('cogs', true, { net_cur: 1 }, { cogs_cur: 5, cogs_prev: 4 })).toEqual({ cur: 5, prev: 4 });
    expect(buildWasteVm({ waste_cur: 5 }, { cur: null, prev: null }, []).pctCur).toBeNull();
  });
  it('perbandingan outlet: peringkat omzet, margin dari net tanpa PPN, status dari waste/penurunan omzet', () => {
    const names = new Map([[21, 'Kelapa Gading'], [22, 'Cibubur']]);
    const rows = buildOutletRows(
      [{ warehouse_id: 21, rev_cur: 1110, rev_prev: 1000, rev_yoy: 0, net_cur: 1000 }, { warehouse_id: 22, rev_cur: 900, rev_prev: 1000, rev_yoy: 800, net_cur: 810 }],
      [{ warehouse_id: 21, gm_cur: 240, net_cur: 1000 }],
      [{ warehouse_id: 22, waste_cur: 40 }],
      names,
      T,
    );
    expect(rows.map((r) => r.name)).toEqual(['Kelapa Gading', 'Cibubur']);
    expect(rows[0].marginPct).toBeCloseTo(24);
    expect(rows[0].yoyPct).toBeNull(); // tanpa pembanding tahun lalu
    expect(rows[1].wastePct).toBeCloseTo((40 / 810) * 100);
    expect(rows[1].status).toBe('bad'); // omzet turun 10% (Perlu dicek) + waste 4,9% (Bermasalah) -> terburuk
  });
  it('heatmap OOS: n/a untuk sel tanpa universe, urut kategori terburuk', () => {
    const h = buildOosHeat(
      [{ categ_id: 1, warehouse_id: 21, oos_sum: 5, uni_sum: 50 }, { categ_id: 2, warehouse_id: 21, oos_sum: 1, uni_sum: 50 }, { categ_id: 2, warehouse_id: 22, oos_sum: 0, uni_sum: 10 }],
      new Map([[1, 'Susu'], [2, 'Roti']]),
      new Map([[21, 'KG'], [22, 'CB']]),
    );
    expect(h.rows).toEqual(['Susu', 'Roti']);
    expect(h.cols).toEqual(['KG', 'CB']);
    expect(h.cells.find((c) => c[0] === 1 && c[1] === 1)?.[2]).toBe(0);
    expect(h.cells.find((c) => c[0] === 1 && c[1] === 0)).toBeUndefined(); // Susu @ CB: tidak ada sel = n/a
  });
  it('funnel: tahap < k ditekan dan tidak diekstrapolasi; penurunan terbesar ditandai', () => {
    const f = buildFunnel({ view: 1000, cart: 300, chk: 100, buy: 5 });
    expect(f.map((s) => s.text)).toEqual(['1.000', '300', '100', '< 10']);
    expect(f[3]).toMatchObject({ suppressed: true, count: null, pctOfFirst: null, pctOfPrev: null });
    expect(f[1].pctOfFirst).toBe(30);
    expect(f.findIndex((s) => s.biggestDrop)).toBe(1); // 30% dari sebelumnya < 33,3%
    expect(buildFunnel({ view: 5, cart: 0, chk: 0, buy: 0 }).every((s) => s.suppressed)).toBe(true);
  });
  it('total kampanye: biaya vs anggaran hanya dari kampanye ber-anggaran', () => {
    const t = campaignTotals([
      { campaignId: 1, name: 'A', kind: '', state: '', channels: [], dateFrom: null, dateTo: null, delivered: 100, vouchersIssued: 50, vouchersUsed: 20, discountIdr: 600, budgetIdr: 1000 },
      { campaignId: 2, name: 'B', kind: '', state: '', channels: [], dateFrom: null, dateTo: null, delivered: 10, vouchersIssued: 5, vouchersUsed: 1, discountIdr: 9999, budgetIdr: null },
    ]);
    expect(t.used).toBe(21);
    expect(t.costVsBudgetPct).toBe(60);
    expect(campaignTotals([]).costVsBudgetPct).toBeNull();
  });
});
