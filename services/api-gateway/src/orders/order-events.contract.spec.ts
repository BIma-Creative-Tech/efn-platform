import { eventFingerprint, parseOdooEvent } from './order-events.service';

/**
 * Kontrak event Odoo → BFF (docs/04 §10.5 + §12.2 no. 7 + L-57/L-58). Fixture = bentuk PERSIS yang dibuat
 * `efn.online.event.outbox._efn_payload()` (efn_online_order/models/efn_online_event_outbox.py), termasuk field
 * tambahan yang tidak dipakai BFF. Tes ini mengunci sisi penerima; sisi pengirim dikunci
 * `efn_online_pos_counter/tests/test_online_pos_counter.py` (payload `review_reason`, `refund_settled`, `payment`).
 */
const REF = '3f2b8c1e-5d4a-4e7b-9c3d-0a1b2c3d4e5f';
const CHECKOUT = '7a6b5c4d-3e2f-4a1b-8c9d-0e1f2a3b4c5d';
const POS_UUID = '0f1e2d3c-4b5a-4968-8776-655443322110';

function odooPayload(over: Record<string, unknown>): Buffer {
  const base = {
    schema_version: 1,
    idempotency_key: `online:${REF}:paid:1000`,
    event_id: '11111111-2222-4333-8444-555555555555',
    seq: 1000,
    platform_order_ref: REF,
    checkout_ref: CHECKOUT,
    channel_code: 'GL',
    company_code: 'GL',
    event: 'paid',
    state: 'confirmed',
    occurred_at: '2026-10-19T03:00:00Z',
    refund: null,
  };
  return Buffer.from(JSON.stringify({ ...base, ...over }), 'utf8');
}

describe('kontrak event Odoo → BFF (G20)', () => {
  it('paid bayar di kasir: payment.method=counter diterima; metode ikut sidik', () => {
    const ev = parseOdooEvent(odooPayload({ payment: { method: 'counter' } }));
    expect(ev.payment_method).toBe('counter');
    expect(ev.company_code).toBe('GL');
    const plain = parseOdooEvent(odooPayload({}));
    expect(eventFingerprint(ev)).not.toBe(eventFingerprint(plain));
  });

  it('L-57: review membawa review_reason counter_paid_after_*; alasan lain Odoo (amount_mismatch) → null, event tetap sah', () => {
    const late = parseOdooEvent(odooPayload({ event: 'review', state: 'review', review_reason: 'counter_paid_after_cancel' }));
    expect(late.review_reason).toBe('counter_paid_after_cancel');
    const expiry = parseOdooEvent(odooPayload({ event: 'review', state: 'review', review_reason: 'counter_paid_after_expiry' }));
    expect(expiry.review_reason).toBe('counter_paid_after_expiry');
    // Odoo mengirim kode review apa pun (tanpa PII); BFF hanya memakai dua nilai di atas.
    const other = parseOdooEvent(odooPayload({ event: 'review', state: 'review', review_reason: 'amount_mismatch' }));
    expect(other.review_reason).toBeNull();
    const none = parseOdooEvent(odooPayload({ event: 'review', state: 'review', review_reason: null }));
    expect(none.review_reason).toBeNull();
  });

  it('L-58: refund_settled bentuk Odoo (kind/initiator ikut, provider_refund_ref = pos:<uuid>, settled_at ISO Z)', () => {
    const ev = parseOdooEvent(
      odooPayload({
        event: 'refund_settled',
        idempotency_key: `online:${REF}:refund_settled:1001`,
        seq: 1001,
        refund: {
          refund_ref: 'RF-0001',
          amount: 159800,
          kind: 'full',
          initiator: 'bff',
          provider_refund_ref: `pos:${POS_UUID}`,
          settled_at: '2026-10-19T03:05:00Z',
        },
      }),
    );
    expect(ev.settlement).toMatchObject({ refund_ref: 'RF-0001', amount: 159800, provider_refund_ref: `pos:${POS_UUID}` });
    expect(ev.settlement?.settled_at?.toISOString()).toBe('2026-10-19T03:05:00.000Z');
    expect(ev.refund).toBeNull(); // jalur refund biasa tidak dipakai untuk refund_settled
  });

  it('L-58: refund_settled tanpa pos:<uuid> / nominal pecahan / tanpa refund → 400', () => {
    const bad = [
      { refund_ref: 'RF-0001', amount: 100, provider_refund_ref: 'gw-r1', settled_at: null },
      { refund_ref: 'RF-0001', amount: 100.5, provider_refund_ref: `pos:${POS_UUID}`, settled_at: null },
      { refund_ref: '', amount: 100, provider_refund_ref: `pos:${POS_UUID}`, settled_at: null },
    ];
    for (const refund of bad) expect(() => parseOdooEvent(odooPayload({ event: 'refund_settled', refund }))).toThrow();
    expect(() => parseOdooEvent(odooPayload({ event: 'refund_settled', refund: null }))).toThrow();
  });
});
