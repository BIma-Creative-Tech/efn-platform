import { describe, expect, it } from 'vitest';
import { buildThresholds, DEFAULT_SM_CONFIG } from '@/lib/sm-thresholds';
import { biasReason, cutoffReason, expiryReason, poReason, fillReason, freshnessReason, gapReason, outletReason, priceReason, sellThroughReason, wapeReason } from '@/lib/status-reasons';
import { freshnessStatus, gapStatus, sellThroughStatus } from '@/lib/thresholds';
import { biasStatus, fillStatus, priceStatus, wapeStatus } from '@/lib/sm-kpi';

const t = buildThresholds(DEFAULT_SM_CONFIG);

describe('sellThroughReason', () => {
  it('bermasalah: menyebut angka, batas, terjual, pasokan, dan transfer keluar', () => {
    const r = sellThroughReason({ status: sellThroughStatus(39), pct: 39, sold: 2045, supply: 5241, stOut: 2645 });
    expect(r).toContain('Sell-through 39% < batas 70%');
    expect(r).toContain('terjual 2.045 dari pasokan 5.241');
    expect(r).toContain('transfer keluar 2.645');
  });
  it('perlu dicek: menyebut target 85%', () => {
    expect(sellThroughReason({ status: 'warn', pct: 78.5, sold: 785, supply: 1000 })).toMatch(/78,5% < target 85%/);
  });
  it('cabang CK = tidak berlaku', () => {
    expect(sellThroughReason({ status: 'neutral', pct: null, sold: 0, supply: 0, isCk: true })).toBe('Cabang CK: tidak menjual, sell-through tidak berlaku.');
  });
  it('n/a tanpa pasokan: jelaskan penyebab', () => {
    expect(sellThroughReason({ status: 'neutral', pct: null, sold: 40, supply: 0 })).toContain('tidak ada pasokan tercatat');
    expect(sellThroughReason({ status: 'neutral', pct: null, sold: 0, supply: 0 })).toContain('tidak ada pasokan dan penjualan');
  });
  it('di atas 100% dijelaskan sebagai stok awal tidak tercatat', () => {
    expect(sellThroughReason({ status: 'good', pct: 120, sold: 120, supply: 100 })).toContain('stok awal');
  });
});

describe('gapReason', () => {
  it('selisih besar: angka kirim, terima, selisih, persen', () => {
    const r = gapReason({ status: gapStatus(30), ckOut: 100, inCk: 70 });
    expect(r).toContain('Dikirim CK 100, diterima toko 70');
    expect(r).toContain('30');
    expect(r).toContain('> batas 20%');
  });
  it('terima tanpa kirim tercatat', () => {
    expect(gapReason({ status: 'neutral', ckOut: 0, inCk: 12 })).toContain('tanpa kiriman CK tercatat');
  });
});

describe('freshnessReason', () => {
  it('bermasalah, perlu dicek, tidak diketahui', () => {
    expect(freshnessReason(60, freshnessStatus(60))).toContain('> batas 48 jam');
    expect(freshnessReason(30, freshnessStatus(30))).toContain('> 26 jam');
    expect(freshnessReason(null, 'unknown')).toContain('tidak diketahui');
  });
});

describe('alasan supermarket', () => {
  it('WAPE bermasalah memuat nilai dan batas dari ambang', () => {
    const v = t.wapeBad + 5;
    expect(wapeReason(v, wapeStatus(v, t), t)).toContain(`> batas bermasalah`);
    expect(wapeReason(null, 'neutral', t)).toContain('n/a');
  });
  it('bias menyebut arah', () => {
    const v = t.biasBad + 10;
    expect(biasReason(v, biasStatus(v, t), t)).toContain('terlalu tinggi');
    expect(biasReason(-v, biasStatus(-v, t), t)).toContain('terlalu rendah');
  });
  it('outlet: waste tinggi dan omzet turun disebut dengan angka; tanpa sinyal = penjelasan', () => {
    const w = t.wasteBad + 1;
    const r = outletReason({ status: 'bad', wastePct: w, revDeltaPct: -12 }, t, 'bad', -5);
    expect(r).toContain('Waste');
    expect(r).toContain('Omzet turun 12%');
    expect(outletReason({ status: 'neutral', wastePct: 0.1, revDeltaPct: 3 }, t, 'good', -5)).toContain('Tidak ada sinyal masalah');
    expect(outletReason({ status: 'neutral', wastePct: null, revDeltaPct: null }, t, 'neutral', -5)).toContain('n/a');
  });
  it('usulan order dan PO', () => {
    expect(cutoffReason({ status: 'bad', countdown: '25 menit', uncertain: false }, 'proposed', 60)).toContain('<= 60 menit (25 menit)');
    expect(cutoffReason({ status: 'warn', countdown: '3 jam', uncertain: false }, 'draft', 60)).toContain('batas persetujuan 3 jam');
    expect(cutoffReason({ status: 'unknown', countdown: '', uncertain: true }, 'approved', 60)).toContain('usang');
    expect(cutoffReason({ status: 'bad', countdown: '', uncertain: false }, 'expired', 60)).toContain('tidak dikirim otomatis');
    expect(poReason(true, '12 Nov 2026')).toContain('sudah lewat');
    expect(poReason(false, null)).toContain('Belum ada tanggal');
  });
  it('harga vs pasar dan lot kedaluwarsa', () => {
    const hi = t.priceBad + 5;
    expect(priceReason(hi, t.priceMinSamples, priceStatus(hi, t.priceMinSamples, t).status, t)).toContain('batas bermasalah');
    expect(priceReason(5, 1, 'neutral', t)).toContain('Sampel harga pasar 1');
    expect(expiryReason('kedaluwarsa', -2, 1500000)).toContain('lewat 2 hari');
    expect(expiryReason('pantau', 9, 0)).toContain('sisa 9 hari');
    expect(expiryReason('markdown_sekarang', null, 10)).toContain('sisa umur tidak diketahui');
  });
  it('fill rate: rendah = bermasalah', () => {
    const v = t.fillWarn - 10;
    expect(fillReason(v, fillStatus(v, t), t)).toContain('batas bermasalah');
    expect(fillReason(null, 'neutral', t)).toContain('n/a');
  });
});
