import { PassThrough } from 'node:stream';
import { Operator } from '../orders/online-admin.service';
import { operatorIdentity, reasonFromStdin } from './online-cli';
import { parseApproveArgs } from './online-refund-approve';
import { parseRefundArgs } from './online-refund';
import { parseSettleArgs } from './online-refund-settle';
import { parseResolveArgs } from './online-resolve';

const PART = '5e0c3a2b-1d4f-4e6a-8b7c-9d0e1f2a3b4c';
const OP: Operator = { actor: 'cs_budi/efn-prod-01', role: 'cs' };
const APPROVER: Operator = { actor: 'spv_sari/efn-prod-01', role: 'approver' };

function stdin(text: string, isTTY = false): PassThrough & { isTTY?: boolean } {
  const s = new PassThrough() as PassThrough & { isTTY?: boolean };
  s.isTTY = isTTY;
  s.end(text);
  return s;
}

describe('CLI CS online (L-3, L-16, L-19)', () => {
  it('online-refund: --lines atau --full (salah satu); aktor & peran dari wrapper (--actor ditolak)', () => {
    expect(parseRefundArgs([`--part=${PART}`, '--lines=11:1,12:2', '--reason=barang rusak', '--restock'], OP)).toEqual({
      partId: PART,
      lines: [
        { product_id: 11, qty: 1 },
        { product_id: 12, qty: 2 },
      ],
      full: false,
      restock: true,
      reason: 'customer_cs',
      actor: OP.actor,
      role: 'cs',
      note: 'barang rusak',
    });
    expect(parseRefundArgs([`--part=${PART}`, '--full', '--kind=ops_cancelled'], OP, 'dari stdin').note).toBe('dari stdin');
    for (const bad of [
      [`--part=${PART}`, '--reason=x'],
      [`--part=${PART}`, '--full', '--lines=1:1', '--reason=x'],
      ['--part=bukan-uuid', '--full', '--reason=x'],
      [`--part=${PART}`, '--full'], // tanpa alasan (argv maupun stdin)
      [`--part=${PART}`, '--full', '--reason=x', '--actor=orang.lain'],
      [`--part=${PART}`, '--full', '--reason=x', '--role=approver'],
      [`--part=${PART}`, '--lines=11:0', '--reason=x'],
    ]) {
      expect(() => parseRefundArgs(bad, OP)).toThrow();
    }
  });

  it('L-19c: identitas + peran WAJIB dari wrapper di luar dev/test; tanpa cadangan node/<container>', () => {
    expect(operatorIdentity({ EFN_CS_OPERATOR: 'budi', EFN_CS_HOST: 'efn-prod-01', EFN_CS_ROLE: 'approver' }, () => 'node', () => 'c0ffee')).toEqual({
      actor: 'budi/efn-prod-01',
      role: 'approver',
    });
    for (const env of [{}, { SUDO_USER: 'ani' }, { EFN_CS_OPERATOR: 'budi' }, { EFN_CS_OPERATOR: 'budi', EFN_CS_ROLE: 'admin' }, { EFN_CS_ROLE: 'cs' }, { APP_ENV: 'production', EFN_CS_ROLE: 'cs' }]) {
      expect(() => operatorIdentity(env, () => 'node', () => 'c0ffee')).toThrow('wrapper');
    }
    // dev/test: cadangan user OS, peran cs kecuali diisi
    expect(operatorIdentity({ APP_ENV: 'dev' }, () => 'node', () => 'host1')).toEqual({ actor: 'node/host1', role: 'cs' });
    expect(operatorIdentity({ APP_ENV: 'test', EFN_CS_ROLE: 'approver', SUDO_USER: 'ani' }, () => 'node', () => 'h')).toEqual({ actor: 'ani/h', role: 'approver' });
    expect(operatorIdentity({ EFN_CS_OPERATOR: 'ani@efn.co.id', EFN_CS_ROLE: 'cs', EFN_CS_HOST: 'h' })).toEqual({ actor: 'ani_efn.co.id/h', role: 'cs' });
  });

  it('SEC-108: alasan dari stdin (wrapper, tanpa --reason di argv) atau --reason-stdin; --reason= di argv tidak membaca stdin', async () => {
    expect(await reasonFromStdin([`--refund=${PART}`], stdin('alasan dari wrapper\nbaris lain'))).toBe('alasan dari wrapper');
    expect(await reasonFromStdin([`--refund=${PART}`, '--reason-stdin'], stdin('eksplisit\n', true))).toBe('eksplisit');
    expect(await reasonFromStdin([`--refund=${PART}`, '--reason=argv'], stdin('diabaikan'))).toBeUndefined();
    expect(await reasonFromStdin([`--refund=${PART}`], stdin('tty', true))).toBeUndefined();
    expect(await reasonFromStdin([`--refund=${PART}`], stdin('   \n'))).toBeUndefined();
    await expect(reasonFromStdin([`--refund=${PART}`], stdin('x'.repeat(3000)))).rejects.toThrow('terlalu panjang');
    expect(parseApproveArgs([`--refund=${PART}`, '--reason-stdin'], APPROVER, 'tiket CS-12')).toEqual({ refundId: PART, operator: APPROVER, note: 'tiket CS-12' });
  });

  it('online-refund-settle, online-resolve, online-refund-approve', () => {
    expect(parseSettleArgs([`--refund=${PART}`, '--provider-ref=TRF-1', '--reason=manual'], OP)).toMatchObject({ refundId: PART, providerRef: 'TRF-1', operator: OP });
    expect(parseResolveArgs([`--part=${PART}`, '--action=sync'], OP, 'cek')).toMatchObject({ action: 'sync', operator: OP, note: 'cek' });
    expect(() => parseResolveArgs([`--part=${PART}`, '--action=delete', '--reason=cek'], OP)).toThrow();
    expect(() => parseSettleArgs([`--refund=${PART}`, '--provider-ref=TRF-1', '--reason=m', '--actor=x'], OP)).toThrow();
    expect(() => parseApproveArgs([`--refund=${PART}`], APPROVER)).toThrow('alasan wajib');
  });
});
