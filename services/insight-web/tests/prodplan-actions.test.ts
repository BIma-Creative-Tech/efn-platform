/**
 * Daftar aksi mutasi (docs/04 §30.8): peran, validasi input browser, pemetaan ke method/path/body. Identitas tidak pernah
 * berasal dari input.
 */
import { describe, expect, it } from 'vitest';
import { ACTIONS, getAction, parsePosInt, parseQty, parseText, parseUuid, roleAllowed, type BuildResult } from '@/lib/prodplan-actions';

const ID = '0b0c0a0e-1111-4222-8333-444455556666';
const DIGEST = 'ab'.repeat(32);
const build = (name: string, input: Record<string, unknown>): BuildResult => {
  const a = getAction(name);
  if (!a) throw new Error(`aksi ${name} tidak ada`);
  return a.build(input);
};

describe('peran per aksi', () => {
  const P = { planner: true, warehouse: false };
  const W = { planner: false, warehouse: true };
  const both = { planner: true, warehouse: true };
  it('P = planner, W = kepala gudang, R = salah satu', () => {
    expect(roleAllowed('P', P)).toBe(true);
    expect(roleAllowed('P', W)).toBe(false);
    expect(roleAllowed('W', W)).toBe(true);
    expect(roleAllowed('W', P)).toBe(false);
    expect(roleAllowed('R', P)).toBe(true);
    expect(roleAllowed('R', W)).toBe(true);
    expect(roleAllowed('R', { planner: false, warehouse: false })).toBe(false);
    expect(roleAllowed('W', both)).toBe(true);
  });
  it('tabel peran sesuai §30.8', () => {
    const role = (n: string) => ACTIONS[n].role;
    for (const n of ['plan-create', 'plan-demand', 'plan-import-pr', 'suggestion-accept', 'suggestion-reject', 'plan-submit', 'plan-cancel', 'outbox-resend', 'pack-rule-put']) {
      expect(role(n), n).toBe('P');
    }
    for (const n of ['transfer-approve', 'transfer-reject', 'plan-approve']) expect(role(n), n).toBe('W');
    // plan-calculate = R: draf oleh planner, rencana disetujui (segarkan stok) oleh planner ATAU gudang (server menegakkan per status).
    for (const n of ['plan-return', 'plan-post', 'plan-calculate']) expect(role(n), n).toBe('R');
  });
  it('aksi tak dikenal / kunci prototipe -> null', () => {
    expect(getAction('plan-delete')).toBeNull();
    expect(getAction('__proto__')).toBeNull();
    expect(getAction('constructor')).toBeNull();
    expect(getAction('toString')).toBeNull();
  });
});

describe('validator skalar', () => {
  it('uuid, bilangan bulat positif, kuantitas, teks', () => {
    expect(parseUuid(ID)).toBe(ID);
    expect(parseUuid('../x')).toBeNull();
    expect(parseUuid(ID.toUpperCase())).toBeNull();
    expect(parsePosInt(5)).toBe(5);
    expect(parsePosInt('12')).toBe(12);
    for (const v of [0, -1, 1.5, '1.5', 'abc', null, undefined, 2 ** 31, '99999999999']) expect(parsePosInt(v), String(v)).toBeNull();
    expect(parseQty(12.5)).toBe(12.5);
    expect(parseQty('12.5')).toBe(12.5);
    expect(parseQty('0.0001')).toBe(0.0001);
    for (const v of [0, -1, '0', '1.23456', 1.23456, 'abc', '1,5', NaN, Infinity, 1e12, '', null]) expect(parseQty(v), String(v)).toBeNull();
    expect(parseText('  halo  ', 10, true)).toBe('halo');
    expect(parseText('', 10, true)).toBeNull();
    expect(parseText('', 10, false)).toBeUndefined();
    expect(parseText('x'.repeat(11), 10, true)).toBeNull();
    expect(parseText(5, 10, false)).toBeNull();
    expect(parseText('a\u0000b', 10, true)).toBe('a b');
  });
});

