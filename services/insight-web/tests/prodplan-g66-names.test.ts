/**
 * Putaran G66 (nama bukan ID, segarkan stok saat disetujui, batalkan penerimaan usulan, kirim ulang dokumen gagal, label galat
 * job, keterbatasan): parser field baru yang opsional, aturan tombol (fungsi murni), dan label.
 */
import { describe, expect, it } from 'vitest';
import { jobDoneText, jobStep } from '@/lib/prodplan-call';
import { PP_ERROR_MESSAGES, jobErrorMessage, mapPpError } from '@/lib/prodplan-errors';
import {
  POSTING_STATE,
  RESEND_ERROR_CODES,
  canRefreshStock,
  canResend,
  cleanLimitation,
  ckLabel,
  describeEndpoint,
  fmtPlace,
  placeHint,
  postingBadge,
  productionViews,
  submitBlock,
  suggestionButtons,
} from '@/lib/prodplan-format';
import { parseJobInfo, parseMeta, parsePlan, parsePlanList, parsePreview } from '@/lib/prodplan-types';

describe('parser: nama cabang/lokasi dan field baru bersifat opsional', () => {
  it('meta.ck memuat nama; tanpa nama -> null', () => {
    const m = parseMeta({
      esb_env: 'staging',
      brands: [{ esb_brand: 'pb', company_code: 'PB', ck: [{ branch_id: 4, production_location_id: 7, branch_name: 'CK Paris', production_location_name: 'WH Produksi' }, { branch_id: 5, production_location_id: 9 }] }],
    });
    expect(m?.brands[0].ck[0]).toEqual({ branch_id: 4, production_location_id: 7, branch_name: 'CK Paris', production_location_name: 'WH Produksi' });
    expect(m?.brands[0].ck[1]).toEqual({ branch_id: 5, production_location_id: 9, branch_name: null, production_location_name: null });
  });

  it('daftar/detail rencana: nama + posting_state; nilai asing diabaikan', () => {
    const l = parsePlanList({
      items: [
        { id: 'a', plan_no: 'P1', status: 'posted', branch_id: 4, branch_name: 'CK Paris', production_location_id: 7, production_location_name: 'WH Produksi', posting_state: 'needs_attention' },
        { id: 'b', plan_no: 'P2', status: 'draft', posting_state: 'aneh' },
        { id: 'c', plan_no: 'P3', status: 'draft' },
      ],
    });
    expect(l.items[0]).toMatchObject({ branch_name: 'CK Paris', production_location_name: 'WH Produksi', posting_state: 'needs_attention' });
    expect(l.items[1].posting_state).toBeUndefined();
    expect(l.items[2].branch_name).toBeNull();
    const p = parsePlan({
      id: 'a',
      plan_no: 'P1',
      status: 'approved',
      stock_fetched_at: '2026-11-24T03:00:00+00:00',
      posting_state: 'in_progress',
      transfers: [{ id: 1, origin_branch_id: 4, origin_location_id: 8, origin_branch_name: 'CK Paris', origin_location_name: 'WH Bahan', dest_branch_id: 4, dest_location_id: 7, status: 'approved', lines: [] }],
    });
    expect(p?.stock_fetched_at).toBe('2026-11-24T03:00:00+00:00');
    expect(p?.posting_state).toBe('in_progress');
    expect(p?.transfers[0]).toMatchObject({ origin_branch_name: 'CK Paris', origin_location_name: 'WH Bahan', dest_branch_name: null, dest_location_name: null });
    expect(parsePlan({ id: 'a', plan_no: 'P1' })?.stock_fetched_at).toBeNull();
  });

  it('pratinjau: asal/tujuan bernama, dampak stok bernama, dokumen produksi dengan produk hasil', () => {
    const digest = 'b'.repeat(64);
    const p = parsePreview({
      phase: 'production',
      docs: [
        {
          doc_kind: 'simple_manufacturing',
          product_code: 'FIN-A',
          product_name: 'Roti Tawar',
          uom_name: 'PCS',
          qty: '30.0000',
          level: 1,
          origin: { branch_id: 4, location_id: 7, branch_name: 'CK Paris', location_name: 'WH Produksi' },
          lines: [{ product_code: 'RMT1', product_name: 'Tepung', qty: '12.5', uom_name: 'KG' }],
        },
      ],
      stock_impact: [{ location_id: 7, location_name: 'WH Produksi', product_code: 'FIN-A', product_name: 'Roti Tawar', qty_in: '30', qty_out: '0' }],
      digest,
    });
    expect(p?.docs[0]).toMatchObject({ product_code: 'FIN-A', product_name: 'Roti Tawar', qty: '30.0000', level: 1 });
    expect(describeEndpoint(p?.docs[0].origin)).toBe('CK Paris - WH Produksi');
    expect(p?.stock_impact[0]).toMatchObject({ location_name: 'WH Produksi', product_name: 'Roti Tawar' });
    // dokumen transfer lama (tanpa nama/produk) tidak mendapat field produksi
    const t = parsePreview({ phase: 'transfers', docs: [{ doc_kind: 'simple_transfer', lines: [] }], stock_impact: [], digest });
    expect(Object.keys(t?.docs[0] ?? {})).toEqual(['doc_kind', 'origin', 'dest', 'lines']);
  });

  it('parseJobInfo: plan_status, changed, path disaring', () => {
    expect(parseJobInfo({ plan_status: 'draft', changed: true, path: ['A', 'B', "x'; DROP", 5] })).toEqual({ plan_status: 'draft', changed: true, path: ['A', 'B'] });
    expect(parseJobInfo({ plan_status: 'approved', changed: false })).toEqual({ plan_status: 'approved', changed: false, path: [] });
    expect(parseJobInfo({ plan_status: 'cancelled', changed: 'ya' })).toEqual({ plan_status: null, changed: null, path: [] });
    expect(parseJobInfo(null)).toEqual({ plan_status: null, changed: null, path: [] });
  });
});

