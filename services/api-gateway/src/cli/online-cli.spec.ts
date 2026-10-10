import { operatorIdentity } from './online-cli';
import { parseApproveArgs } from './online-refund-approve';
import { parseRefundArgs } from './online-refund';
import { parseSettleArgs } from './online-refund-settle';
import { parseResolveArgs } from './online-resolve';

const PART = '5e0c3a2b-1d4f-4e6a-8b7c-9d0e1f2a3b4c';

describe('CLI CS online (L-3)', () => {
  const OP = 'cs_budi/efn-prod-01';

  it('online-refund: --lines atau --full (salah satu), alasan wajib; aktor dari OS (--actor ditolak, SEC-98)', () => {
    expect(parseRefundArgs([`--part=${PART}`, '--lines=11:1,12:2', '--reason=barang rusak', '--restock'], OP)).toEqual({
      partId: PART,
      lines: [
        { product_id: 11, qty: 1 },
        { product_id: 12, qty: 2 },
      ],
      full: false,
      restock: true,
      reason: 'customer_cs',
      actor: OP,
      note: 'barang rusak',
    });
    expect(parseRefundArgs([`--part=${PART}`, '--full', '--reason=ops batal', '--kind=ops_cancelled'], OP).full).toBe(true);
    for (const bad of [
      [`--part=${PART}`, '--reason=x'],
      [`--part=${PART}`, '--full', '--lines=1:1', '--reason=x'],
      ['--part=bukan-uuid', '--full', '--reason=x'],
      [`--part=${PART}`, '--full'],
      [`--part=${PART}`, '--full', '--reason=x', '--actor=orang.lain'],
      [`--part=${PART}`, '--full', '--reason=x', '--amount=1000'],
      [`--part=${PART}`, '--lines=11:0', '--reason=x'],
    ]) {
      expect(() => parseRefundArgs(bad, OP)).toThrow();
    }
  });

  it('SEC-98: identitas operator = EFN_CS_OPERATOR / SUDO_USER / user OS + host; karakter dibersihkan (tanpa email)', () => {
    expect(operatorIdentity({ EFN_CS_OPERATOR: 'budi', EFN_CS_HOST: 'efn-prod-01' }, () => 'app', () => 'c0ffee')).toBe('budi/efn-prod-01');
    expect(operatorIdentity({ SUDO_USER: 'ani' }, () => 'app', () => 'host1')).toBe('ani/host1');
    expect(operatorIdentity({}, () => 'app', () => 'host1')).toBe('app/host1');
    expect(operatorIdentity({ EFN_CS_OPERATOR: 'ani@efn.co.id' }, () => 'app', () => 'h')).toBe('ani_efn.co.id/h');
    expect(() => operatorIdentity({}, () => '', () => 'h')).toThrow();
  });

  it('online-refund-settle, online-resolve, online-refund-approve (tanpa --actor)', () => {
    expect(parseSettleArgs([`--refund=${PART}`, '--provider-ref=TRF-1', '--reason=manual'], OP)).toMatchObject({ refundId: PART, providerRef: 'TRF-1', actor: OP });
    expect(parseResolveArgs([`--part=${PART}`, '--action=sync', '--reason=cek'], OP)).toMatchObject({ action: 'sync', actor: OP });
    expect(parseApproveArgs([`--refund=${PART}`, '--reason=tiket CS-12'], OP)).toEqual({ refundId: PART, actor: OP, note: 'tiket CS-12' });
    expect(() => parseResolveArgs([`--part=${PART}`, '--action=delete', '--reason=cek'], OP)).toThrow();
    expect(() => parseSettleArgs([`--refund=${PART}`, '--provider-ref=TRF-1', '--reason=m', '--actor=x'], OP)).toThrow();
  });
});
