/**
 * Pemetaan galat prodplan -> pesan Indonesia (tanpa detail internal), helper tampilan hasil hitung (contoh angka §30.5),
 * SoD, fase posting, parser respons defensif, dan logika polling job.
 */
import { describe, expect, it } from 'vitest';
import { getTerm, termsIn } from '@/lib/glossary';
import { jobStep, jobIdOf, parseCallBody, POLL_MAX_MS } from '@/lib/prodplan-call';
import { PP_ERROR_MESSAGES, jobErrorMessage, mapPpError, publicStatus, sanitizeDetails } from '@/lib/prodplan-errors';
import {
  canResend,
  defaultPhase,
  describeEndpoint,
  describeLine,
  envLabel,
  fmtPpQty,
  fmtPpQtyUom,
  fmtShelfLife,
  fmtWibDateTime,
  hasOpenShortage,
  isIsoDate,
  needToCover,
  outboxErrorLabel,
  packSummary,
  parsePhase,
  parseTab,
  sodBlock,
  todayWib,
  undecidedTransfers,
  usesPack,
} from '@/lib/prodplan-format';
import { parseJob, parseMeta, parsePlan, parsePlanList, parsePreview } from '@/lib/prodplan-types';

describe('mapPpError', () => {
  it('kode dikenal -> pesan Indonesia dari tabel kita; teks server tidak diteruskan', () => {
    const r = mapPpError(409, { error: { code: 'conflict_version', message: 'SELECT * FROM prodplan.plan; stack: File "/app/x.py"', details: { row_version: 7, sql: 'DROP' } } });
    expect(r.code).toBe('conflict_version');
    expect(r.message).toBe(PP_ERROR_MESSAGES.conflict_version);
    expect(r.message).not.toMatch(/SELECT|stack|app\/x/);
    expect(r.details).toEqual({ row_version: 7 });
    expect(JSON.stringify(r)).not.toMatch(/DROP|sql/);
  });

  it('kode asing/kosong dipetakan dari status HTTP; bukan JSON -> bad_response', () => {
    expect(mapPpError(404, { error: { code: 'aneh' } }).code).toBe('not_found');
    expect(mapPpError(403, null).code).toBe('forbidden');
    expect(mapPpError(401, {}).code).toBe('actor_invalid');
    expect(mapPpError(429, 'x').code).toBe('rate_limited');
    expect(mapPpError(503, undefined).code).toBe('disabled');
    expect(mapPpError(500, '<html>').code).toBe('bad_response');
    expect(mapPpError(500, '<html>').message).toBe(PP_ERROR_MESSAGES.bad_response);
    // kunci prototipe tidak boleh lolos sebagai kode
    expect(mapPpError(500, { error: { code: '__proto__' } }).code).toBe('bad_response');
    expect(mapPpError(500, { error: { code: 'constructor' } }).code).toBe('bad_response');
  });

  it('semua kode kontrak §30.8 punya pesan Indonesia tidak kosong', () => {
    for (const c of [
      'invalid_request', 'actor_invalid', 'forbidden', 'not_found', 'conflict_version', 'invalid_state', 'bom_cycle', 'bom_too_deep', 'bom_not_assembly', 'invalid_branch',
      'job_running', 'stale_calculation', 'stock_too_old', 'sod_violation', 'transfers_undecided', 'transfers_not_authorized', 'write_disabled', 'digest_mismatch',
      'shortage_open', 'too_many_docs', 'date_out_of_window', 'rate_limited', 'esb_unavailable', 'disabled',
    ]) {
      expect(PP_ERROR_MESSAGES[c], c).toBeTruthy();
      expect(PP_ERROR_MESSAGES[c].length).toBeGreaterThan(15);
    }
  });

  it('bom_cycle menampilkan jalur kode produk; transfers_not_authorized menampilkan nomor dokumen', () => {
    const c = mapPpError(422, { error: { code: 'bom_cycle', details: { path: ['FIN-A', 'WIP-B', 'FIN-A'] } } });
    expect(c.message).toContain('FIN-A → WIP-B → FIN-A');
    const t = mapPpError(409, { error: { code: 'transfers_not_authorized', details: { docs: ['ST202611240001', 'GT0099'] } } });
    expect(t.message).toContain('ST202611240001, GT0099');
  });

  it('details disaring: tipe salah, isi aneh, dan kunci tak dikenal dibuang', () => {
    expect(sanitizeDetails({ fields: ['qty', '<script>', 'bom_id'], x: 1 })).toEqual({ fields: ['qty', 'bom_id'] });
    expect(sanitizeDetails({ field: 'production_date' })).toEqual({ fields: ['production_date'] });
    expect(sanitizeDetails({ path: ['OK-1', "a'; DROP", 5] })).toEqual({ path: ['OK-1'] });
    expect(sanitizeDetails({ row_version: 'x' })).toBeUndefined();
    expect(sanitizeDetails('x')).toBeUndefined();
    expect(sanitizeDetails({ stack: 'x', url: 'https://esb' })).toBeUndefined();
  });

  it('publicStatus hanya meneruskan status yang dikenal; selain itu 502', () => {
    for (const s of [400, 401, 403, 404, 409, 422, 429, 502, 503]) expect(publicStatus(s)).toBe(s);
    for (const s of [200, 500, 504, 301]) expect(publicStatus(s)).toBe(502);
  });

  it('kode galat job', () => {
    expect(jobErrorMessage('esb_auth_failed')).toContain('Login');
    expect(jobErrorMessage('call_budget_exceeded')).toContain('batas');
    expect(jobErrorMessage('aneh')).toContain('gagal');
    expect(jobErrorMessage(null)).toContain('gagal');
  });
});