describe('plan-create', () => {
  const ok = { brand: 'pb', branch_id: 4, production_location_id: 7, production_date: '2026-11-24', notes: ' uji ' };
  it('sah -> POST /v1/plans; identitas dari input diabaikan', () => {
    const r = build('plan-create', { ...ok, sub: 999, created_by_sub: 999, roles: ['pp_warehouse'] });
    expect(r).toEqual({ ok: true, method: 'POST', path: '/v1/plans', body: { brand: 'pb', branch_id: 4, production_location_id: 7, production_date: '2026-11-24', notes: 'uji' } });
  });
  it('field tidak sah dilaporkan namanya', () => {
    expect(build('plan-create', { ...ok, brand: 'PB' })).toEqual({ ok: false, field: 'brand' });
    expect(build('plan-create', { ...ok, branch_id: 0 })).toEqual({ ok: false, field: 'branch_id' });
    expect(build('plan-create', { ...ok, production_location_id: 'x' })).toEqual({ ok: false, field: 'production_location_id' });
    expect(build('plan-create', { ...ok, production_date: '2026-13-01' })).toEqual({ ok: false, field: 'production_date' });
    expect(build('plan-create', { ...ok, notes: 'x'.repeat(1001) })).toEqual({ ok: false, field: 'notes' });
    const noNotes = build('plan-create', { ...ok, notes: '' });
    expect(noNotes.ok && 'notes' in noNotes.body).toBe(false);
  });
});

describe('aksi rencana berversi', () => {
  it('calculate/submit/approve: POST + row_version', () => {
    for (const [name, tail] of [['plan-calculate', 'calculate'], ['plan-submit', 'submit'], ['plan-approve', 'approve']] as const) {
      expect(build(name, { id: ID, row_version: 3 })).toEqual({ ok: true, method: 'POST', path: `/v1/plans/${ID}/${tail}`, body: { row_version: 3 } });
    }
  });
  it('id/row_version tidak sah ditolak; path tidak bisa disisipi', () => {
    expect(build('plan-submit', { id: '../../v1/outbox', row_version: 1 })).toEqual({ ok: false, field: 'id' });
    expect(build('plan-submit', { id: `${ID}/x`, row_version: 1 })).toEqual({ ok: false, field: 'id' });
    expect(build('plan-submit', { id: ID })).toEqual({ ok: false, field: 'row_version' });
    expect(build('plan-submit', { id: ID, row_version: 0 })).toEqual({ ok: false, field: 'row_version' });
  });
  it('cancel dan return wajib alasan; reject transfer wajib alasan, approve tidak', () => {
    expect(build('plan-cancel', { id: ID, row_version: 2, reason: ' salah tanggal ' })).toEqual({ ok: true, method: 'POST', path: `/v1/plans/${ID}/cancel`, body: { row_version: 2, reason: 'salah tanggal' } });
    expect(build('plan-cancel', { id: ID, row_version: 2 })).toEqual({ ok: false, field: 'reason' });
    expect(build('plan-return', { id: ID, row_version: 2, reason: '' })).toEqual({ ok: false, field: 'reason' });
    expect(build('plan-return', { id: ID, row_version: 2, reason: 'revisi' })).toMatchObject({ ok: true, path: `/v1/plans/${ID}/return-to-draft` });
    expect(build('transfer-reject', { id: ID, tid: 4, row_version: 2 })).toEqual({ ok: false, field: 'reason' });
    expect(build('transfer-reject', { id: ID, tid: 4, row_version: 2, reason: 'stok dipakai' })).toEqual({
      ok: true,
      method: 'POST',
      path: `/v1/plans/${ID}/transfers/4/reject`,
      body: { row_version: 2, reason: 'stok dipakai' },
    });
    expect(build('transfer-approve', { id: ID, tid: 4, row_version: 2 })).toEqual({ ok: true, method: 'POST', path: `/v1/plans/${ID}/transfers/4/approve`, body: { row_version: 2 } });
    expect(build('transfer-approve', { id: ID, tid: 'x', row_version: 2 })).toEqual({ ok: false, field: 'tid' });
  });
  it('usulan batch', () => {
    expect(build('suggestion-accept', { id: ID, sid: 9, row_version: 1 })).toEqual({ ok: true, method: 'POST', path: `/v1/plans/${ID}/suggestions/9/accept`, body: { row_version: 1 } });
    expect(build('suggestion-reject', { id: ID, sid: 9, row_version: 1 })).toMatchObject({ path: `/v1/plans/${ID}/suggestions/9/reject` });
    expect(build('suggestion-accept', { id: ID, sid: 0, row_version: 1 })).toEqual({ ok: false, field: 'sid' });
  });
});