describe('nama dulu, ID cadangan', () => {
  it('fmtPlace / placeHint / ckLabel', () => {
    expect(fmtPlace('CK Paris', 4, 'WH Produksi', 7)).toBe('CK Paris - WH Produksi');
    expect(placeHint('CK Paris', 4, 'WH Produksi', 7)).toBe('ID 4 / 7');
    expect(fmtPlace('CK Paris', 4, null, 7)).toBe('CK Paris');
    expect(fmtPlace(null, 4, null, 7)).toBe('cabang 4, lokasi 7');
    expect(placeHint(null, 4, null, 7)).toBe('');
    expect(fmtPlace('', 0, undefined, undefined)).toBe('-');
    expect(ckLabel('PB', { branch_id: 4, production_location_id: 7, branch_name: 'CK Paris', production_location_name: 'WH Produksi' })).toBe('PB - CK Paris - WH Produksi (ID 4 / 7)');
    expect(ckLabel('PB', { branch_id: 4, production_location_id: 7 })).toBe('PB - cabang 4, lokasi 7');
    expect(describeEndpoint({ branch_id: 4, location_id: 8 })).toBe('cabang 4, lokasi 8');
    expect(describeEndpoint({ branch_id: 4, location_id: 8, location_name: 'WH Bahan' })).toBe('WH Bahan');
  });
});