describe('parseCallBody + polling job (sisi browser)', () => {
  it('sukses dan galat', () => {
    expect(parseCallBody(200, { ok: true, status: 200, data: { a: 1 } })).toEqual({ ok: true, status: 200, data: { a: 1 } });
    const e = parseCallBody(409, { error: { code: 'invalid_state', message: PP_ERROR_MESSAGES.invalid_state } });
    expect(e).toMatchObject({ ok: false, status: 409, code: 'invalid_state', message: PP_ERROR_MESSAGES.invalid_state });
    expect(parseCallBody(500, '<html>')).toMatchObject({ ok: false, code: 'bad_response', message: PP_ERROR_MESSAGES.bad_response });
    expect(parseCallBody(200, { ok: false })).toMatchObject({ ok: false });
    expect(parseCallBody(422, { error: { code: 'bad_input', message: 'x', details: { fields: ['qty', 3] } } })).toMatchObject({ fields: ['qty'] });
  });
  it('jobIdOf hanya menerima UUID', () => {
    expect(jobIdOf({ job_id: '0b0c0a0e-0000-4000-8000-000000000000' })).toBe('0b0c0a0e-0000-4000-8000-000000000000');
    expect(jobIdOf({ job_id: '../x' })).toBeNull();
    expect(jobIdOf(null)).toBeNull();
  });
  it('jobStep: tunggu, selesai, gagal (pesan Indonesia), batas 5 menit', () => {
    expect(jobStep('queued', null, 0)).toEqual({ kind: 'wait' });
    expect(jobStep('running', null, POLL_MAX_MS - 1)).toEqual({ kind: 'wait' });
    expect(jobStep('done', null, 10)).toEqual({ kind: 'done' });
    const f = jobStep('failed', 'esb_unavailable', 10);
    expect(f.kind === 'failed' && f.message).toBe(jobErrorMessage('esb_unavailable'));
    expect(jobStep('running', null, POLL_MAX_MS).kind).toBe('timeout');
    expect(POLL_MAX_MS).toBe(300_000);
  });
});

describe('format angka & hasil hitung (contoh §30.5)', () => {
  it('kuantitas Indonesia: titik ribuan, koma desimal, maks 4 desimal, tanpa nol di belakang', () => {
    expect(fmtPpQty('1950')).toBe('1.950');
    expect(fmtPpQty(8.5)).toBe('8,5');
    expect(fmtPpQty('12.5000')).toBe('12,5');
    expect(fmtPpQty('0.123456')).toBe('0,1235');
    expect(fmtPpQty(0)).toBe('0');
    expect(fmtPpQty(null)).toBe('-');
    expect(fmtPpQty('abc')).toBe('-');
    expect(fmtPpQtyUom('2000', 'ML')).toBe('2.000 ML');
    expect(fmtPpQtyUom(null, 'ML')).toBe('-');
  });

  it('susu segar: kebutuhan 1.950 ML, kemasan 1.000 -> 2 kemasan, sisa 50, efektif 2.000; stok 700 -> perlu dipenuhi 1.300', () => {
    const r = { packs: 2, pack_qty: '1000', uom_name: 'ML', need_eff_qty: '2000', stock_prod_qty: '700' };
    expect(packSummary(r)).toBe('2 kemasan × 1.000 ML');
    expect(usesPack(r)).toBe(true);
    expect(needToCover(r)).toBe(1300);
  });

  it('tepung (tanpa kemasan): kebutuhan 12,5, stok 4 -> perlu dipenuhi 8,5; stok negatif dihitung 0; stok melebihi kebutuhan -> 0', () => {
    const t = { packs: null, pack_qty: null, uom_name: 'KG', need_eff_qty: '12.5', stock_prod_qty: '4' };
    expect(packSummary(t)).toBeNull();
    expect(usesPack(t)).toBe(false);
    expect(needToCover(t)).toBe(8.5);
    expect(needToCover({ need_eff_qty: '3', stock_prod_qty: '-2' })).toBe(3);
    expect(needToCover({ need_eff_qty: '3', stock_prod_qty: '10' })).toBe(0);
    expect(needToCover({ need_eff_qty: null, stock_prod_qty: null })).toBe(0);
  });

  it('kekurangan terbuka (shortage_qty > 0) terdeteksi', () => {
    expect(hasOpenShortage([{ shortage_qty: '0' }, { shortage_qty: null }])).toBe(false);
    expect(hasOpenShortage([{ shortage_qty: '0' }, { shortage_qty: '300' }])).toBe(true);
  });

  it('umur setelah dibuka', () => {
    expect(fmtShelfLife(null)).toBe('-');
    expect(fmtShelfLife(6)).toBe('6 jam');
    expect(fmtShelfLife(48)).toBe('48 jam (2 hari)');
    expect(fmtShelfLife(36)).toBe('36 jam (1,5 hari)');
  });
});