describe('plan-post', () => {
  const base = { id: ID, row_version: 4, phase: 'production', digest: DIGEST };
  it('membawa fase, digest, accept_shortage (bawaan false); hanya true persis yang berlaku', () => {
    expect(build('plan-post', base)).toEqual({ ok: true, method: 'POST', path: `/v1/plans/${ID}/post`, body: { row_version: 4, phase: 'production', digest: DIGEST, accept_shortage: false } });
    expect(build('plan-post', { ...base, accept_shortage: true })).toMatchObject({ body: { accept_shortage: true } });
    expect(build('plan-post', { ...base, accept_shortage: 'true' })).toMatchObject({ body: { accept_shortage: false } });
  });
  it('fase/digest tidak sah ditolak', () => {
    expect(build('plan-post', { ...base, phase: 'all' })).toEqual({ ok: false, field: 'phase' });
    expect(build('plan-post', { ...base, digest: 'abc' })).toEqual({ ok: false, field: 'digest' });
    expect(build('plan-post', { ...base, digest: DIGEST.toUpperCase() })).toEqual({ ok: false, field: 'digest' });
    expect(build('plan-post', { ...base, digest: undefined })).toEqual({ ok: false, field: 'digest' });
  });
});

describe('plan-demand', () => {
  const line = { product_detail_id: 101, bom_id: 55, qty: '30', source: 'manual' };
  it('PUT mengganti seluruh baris; qty string -> number; pr_nums hanya untuk sumber pr', () => {
    const r = build('plan-demand', {
      id: ID,
      row_version: 2,
      lines: [line, { product_detail_id: 102, bom_id: 56, qty: 20.5, source: 'pr', pr_nums: ['PR-1', 'PR 2', '<x>'] }, { ...line, product_detail_id: 103, pr_nums: ['PR-9'] }],
    });
    expect(r).toEqual({
      ok: true,
      method: 'PUT',
      path: `/v1/plans/${ID}/demand`,
      body: {
        row_version: 2,
        lines: [
          { product_detail_id: 101, bom_id: 55, qty: 30, source: 'manual' },
          { product_detail_id: 102, bom_id: 56, qty: 20.5, source: 'pr', pr_nums: ['PR-1', 'PR 2'] },
          { product_detail_id: 103, bom_id: 55, qty: 30, source: 'manual' },
        ],
      },
    });
  });
  it('baris kosong boleh (menghapus semua baris non-suggestion)', () => {
    expect(build('plan-demand', { id: ID, row_version: 2, lines: [] })).toMatchObject({ ok: true, body: { lines: [] } });
  });
  it('sumber suggestion, qty nol, ID tidak sah, ganda, atau > 500 baris -> ditolak', () => {
    const bad = (lines: unknown) => build('plan-demand', { id: ID, row_version: 2, lines });
    expect(bad([{ ...line, source: 'suggestion' }])).toEqual({ ok: false, field: 'lines' });
    expect(bad([{ ...line, qty: 0 }])).toEqual({ ok: false, field: 'lines' });
    expect(bad([{ ...line, qty: '1.23456' }])).toEqual({ ok: false, field: 'lines' });
    expect(bad([{ ...line, product_detail_id: 'x' }])).toEqual({ ok: false, field: 'lines' });
    expect(bad([{ ...line, bom_id: 0 }])).toEqual({ ok: false, field: 'lines' });
    expect(bad([line, line])).toEqual({ ok: false, field: 'lines' });
    expect(bad(Array.from({ length: 501 }, (_, i) => ({ ...line, product_detail_id: i + 1 })))).toEqual({ ok: false, field: 'lines' });
    expect(bad(Array.from({ length: 500 }, (_, i) => ({ ...line, product_detail_id: i + 1 }))).ok).toBe(true);
    expect(bad('x')).toEqual({ ok: false, field: 'lines' });
    expect(bad(['x'])).toEqual({ ok: false, field: 'lines' });
  });
});

