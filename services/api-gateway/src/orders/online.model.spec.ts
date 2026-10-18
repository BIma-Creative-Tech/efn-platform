import { UNPAID_OPEN, canTransitionPart, deriveOrderStatus, isPaymentMethod, orderHasPayment } from './online.model';

describe('G20 §12.2: model bayar di kasir', () => {
  it('awaiting_counter_payment = order belum dibayar terbuka; tidak diturunkan dari bagian', () => {
    expect(UNPAID_OPEN).toEqual(['created', 'awaiting_payment', 'awaiting_counter_payment']);
    expect(deriveOrderStatus('awaiting_counter_payment', ['placed'])).toBe('awaiting_counter_payment');
    expect(deriveOrderStatus('paid', ['confirmed'])).toBe('fulfilling');
  });
  it('isPaymentMethod hanya qris|counter; orderHasPayment: intent ATAU counter + paid_at', () => {
    expect(isPaymentMethod('qris')).toBe(true);
    expect(isPaymentMethod('counter')).toBe(true);
    for (const v of ['COUNTER', 'cash', null, undefined, 1]) expect(isPaymentMethod(v)).toBe(false);
    expect(orderHasPayment({ payment_method: 'qris', payment_intent_id: 'x', paid_at: null })).toBe(true);
    expect(orderHasPayment({ payment_method: 'qris', payment_intent_id: null, paid_at: new Date() })).toBe(false);
    expect(orderHasPayment({ payment_method: 'counter', payment_intent_id: null, paid_at: null })).toBe(false);
    expect(orderHasPayment({ payment_method: 'counter', payment_intent_id: null, paid_at: new Date() })).toBe(true);
  });
});

describe('Transisi bagian monoton (§10.6.2 + L-14)', () => {
  it('naik peringkat diizinkan; mundur ditolak', () => {
    expect(canTransitionPart('placed', 'paying')).toBe(true);
    expect(canTransitionPart('ready', 'confirmed')).toBe(false);
    expect(canTransitionPart('handed_over', 'ready')).toBe(false);
    expect(canTransitionPart('cancelled', 'review')).toBe(false);
    expect(canTransitionPart('paying', 'paying')).toBe(false);
  });
  it('L-14: confirmed|ready → review selalu; review → confirmed|cancelled', () => {
    expect(canTransitionPart('confirmed', 'review')).toBe(true);
    expect(canTransitionPart('ready', 'review')).toBe(true);
    expect(canTransitionPart('handed_over', 'review')).toBe(false);
    expect(canTransitionPart('review', 'confirmed')).toBe(true);
    expect(canTransitionPart('review', 'cancelled')).toBe(true);
  });
  it('status order diturunkan dari bagian setelah paid', () => {
    expect(deriveOrderStatus('paid', ['review'])).toBe('fulfilling');
    expect(deriveOrderStatus('fulfilling', ['handed_over', 'cancelled'])).toBe('completed');
    expect(deriveOrderStatus('fulfilling', ['cancelled'])).toBe('cancelled');
    expect(deriveOrderStatus('awaiting_payment', ['placed'])).toBe('awaiting_payment');
  });
});
