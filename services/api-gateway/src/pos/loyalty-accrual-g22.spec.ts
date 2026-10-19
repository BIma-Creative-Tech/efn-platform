import { Logger } from '@nestjs/common';
import { FakeLoyaltyDb } from '../common/testing/fake-loyalty-db';
import { testConfig } from '../common/testing/fakes';
import { LoyaltyAccrualService, classifyFlows } from './loyalty-accrual.service';

/** G22 §14.C.5 / §14.D.5 (L-67): akrual — alasan online & hangus tidak aktif (entri eksplisit) ke expired_value_idr. */

const H = 'c'.repeat(64);

function row(id: number, delta: number, reason: string, source: string, createdAt: string, issuer = 'GL', company = 'GL') {
  return {
    id,
    member_ref_hash: H,
    member_ref_enc: Buffer.alloc(40, 1),
    company_code: company,
    delta_points: delta,
    reason,
    source,
    source_ref: 'p1',
    idempotency_key: `k${id}`,
    expires_at: null,
    issuer_company_code: issuer,
    created_at: createdAt,
  };
}

describe('Akrual G22', () => {
  it('inactivity_expire masuk expired_value_idr penerbit (bukan forfeited); liabilitas penutup turun', async () => {
    const db = new FakeLoyaltyDb();
    db.ledger.push(row(1, 100, 'pos_sale', 'pos_earn', '2026-10-05T03:00:00.000Z'));
    db.ledger.push(row(2, -100, 'inactivity_expire', 'loyalty_expiry', '2026-10-06T03:00:00.000Z'));
    const svc = new LoyaltyAccrualService(testConfig(), db);
    svc.now = () => Date.UTC(2026, 9, 10);
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    try {
      const r = await svc.accrual('GL', '2026-10-06');
      expect(r).toMatchObject({ expired_value_idr: 10_000, forfeited_value_idr: 0, closing_liability_idr: 0, earned_value_idr: 0 });
      const before = await svc.accrual('GL', '2026-10-05');
      expect(before).toMatchObject({ earned_value_idr: 10_000, expired_value_idr: 0, closing_liability_idr: 10_000 });
    } finally {
      log.mockRestore();
    }
  });

  it('online_redeem/online_restore/online_reverse/online_earn diklasifikasikan seperti padanan POS', () => {
    const out = classifyFlows('GL', [
      { issuer: 'GL', company_code: 'GL', reason: 'online_earn', credit: true, flow_points: 50 },
      { issuer: 'PB', company_code: 'GL', reason: 'online_redeem', credit: false, flow_points: -30 },
      { issuer: 'PB', company_code: 'GL', reason: 'online_restore', credit: true, flow_points: 10 },
      { issuer: 'GL', company_code: 'GL', reason: 'online_reverse', credit: false, flow_points: -5 },
      { issuer: 'GL', company_code: 'GL', reason: 'inactivity_expire', credit: false, flow_points: -7 },
    ]);
    expect(out.earned).toBe(50);
    expect(out.redeemedByIssuer.get('PB')).toBe(30);
    expect(out.restored).toBe(10);
    expect(out.earnReversed).toBe(5);
    expect(out.forfeited).toBe(0);
  });
});
