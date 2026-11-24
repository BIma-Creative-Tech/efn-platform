/**
 * Kontrak UI <-> service prodplan (docs/04 §30.8) memakai bentuk JSON yang BENAR-BENAR dikeluarkan `service.py`
 * (bukan bentuk yang diasumsikan): detail galat, kode galat, kode galat outbox, bentuk rencana/pratinjau/job, dan kunci
 * body setiap aksi mutasi terhadap yang dibaca service. Tes murni (tanpa jaringan).
 */
import { describe, expect, it } from 'vitest';
import { jobStep } from '@/lib/prodplan-call';
import { ACTIONS } from '@/lib/prodplan-actions';
import { PP_ERROR_MESSAGES, jobErrorMessage, mapPpError, messageFor, sanitizeDetails } from '@/lib/prodplan-errors';
import { describeEndpoint, describeLine, fmtPpQty, outboxErrorLabel } from '@/lib/prodplan-format';
import { parseJob, parsePlan, parsePreview } from '@/lib/prodplan-types';

const PLAN_ID = '0b6f1c1e-1111-4222-8333-444455556666';
const OUTBOX_ID = '1c6f1c1e-1111-4222-8333-444455556666';

/** Bentuk `Service.get_plan` (service.py): kuantitas = string desimal 4 angka; tanggal = ISO. */
const servicePlan = {
  id: PLAN_ID,
  plan_no: 'PP-PB-20261125-001',
  company_code: 'PB',
  esb_brand: 'pb',
  esb_env: 'staging',
  branch_id: 4,
  production_location_id: 7,
  production_date: '2026-11-25',
  status: 'draft',
  calc_status: 'fresh',
  calculated_at: '2026-11-24T03:00:00+00:00',
  stock_snapshot_id: '9d6f1c1e-1111-4222-8333-444455556666',
  notes: null,
  created_by_sub: 21,
  created_by_name: 'Planner',
  created_at: '2026-11-24T02:00:00+00:00',
  submitted_by_sub: null,
  submitted_by_name: null,
  submitted_at: null,
  approved_by_sub: null,
  approved_by_name: null,
  approved_at: null,
  posted_by_sub: null,
  posted_by_name: null,
  posted_at: null,
  cancelled_by_sub: null,
  cancelled_at: null,
  cancel_reason: null,
  row_version: 5,
  updated_at: '2026-11-24T03:00:00+00:00',
  demand: [
    {
      id: 1,
      product_detail_id: 1001,
      product_code: 'FIN-A',
      product_name: 'Fin A',
      bom_id: 101,
      uom_name: 'PCS',
      qty: '30.0000',
      source: 'manual',
      pr_nums: [],
      suggestion_id: null,
    },
  ],
  unmapped: [{ pr_num: 'PR1', product_code: 'X', product_name: 'X', qty: '1.0000', reason: 'no_assembly_bom' }],
  production_lines: [
    {
      id: 10,
      level: 1,
      bom_id: 101,
      product_detail_id: 1001,
      product_code: 'FIN-A',
      product_name: 'Fin A',
      uom_name: 'PCS',
      qty: '30.0000',
      is_demand: true,
      materials: [{ product_detail_id: 21, product_code: 'RMT000021', qty_per_unit: '45', total_qty: '1350.0000' }],
    },
  ],
  requirements: [
    {
      product_detail_id: 21,
      product_code: 'RMT000021',
      product_name: 'Susu',
      uom_name: 'ML',
      need_qty: '1950.0000',
      pack_qty: '1000.0000',
      packs: 2,
      leftover_qty: '50.0000',
      need_eff_qty: '2000.0000',
      stock_prod_qty: '700.0000',
      transfer_qty: '2000.0000',
      shortage_qty: '0.0000',
      flags: [],
    },
  ],
  suggestions: [
    {
      id: 7,
      material_detail_id: 21,
      bom_id: 101,
      product_detail_id: 1001,
      extra_units: 1,
      uses_qty: '45.0000',
      leftover_qty: '50.0000',
      status: 'open',
      decided_by_sub: null,
      decided_at: null,
      material_code: 'RMT000021',
      material_name: 'Susu',
      product_code: 'FIN-A',
      product_name: 'Fin A',
    },
  ],
  transfers: [
    {
      id: 3,
      doc_type: 'goods_transfer_request',
      origin_branch_id: 13,
      origin_location_id: 21,
      dest_branch_id: 4,
      dest_location_id: 7,
      status: 'proposed',
      decided_by_sub: null,
      decided_by_name: null,
      decided_at: null,
      reject_reason: null,
      lines: [
        {
          transfer_id: 3,
          product_detail_id: 21,
          product_code: 'RMT000021',
          uom_name: 'ML',
          qty: '1000.0000',
          product_name: 'Susu',
        },
      ],
    },
  ],
  outbox: [
    {
      id: OUTBOX_ID,
      seq: 1,
      phase: 'transfers',
      doc_kind: 'simple_transfer',
      status: 'confirmed',
      attempts: 1,
      esb_doc_number: 'ST202611240001',
      esb_status_id: 1,
      last_error_code: null,
      sent_at: '2026-11-24T03:01:00+00:00',
      confirmed_at: '2026-11-24T03:02:00+00:00',
      deleted_at: null,
    },
  ],
  limitations: ['L-253: stok WIP tidak di-netting'],
  stock_fetched_at: '2026-11-24T03:00:00+00:00',
};