describe('SoD, aksi, fase, tanggal', () => {
  it('sodBlock: pembuat atau pengaju tidak boleh menyetujui; pihak ketiga boleh', () => {
    expect(sodBlock({ created_by_sub: 5, submitted_by_sub: 6 }, 5)).toContain('pembuat');
    expect(sodBlock({ created_by_sub: 5, submitted_by_sub: 6 }, 6)).toContain('pengaju');
    expect(sodBlock({ created_by_sub: 5, submitted_by_sub: 6 }, 9)).toBeNull();
    expect(sodBlock({ created_by_sub: 5, submitted_by_sub: null }, 9)).toBeNull();
  });
  it('canResend: hanya failed + not_found_after_timeout', () => {
    expect(canResend({ status: 'failed', last_error_code: 'not_found_after_timeout' })).toBe(true);
    expect(canResend({ status: 'failed', last_error_code: 'esb_validation' })).toBe(false);
    expect(canResend({ status: 'unknown', last_error_code: 'not_found_after_timeout' })).toBe(false);
    expect(canResend({ status: 'confirmed', last_error_code: null })).toBe(false);
    expect(outboxErrorLabel('not_found_after_timeout')).toContain('aman dikirim ulang');
    expect(outboxErrorLabel(null)).toBe('');
  });
  it('defaultPhase: transfer dulu bila ada transfer disetujui tanpa dokumen; selain itu produksi', () => {
    const approved = { transfers: [{ status: 'approved' as const }], outbox: [] };
    expect(defaultPhase(approved as never)).toBe('transfers');
    expect(defaultPhase({ transfers: [], outbox: [] } as never)).toBe('production');
    expect(defaultPhase({ transfers: [{ status: 'approved' }], outbox: [{ phase: 'transfers' }] } as never)).toBe('production');
    expect(defaultPhase({ transfers: [{ status: 'rejected' }], outbox: [] } as never)).toBe('production');
    expect(parsePhase('transfers')).toBe('transfers');
    expect(parsePhase('x')).toBeNull();
    expect(undecidedTransfers([{ status: 'proposed' }, { status: 'approved' }, { status: 'proposed' }])).toBe(2);
  });
  it('tab, tanggal, lingkungan', () => {
    expect(parseTab('kebutuhan')).toBe('kebutuhan');
    expect(parseTab('bukan')).toBe('permintaan');
    expect(parseTab(undefined)).toBe('permintaan');
    expect(isIsoDate('2026-11-24')).toBe(true);
    expect(isIsoDate('2026-02-30')).toBe(false);
    expect(isIsoDate('24-11-2026')).toBe(false);
    expect(todayWib(new Date('2026-11-24T18:30:00Z'))).toBe('2026-11-25');
    expect(todayWib(new Date('2026-11-24T16:59:00Z'))).toBe('2026-11-24');
    expect(fmtWibDateTime('2026-11-24T07:05:00Z')).toBe('24 Nov 2026 14:05 WIB');
    expect(fmtWibDateTime('rusak')).toBe('-');
    expect(envLabel('staging')).toBe('ESB STAGING');
    expect(envLabel('production')).toBe('ESB PRODUCTION');
    expect(envLabel('x')).toContain('tidak dikenal');
  });
  it('pratinjau: asal/tujuan dan baris dengan bentuk tidak pasti', () => {
    expect(describeEndpoint({ branch_id: 4, location_id: 7 })).toBe('cabang 4, lokasi 7');
    expect(describeEndpoint(7)).toBe('lokasi 7');
    expect(describeEndpoint({ x: 1 })).toBe('-');
    expect(describeEndpoint(null)).toBe('-');
    expect(describeLine({ product_code: 'RMT000021', product_name: 'Susu', qty: '1000', uom_name: 'ML' })).toBe('RMT000021 - Susu: 1.000 ML');
    expect(describeLine({ product_code: 'X', total_qty: 2 })).toBe('X: 2');
    expect(describeLine('x')).toBeNull();
    expect(describeLine({})).toBeNull();
  });
});

