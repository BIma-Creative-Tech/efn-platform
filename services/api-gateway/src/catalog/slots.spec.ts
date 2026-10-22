import { ApiError } from '../common/errors/api-error';
import { parseCartPut } from '../cart/cart.service';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { parseSlots } from '../odoo-rpc/odoo-online.parse';
import { OdooRpcError } from '../odoo-rpc/odoo-online.types';
import { slotIso } from '../orders/orders.service';

/** G23 §15.4 (W-36): slot ambil pre-order — parser, cart, stub Odoo. Alur checkout end-to-end diuji di orders spec. */

function wibSlot(hour: number, daysAhead = 2): string {
  const wibNow = new Date(Date.now() + 7 * 3600_000);
  const d = new Date(Date.UTC(wibNow.getUTCFullYear(), wibNow.getUTCMonth(), wibNow.getUTCDate() + daysAhead));
  return new Date(d.getTime() - 7 * 3600_000 + hour * 3600_000).toISOString().replace('.000Z', 'Z');
}
const dateOf = (iso: string): string => new Date(Date.parse(iso) + 7 * 3600_000).toISOString().slice(0, 10);

describe('slot pre-order', () => {
  it('detail pesanan mengirim pickup_slot dengan bentuk yang sama seperti slot (tanpa milidetik; E2E G23)', () => {
    expect(slotIso(new Date('2026-10-25T05:00:00Z'))).toBe('2026-10-25T05:00:00Z');
    expect(slotIso(null)).toBeNull();
  });
  it('parseSlots memvalidasi bentuk respons Odoo', () => {
    const ok = {
      outlet_code: 'TOKO-01',
      date: '2026-10-25',
      timezone: 'Asia/Jakarta',
      slot_minutes: 60,
      slots: [{ start_at: '2026-10-25T02:00:00Z', end_at: '2026-10-25T03:00:00Z', remaining: 3 }],
    };
    expect(parseSlots(ok).slots).toHaveLength(1);
    for (const bad of [
      { ...ok, slot_minutes: 45 },
      { ...ok, slots: [{ ...ok.slots[0], end_at: '2026-10-25T04:00:00Z' }] },
      { ...ok, slots: [{ ...ok.slots[0], remaining: -1 }] },
      { ...ok, slots: [{ ...ok.slots[0], start_at: '2026-10-25T02:00:30Z' }] },
      { ...ok, date: '25-10-2026' },
    ]) {
      expect(() => parseSlots(bad)).toThrow(OdooRpcError);
    }
  });

  it('cart menerima pickup_slot hanya untuk pickup dan format ISO UTC menit', () => {
    const base = { channel_code: 'PB', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines: [{ product_id: 11, qty: 1 }] };
    const slot = wibSlot(10);
    expect(parseCartPut({ version: 0, groups: [{ ...base, pickup_slot: slot }] }).groups[0].pickup_slot).toBe(slot);
    expect(parseCartPut({ version: 0, groups: [base] }).groups[0]).not.toHaveProperty('pickup_slot');
    for (const g of [
      { ...base, pickup_slot: '2026-10-25T10:00:30Z' },
      { ...base, pickup_slot: 'besok pagi' },
      { ...base, pickup_slot: 123 },
      { ...base, fulfilment: 'delivery', pickup_slot: slot },
    ]) {
      expect(() => parseCartPut({ version: 0, groups: [g] })).toThrow(ApiError);
    }
  });

  describe('stub Odoo', () => {
    it('slots hanya kanal F&B; quote ber-slot mengubah quote_hash; kapasitas habis → issue + place ditolak', async () => {
      const odoo = new MockOdooOnline();
      const slot = wibSlot(11);
      await expect(odoo.slots('GL', 'TOKO-01', dateOf(slot))).rejects.toBeInstanceOf(OdooRpcError);
      const s = await odoo.slots('PB', 'TOKO-01', dateOf(slot));
      expect(s.slots.map((x) => x.start_at)).toContain(slot);
      const lines = [{ product_id: 11, qty: 1 }];
      const plain = await odoo.quote('PB', 'TOKO-01', 'pickup', lines, []);
      const withSlot = await odoo.quote('PB', 'TOKO-01', 'pickup', lines, [], slot);
      expect(withSlot.quote_hash).not.toBe(plain.quote_hash);
      expect(withSlot.issues).toEqual([]);
      odoo.slotCapacity = 1;
      const place = (ref: string) =>
        odoo.place('PB', {
          idempotency_key: `online:place:${ref}`,
          platform_order_ref: ref,
          checkout_ref: ref,
          outlet_code: 'TOKO-01',
          fulfilment: 'pickup',
          lines,
          quote_hash: withSlot.quote_hash,
          promo_codes: [],
          pickup_code: 'ABCD2345',
          pickup_slot: slot,
        });
      await place('00000000-0000-4000-8000-000000000001');
      const full = await odoo.quote('PB', 'TOKO-01', 'pickup', lines, [], slot);
      expect(full.issues).toContainEqual({ product_id: null, code: 'slot_unavailable' });
      await expect(place('00000000-0000-4000-8000-000000000002')).rejects.toMatchObject({ kind: 'business', code: 'slot_unavailable' });
      const after = await odoo.slots('PB', 'TOKO-01', dateOf(slot));
      expect(after.slots.find((x) => x.start_at === slot)?.remaining).toBe(0);
    });

    it('slot di luar jam / terlalu dekat → issue slot_unavailable', async () => {
      const odoo = new MockOdooOnline();
      const lines = [{ product_id: 11, qty: 1 }];
      expect((await odoo.quote('PB', 'TOKO-01', 'pickup', lines, [], wibSlot(20))).issues).toContainEqual({ product_id: null, code: 'slot_unavailable' });
      expect((await odoo.quote('PB', 'TOKO-01', 'pickup', lines, [], wibSlot(10, 30))).issues).toContainEqual({ product_id: null, code: 'slot_unavailable' });
    });
  });
});