describe('bentuk rencana dari service.get_plan', () => {
  const plan = parsePlan(servicePlan);

  it('kunci utama terbaca (string desimal dipertahankan, row_version, status)', () => {
    expect(plan).not.toBeNull();
    expect(plan?.row_version).toBe(5);
    expect(plan?.status).toBe('draft');
    expect(plan?.calc_status).toBe('fresh');
    expect(plan?.demand[0]).toMatchObject({ product_detail_id: 1001, bom_id: 101, qty: '30.0000', source: 'manual' });
    expect(plan?.unmapped[0].reason).toBe('no_assembly_bom');
    expect(plan?.limitations).toHaveLength(1);
  });

  it('kebutuhan, usulan (dengan nama), transfer (dengan nama baris) dan outbox terbaca', () => {
    const req = plan?.requirements[0];
    expect(req).toMatchObject({ packs: 2, need_eff_qty: '2000.0000', shortage_qty: '0.0000', flags: [] });
    expect(plan?.suggestions[0]).toMatchObject({ id: 7, material_name: 'Susu', product_name: 'Fin A', status: 'open' });
    const t = plan?.transfers[0];
    expect(t?.doc_type).toBe('goods_transfer_request');
    expect(t?.lines[0]).toMatchObject({ product_name: 'Susu', qty: '1000.0000', uom_name: 'ML' });
    expect(plan?.outbox[0]).toMatchObject({ seq: 1, status: 'confirmed', esb_doc_number: 'ST202611240001', attempts: 1 });
    expect(plan?.production_lines[0]).toMatchObject({ level: 1, is_demand: true, qty: '30.0000' });
  });

  it('string desimal 4 angka diformat tanpa nol belakang', () => {
    expect(fmtPpQty('1050.0000')).toBe('1.050');
    expect(fmtPpQty('0.0000')).toBe('0');
  });
});