describe('glosarium modul produksi', () => {
  it('istilah baru tersedia dan terdeteksi di label', () => {
    for (const id of ['kemasan-utuh', 'sisa-terbuka', 'usulan-batch', 'outbox', 'posting']) {
      const t = getTerm(id);
      expect(t, id).toBeDefined();
      expect(t?.def.length).toBeGreaterThan(20);
    }
    expect(termsIn('Kemasan utuh')).toEqual(['kemasan-utuh']);
    expect(termsIn('Sisa terbuka')).toEqual(['sisa-terbuka']);
    expect(termsIn('Usulan batch tambahan')).toEqual(['usulan-batch']);
    expect(termsIn('Dokumen ESB (outbox)')).toEqual(['outbox']);
    expect(termsIn('Pratinjau dan posting')).toEqual(['posting']);
    expect(termsIn('Rencana produksi')).toEqual([]);
  });
});

describe('parser respons defensif', () => {
  it('meta', () => {
    const m = parseMeta({ esb_env: 'staging', write_enabled: true, mfg_mode: 'actual', brands: [{ esb_brand: 'pb', company_code: 'PB', ck: [{ branch_id: 4, production_location_id: 7 }], source_locations: [] }] });
    expect(m).toMatchObject({ esb_env: 'staging', write_enabled: true, brands: [{ esb_brand: 'pb', ck: [{ branch_id: 4, production_location_id: 7 }] }] });
    expect(parseMeta(null)).toBeNull();
    expect(parseMeta({})?.write_enabled).toBe(false);
    expect(parseMeta({})?.brands).toEqual([]);
  });
  it('daftar rencana dan rencana: field hilang -> nilai aman; status asing -> draft', () => {
    expect(parsePlanList({ items: [{ id: 'a', plan_no: 'PP-1', status: 'zzz', calc_status: 'fresh', production_date: '2026-11-24' }], count: 9 })).toMatchObject({
      count: 9,
      items: [{ id: 'a', status: 'draft', calc_status: 'fresh' }],
    });
    expect(parsePlanList('x')).toEqual({ items: [], count: 0 });
    expect(parsePlan({})).toBeNull();
    const p = parsePlan({
      id: 'a',
      plan_no: 'PP-PB-20261124-001',
      status: 'submitted',
      calc_status: 'fresh',
      row_version: 3,
      created_by_sub: 5,
      requirements: [{ product_detail_id: 1, product_code: 'RMT000021', need_qty: '1950', packs: 2, pack_qty: '1000', shortage_qty: '0', flags: ['no_stock_data', 7] }],
      transfers: [{ id: 1, doc_type: 'goods_transfer_request', status: 'proposed', lines: [{ product_detail_id: 1, product_code: 'RMT000021', uom_name: 'ML', qty: '1000' }] }],
      outbox: [{ id: 'o1', seq: 1, phase: 'production', doc_kind: 'simple_manufacturing', status: 'sent' }],
    });
    expect(p?.status).toBe('submitted');
    expect(p?.row_version).toBe(3);
    expect(p?.requirements[0].flags).toEqual(['no_stock_data']);
    expect(p?.transfers[0].doc_type).toBe('goods_transfer_request');
    expect(p?.outbox[0]).toMatchObject({ phase: 'production', doc_kind: 'simple_manufacturing', status: 'sent', esb_doc_number: null });
    expect(p?.demand).toEqual([]);
    expect(p?.limitations).toEqual([]);
  });
  it('pratinjau wajib punya digest hex 64; job wajib status dikenal', () => {
    expect(parsePreview({ phase: 'production', docs: [], stock_impact: [], digest: 'zz' })).toBeNull();
    expect(parsePreview({ phase: 'production', docs: [], stock_impact: [], digest: 'a'.repeat(64) })?.phase).toBe('production');
    expect(parseJob({ status: 'weird' })).toBeNull();
    expect(parseJob({ status: 'failed', error_code: 'parse_error' })).toMatchObject({ status: 'failed', error_code: 'parse_error' });
  });
});