describe('import PR, resend, pack-rule', () => {
  it('import PR: tanggal sah, dari <= sampai', () => {
    expect(build('plan-import-pr', { id: ID, required_date_from: '2026-11-24', required_date_to: '2026-11-25' })).toEqual({
      ok: true,
      method: 'POST',
      path: `/v1/plans/${ID}/import-pr`,
      body: { required_date_from: '2026-11-24', required_date_to: '2026-11-25' },
    });
    expect(build('plan-import-pr', { id: ID, required_date_from: '2026-11-25', required_date_to: '2026-11-24' })).toEqual({ ok: false, field: 'required_date_to' });
    expect(build('plan-import-pr', { id: ID, required_date_from: 'x', required_date_to: '2026-11-24' })).toEqual({ ok: false, field: 'required_date_from' });
  });
  it('resend: hanya UUID outbox, badan kosong', () => {
    expect(build('outbox-resend', { oid: ID })).toEqual({ ok: true, method: 'POST', path: `/v1/outbox/${ID}/resend`, body: {} });
    expect(build('outbox-resend', { oid: '1' })).toEqual({ ok: false, field: 'oid' });
  });
  it('pack-rule-put: PUT /v1/pack-rules/{brand}/{kode}; path di-encode; validasi isian', () => {
    const r = build('pack-rule-put', { brand: 'pb', product_code: 'RMT000021', base_uom: 'ML', pack_qty: '1000', opened_shelf_life_hours: '48', notes: ' susu ', active: true, row_version: 3 });
    expect(r).toEqual({
      ok: true,
      method: 'PUT',
      path: '/v1/pack-rules/pb/RMT000021',
      body: { base_uom: 'ML', pack_qty: 1000, active: true, opened_shelf_life_hours: 48, notes: 'susu', row_version: 3 },
    });
    const minimal = build('pack-rule-put', { brand: 'pb', product_code: 'A-1', base_uom: 'KG', pack_qty: 25, opened_shelf_life_hours: '', notes: '', active: false });
    expect(minimal).toEqual({ ok: true, method: 'PUT', path: '/v1/pack-rules/pb/A-1', body: { base_uom: 'KG', pack_qty: 25, active: false } });
    const base = { brand: 'pb', product_code: 'A', base_uom: 'ML', pack_qty: 1 };
    expect(build('pack-rule-put', { ...base, product_code: '../x' })).toEqual({ ok: false, field: 'product_code' });
    expect(build('pack-rule-put', { ...base, product_code: 'a/b' })).toEqual({ ok: false, field: 'product_code' });
    expect(build('pack-rule-put', { ...base, brand: 'PB' })).toEqual({ ok: false, field: 'brand' });
    expect(build('pack-rule-put', { ...base, base_uom: '' })).toEqual({ ok: false, field: 'base_uom' });
    expect(build('pack-rule-put', { ...base, pack_qty: 0 })).toEqual({ ok: false, field: 'pack_qty' });
    expect(build('pack-rule-put', { ...base, opened_shelf_life_hours: 721 })).toEqual({ ok: false, field: 'opened_shelf_life_hours' });
    expect(build('pack-rule-put', { ...base, opened_shelf_life_hours: 0 })).toEqual({ ok: false, field: 'opened_shelf_life_hours' });
    expect(build('pack-rule-put', { ...base, row_version: 'x' })).toEqual({ ok: false, field: 'row_version' });
    expect(build('pack-rule-put', { ...base, notes: 'x'.repeat(501) })).toEqual({ ok: false, field: 'notes' });
  });
});