describe('pratinjau posting dari service.build_preview', () => {
  const digest = 'a'.repeat(64);

  it('dokumen transfer: asal/tujuan objek dan baris ber-qty string terbaca', () => {
    const p = parsePreview({
      phase: 'transfers',
      docs: [
        {
          doc_kind: 'simple_transfer',
          transfer_id: 1,
          origin: { branch_id: 4, location_id: 8 },
          dest: { branch_id: 4, location_id: 7 },
          lines: [{ product_detail_id: 21, product_code: 'RMT000021', uom_name: 'ML', qty: '1000.0000' }],
        },
      ],
      stock_impact: [{ location_id: 7, product_code: 'RMT000021', qty_out: '0.0000', qty_in: '1000.0000' }],
      digest,
    });
    expect(p?.docs[0].doc_kind).toBe('simple_transfer');
    expect(describeEndpoint(p?.docs[0].origin)).toBe('cabang 4, lokasi 8');
    expect(describeLine(p?.docs[0].lines?.[0])).toBe('RMT000021: 1.000 ML');
    expect(p?.stock_impact[0].qty_in).toBe('1000.0000');
  });

  it('dokumen produksi: baris bahan tanpa uom tetap tampil', () => {
    const line = { product_detail_id: 31, product_code: 'RMT000031', qty: '1.0000' };
    expect(describeLine(line)).toBe('RMT000031: 1');
  });
});

describe('job dari service.get_job', () => {
  it('done membawa result; failed membawa error_code', () => {
    const done = parseJob({ status: 'done', kind: 'calculate', result: { production_lines: 1, warnings: {} } });
    expect(done?.status).toBe('done');
    expect(jobStep('done', null, 0).kind).toBe('done');
    const failed = parseJob({ status: 'failed', kind: 'calculate', error_code: 'bom_cycle', result: { path: ['A', 'B'] } });
    expect(failed?.error_code).toBe('bom_cycle');
  });

  it('kode galat job yang ditulis worker tetapi tidak ada di kontrak tetap menghasilkan pesan', () => {
    for (const code of ['conflict_version', 'invalid_state', 'bom_not_assembly', 'bom_missing', 'interrupted', 'internal']) {
      expect(jobErrorMessage(code)).toMatch(/\S/);
    }
  });
});

describe('kode galat', () => {
  // Semua kode `workflow.MESSAGES` yang bisa sampai ke browser (internal/method_not_allowed/payload_too_large dipetakan
  // dari status HTTP oleh mapPpError).
  const SERVICE_CODES = [
    'invalid_request',
    'actor_invalid',
    'forbidden',
    'not_found',
    'conflict_version',
    'invalid_state',
    'rate_limited',
    'esb_unavailable',
    'disabled',
    'invalid_branch',
    'bom_not_assembly',
    'bom_cycle',
    'bom_too_deep',
    'job_running',
    'stale_calculation',
    'stock_too_old',
    'sod_violation',
    'transfers_undecided',
    'write_disabled',
    'digest_mismatch',
    'transfers_not_authorized',
    'shortage_open',
    'too_many_docs',
    'date_out_of_window',
  ];

  it.each(SERVICE_CODES)('kode service %s punya pesan Indonesia sendiri', (code) => {
    expect(PP_ERROR_MESSAGES[code]).toBeTruthy();
    expect(mapPpError(409, { error: { code } }).code).toBe(code);
  });

  it('transfers_not_authorized: details.doc_numbers (bentuk service) tampil di pesan', () => {
    const body = { error: { code: 'transfers_not_authorized', message: 'x', details: { doc_numbers: ['ST202611240001', 'SM202611240001 - 1'] } } };
    const r = mapPpError(409, body);
    expect(r.details?.docs).toEqual(['ST202611240001', 'SM202611240001 - 1']);
    expect(r.message).toContain('ST202611240001');
    expect(messageFor('transfers_not_authorized', sanitizeDetails({ docs: ['GT1'] }))).toContain('GT1');
  });

  it('galat validasi service (details.field tunggal) menjadi fields[]', () => {
    const r = mapPpError(400, { error: { code: 'invalid_request', details: { field: 'lines[2].qty' } } });
    expect(r.details?.fields).toEqual(['lines[2].qty']);
  });

  it('conflict_version membawa row_version terkini', () => {
    expect(mapPpError(409, { error: { code: 'conflict_version', details: { row_version: 9 } } }).details?.row_version).toBe(9);
  });

  it('kode galat outbox yang ditulis worker punya label khusus (bukan teks umum)', () => {
    const generic = outboxErrorLabel('kode_tak_dikenal');
    for (const code of ['esb_rejected', 'not_sent', 'sent_unknown', 'doc_not_found', 'marker_mismatch', 'cancelled', 'client_error', 'esb_auth_failed', 'not_found_after_timeout']) {
      const label = outboxErrorLabel(code);
      expect(label).toMatch(/\S/);
      expect(label).not.toBe(generic);
    }
  });
});