describe('pratinjau produksi: produk hasil lalu bahan', () => {
  it('field tingkat dokumen + bahan dari lines[]', () => {
    const v = productionViews({
      product_code: 'FIN-A',
      product_name: 'Roti Tawar',
      uom_name: 'PCS',
      qty: '30.0000',
      level: 1,
      lines: [{ product_code: 'RMT1', product_name: 'Tepung', qty: '12.5', uom_name: 'KG' }, 'x'],
    });
    expect(v).toEqual([{ product: 'FIN-A - Roti Tawar', qty: '30 PCS', level: 1, materials: ['RMT1 - Tepung: 12,5 KG'] }]);
  });
  it('bahan eksplisit (materials[]) didahulukan; level hilang -> null', () => {
    const v = productionViews({ product_code: 'FIN-B', qty: 5, materials: [{ product_code: 'M1', total_qty: '2' }], lines: [{ product_code: 'ZZ', qty: 1 }] });
    expect(v).toEqual([{ product: 'FIN-B', qty: '5', level: null, materials: ['M1: 2'] }]);
  });
  it('tanpa produk di dokumen: baca dari lines yang punya materials; selain itu kosong', () => {
    const v = productionViews({ lines: [{ product_code: 'FIN-C', product_name: 'Kue', qty: '2', materials: [{ product_code: 'M2', qty: '1' }] }, { product_code: 'M3', qty: '1' }] });
    expect(v).toEqual([{ product: 'FIN-C - Kue', qty: '2', level: null, materials: ['M2: 1'] }]);
    expect(productionViews({ lines: [{ product_code: 'M3', qty: '1' }] })).toEqual([]);
    expect(productionViews({})).toEqual([]);
  });
});

describe('aturan tombol (fungsi murni)', () => {
  it('segarkan stok: hanya rencana disetujui, planner ATAU gudang', () => {
    expect(canRefreshStock('approved', { planner: true, warehouse: false })).toBe(true);
    expect(canRefreshStock('approved', { planner: false, warehouse: true })).toBe(true);
    expect(canRefreshStock('approved', { planner: false, warehouse: false })).toBe(false);
    for (const s of ['draft', 'submitted', 'posted', 'cancelled'] as const) expect(canRefreshStock(s, { planner: true, warehouse: true }), s).toBe(false);
  });

  it('usulan batch: terima/tolak hanya saat open; batalkan penerimaan hanya saat accepted; hanya planner pada rencana draf', () => {
    expect(suggestionButtons('open', 'draft', true)).toEqual({ accept: true, reject: true, cancelAccept: false });
    expect(suggestionButtons('accepted', 'draft', true)).toEqual({ accept: false, reject: false, cancelAccept: true });
    for (const st of ['rejected', 'superseded'] as const) expect(suggestionButtons(st, 'draft', true)).toEqual({ accept: false, reject: false, cancelAccept: false });
    expect(suggestionButtons('accepted', 'submitted', true).cancelAccept).toBe(false);
    expect(suggestionButtons('accepted', 'draft', false).cancelAccept).toBe(false);
    expect(suggestionButtons('open', 'approved', true).accept).toBe(false);
  });

  it('kirim ulang: failed, tanpa nomor ESB, kode dalam daftar', () => {
    for (const code of ['esb_rejected', 'not_sent', 'client_error', 'not_found_after_timeout']) {
      expect(canResend({ status: 'failed', last_error_code: code, esb_doc_number: null }), code).toBe(true);
      expect(RESEND_ERROR_CODES).toContain(code);
    }
    expect(canResend({ status: 'failed', last_error_code: 'esb_rejected', esb_doc_number: 'ST0001' })).toBe(false);
    expect(canResend({ status: 'failed', last_error_code: 'marker_mismatch', esb_doc_number: null })).toBe(false);
    expect(canResend({ status: 'failed', last_error_code: null })).toBe(false);
    expect(canResend({ status: 'unknown', last_error_code: 'not_sent', esb_doc_number: null })).toBe(false);
    expect(canResend({ status: 'confirmed', last_error_code: 'not_sent', esb_doc_number: null })).toBe(false);
  });

  it('ajukan: butuh hasil hitung terbaru DAN baris produksi', () => {
    expect(submitBlock({ calc_status: 'fresh', production_lines: [{ level: 1 } as never] })).toBeNull();
    expect(submitBlock({ calc_status: 'stale', production_lines: [{ level: 1 } as never] })).toContain('Hitung kebutuhan');
    expect(submitBlock({ calc_status: 'fresh', production_lines: [] })).toContain('baris produksi');
  });

  it('lencana posting: teks + nada; none disembunyikan kecuali sudah posted; tanpa field -> tidak ada', () => {
    expect(postingBadge({ status: 'posted', posting_state: 'needs_attention' })).toEqual(POSTING_STATE.needs_attention);
    expect(POSTING_STATE.needs_attention.tone).toBe('bad');
    expect(POSTING_STATE.needs_attention.label).toMatch(/Perlu perhatian/);
    expect(postingBadge({ status: 'posted', posting_state: 'complete' })?.label).toBe('Posting selesai');
    expect(postingBadge({ status: 'approved', posting_state: 'in_progress' })?.label).toBe('Posting berjalan');
    expect(postingBadge({ status: 'approved', posting_state: 'none' })).toBeNull();
    expect(postingBadge({ status: 'posted', posting_state: 'none' })?.label).toBe('Belum diposting');
    expect(postingBadge({ status: 'posted' })).toBeNull();
  });

  it('keterbatasan tampil sebagai kalimat tanpa awalan kode', () => {
    expect(cleanLimitation('L-253: stok WIP tidak di-netting')).toBe('Stok WIP tidak di-netting');
    expect(cleanLimitation('L-255 - urutan sumber bukan FEFO')).toBe('Urutan sumber bukan FEFO');
    expect(cleanLimitation('Tanpa awalan kode.')).toBe('Tanpa awalan kode.');
    expect(cleanLimitation('L-1:')).toBe('L-1:');
  });
});

