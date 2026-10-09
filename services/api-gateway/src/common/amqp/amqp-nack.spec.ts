import { nackDecision } from './amqp.service';

describe('nackDecision (L-13, docs/04 §10.9)', () => {
  it('queue saga online: transien selalu requeue (broker x-delivery-limit yang men-DLQ), dengan jeda bertahap', () => {
    expect(nackDecision('order.paid', false, false, 0)).toEqual({ requeue: true, delayMs: 1000 });
    expect(nackDecision('order.refund.requested', false, true, 3)).toEqual({ requeue: true, delayMs: 4000 });
    expect(nackDecision('order.compensate', false, true, 9)).toEqual({ requeue: true, delayMs: 5000 });
  });

  it('jaring pengaman: x-delivery-count ≥ 2× limit (broker salah konfigurasi) → DLQ; permanen → DLQ langsung', () => {
    expect(nackDecision('order.unpaid_closed', false, true, 20)).toEqual({ requeue: false, delayMs: 0 });
    expect(nackDecision('order.paid', true, false, 0)).toEqual({ requeue: false, delayMs: 0 });
  });

  it('queue lama (tanpa limit broker) tidak berubah: requeue sekali lalu DLQ', () => {
    for (const q of ['notify.push', 'loyalty.earn.requested', 'privacy.erasure.completed']) {
      expect(nackDecision(q, false, false, 0)).toEqual({ requeue: true, delayMs: 0 });
      expect(nackDecision(q, false, true, 1)).toEqual({ requeue: false, delayMs: 0 });
      expect(nackDecision(q, true, false, 0)).toEqual({ requeue: false, delayMs: 0 });
    }
  });
});