describe('body aksi = kunci yang dibaca service', () => {
  const keys = (name: string, input: Record<string, unknown>): string[] => {
    const r = ACTIONS[name].build(input);
    if (!r.ok) throw new Error(`${name}: ${r.field}`);
    return Object.keys(r.body).sort();
  };
  const base = { id: PLAN_ID, row_version: 3 };

  it('aksi bertanda versi hanya mengirim row_version (+ alasan bila perlu)', () => {
    expect(keys('plan-calculate', base)).toEqual(['row_version']);
    expect(keys('plan-submit', base)).toEqual(['row_version']);
    expect(keys('plan-approve', base)).toEqual(['row_version']);
    expect(keys('suggestion-accept', { ...base, sid: 7 })).toEqual(['row_version']);
    expect(keys('suggestion-reject', { ...base, sid: 7 })).toEqual(['row_version']);
    expect(keys('transfer-approve', { ...base, tid: 3 })).toEqual(['row_version']);
    expect(keys('transfer-reject', { ...base, tid: 3, reason: 'stok tidak ada' })).toEqual(['reason', 'row_version']);
    expect(keys('plan-return', { ...base, reason: 'salah qty' })).toEqual(['reason', 'row_version']);
    expect(keys('plan-cancel', { ...base, reason: 'batal' })).toEqual(['reason', 'row_version']);
  });

  it('alasan wajib untuk tolak transfer, kembalikan, dan batalkan', () => {
    for (const [name, extra] of [['transfer-reject', { tid: 3 }], ['plan-return', {}], ['plan-cancel', {}]] as const) {
      expect(ACTIONS[name].build({ ...base, ...extra }).ok).toBe(false);
    }
  });

  it('posting mengirim phase, digest, accept_shortage (boolean) dan row_version', () => {
    const r = ACTIONS['plan-post'].build({ ...base, phase: 'production', digest: 'b'.repeat(64) });
    expect(r.ok && r.body).toEqual({ row_version: 3, phase: 'production', digest: 'b'.repeat(64), accept_shortage: false });
    const accepted = ACTIONS['plan-post'].build({ ...base, phase: 'transfers', digest: 'b'.repeat(64), accept_shortage: true });
    expect(accepted.ok && accepted.body.accept_shortage).toBe(true);
  });

  it('impor PR, buat rencana, dan kirim ulang outbox', () => {
    expect(keys('plan-import-pr', { id: PLAN_ID, required_date_from: '2026-11-25', required_date_to: '2026-11-26' })).toEqual([
      'required_date_from',
      'required_date_to',
    ]);
    expect(keys('plan-create', { brand: 'pb', branch_id: 4, production_location_id: 7, production_date: '2026-11-25' })).toEqual([
      'branch_id',
      'brand',
      'production_date',
      'production_location_id',
    ]);
    expect(keys('outbox-resend', { oid: OUTBOX_ID })).toEqual([]);
  });

  it('editor permintaan mengirim angka (bukan string) ke service walau input berupa string', () => {
    const r = ACTIONS['plan-demand'].build({
      ...base,
      lines: [{ product_detail_id: '1001', bom_id: '101', qty: '12.5', source: 'manual' }],
    });
    expect(r.ok && r.body.lines).toEqual([{ product_detail_id: 1001, bom_id: 101, qty: 12.5, source: 'manual' }]);
  });
});
