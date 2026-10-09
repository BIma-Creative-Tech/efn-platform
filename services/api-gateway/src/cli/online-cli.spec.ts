import { parseRefundArgs } from './online-refund';
import { parseSettleArgs } from './online-refund-settle';
import { parseResolveArgs } from './online-resolve';

const PART = '5e0c3a2b-1d4f-4e6a-8b7c-9d0e1f2a3b4c';

describe('CLI CS online (L-3)', () => {
  it('online-refund: --lines atau --full (salah satu), aktor & alasan wajib', () => {
    expect(parseRefundArgs([`--part=${PART}`, '--lines=11:1,12:2', '--actor=cs.budi', '--reason=barang rusak', '--restock'])).toEqual({
      partId: PART,
      lines: [
        { product_id: 11, qty: 1 },
        { product_id: 12, qty: 2 },
      ],
      full: false,
      restock: true,
      reason: 'customer_cs',
      actor: 'cs.budi',
      note: 'barang rusak',
    });
    expect(parseRefundArgs([`--part=${PART}`, '--full', '--actor=a.b', '--reason=ops batal', '--kind=ops_cancelled']).full).toBe(true);
    for (const bad of [
      [`--part=${PART}`, '--actor=a', '--reason=x'],
      [`--part=${PART}`, '--full', '--lines=1:1', '--actor=a', '--reason=x'],
      ['--part=bukan-uuid', '--full', '--actor=a', '--reason=x'],
      [`--part=${PART}`, '--full', '--reason=x'],
      [`--part=${PART}`, '--full', '--actor=a', '--reason=x', '--amount=1000'],
      [`--part=${PART}`, '--lines=11:0', '--actor=a', '--reason=x'],
    ]) {
      expect(() => parseRefundArgs(bad)).toThrow();
    }
  });

  it('online-refund-settle & online-resolve', () => {
    expect(parseSettleArgs([`--refund=${PART}`, '--provider-ref=TRF-1', '--actor=fin', '--reason=manual'])).toMatchObject({ refundId: PART, providerRef: 'TRF-1' });
    expect(parseResolveArgs([`--part=${PART}`, '--action=sync', '--actor=ops', '--reason=cek'])).toMatchObject({ action: 'sync' });
    expect(() => parseResolveArgs([`--part=${PART}`, '--action=delete', '--actor=ops', '--reason=cek'])).toThrow();
  });
});