describe('label galat', () => {
  it('empty_plan dan stock_too_old (mengarah ke tombol segarkan stok)', () => {
    expect(mapPpError(409, { error: { code: 'empty_plan' } })).toMatchObject({ code: 'empty_plan', message: PP_ERROR_MESSAGES.empty_plan });
    expect(PP_ERROR_MESSAGES.empty_plan).toMatch(/baris produksi/);
    expect(PP_ERROR_MESSAGES.stock_too_old).toContain('Segarkan stok & hitung ulang');
    expect(PP_ERROR_MESSAGES.stock_too_old).toContain('Hitung ulang');
  });

  it('kode galat job: label khusus, jalur siklus BOM, fallback umum', () => {
    for (const c of ['conflict_version', 'invalid_state', 'bom_missing', 'bom_not_assembly', 'interrupted', 'internal', 'bom_cycle']) {
      expect(jobErrorMessage(c), c).toMatch(/\S/);
      expect(jobErrorMessage(c)).not.toBe(jobErrorMessage('kode_asing_xyz'));
    }
    expect(jobErrorMessage('bom_cycle', ['FIN-A', 'WIP-B', 'FIN-A'])).toContain('FIN-A → WIP-B → FIN-A');
    expect(jobErrorMessage('bom_cycle', [])).not.toContain('Jalur');
    expect(jobErrorMessage('esb_unavailable', ['A'])).not.toContain('Jalur');
    expect(jobErrorMessage('kode_asing_xyz')).toContain('gagal');
    expect(jobErrorMessage('__proto__')).toContain('gagal');
    const f = jobStep('failed', 'bom_cycle', 0, ['A', 'B']);
    expect(f.kind === 'failed' && f.message).toContain('A → B');
  });

  it('teks job selesai: segarkan stok berubah -> kembali ke draf; tidak berubah -> tetap disetujui', () => {
    const back = jobDoneText({ planStatus: 'draft', changed: true }, true);
    expect(back.backToDraft).toBe(true);
    expect(back.text).toMatch(/kembali ke Draf/);
    expect(back.text).toMatch(/ajukan/i);
    expect(jobDoneText({ planStatus: null, changed: true }, true).backToDraft).toBe(true);
    expect(jobDoneText({ planStatus: 'draft', changed: null }, true).backToDraft).toBe(true);
    const same = jobDoneText({ planStatus: 'approved', changed: false }, true);
    expect(same.backToDraft).toBe(false);
    expect(same.text).toMatch(/tetap Disetujui/);
    expect(jobDoneText({ planStatus: 'draft', changed: true }, false)).toEqual({ text: 'Proses selesai.', backToDraft: false });
  });
});
