import 'reflect-metadata';
import { AuthService } from '../../auth/auth.service';
import { QuoteService } from '../../cart/quote.service';
import { NotifyPushConsumer } from '../../notification/notify-push.consumer';
import { OnlineAdminService } from '../../orders/online-admin.service';
import { PgOnlinePoints } from '../../orders/online-points';
import { OrdersService } from '../../orders/orders.service';
import { LoyaltyAccrualService } from '../../pos/loyalty-accrual.service';
import { PosLoyaltyService } from '../../pos/pos-loyalty.service';
import { LoyaltyInactivityJob } from '../loyalty-inactivity.job';
import { LoyaltyPolicyService } from '../loyalty-policy.service';
import { MgmEngineService } from './mgm-engine.service';
import { MgmPolicyService } from './mgm-policy.service';
import { MgmReferralService } from './mgm-referral.service';

/**
 * Penjaga DI (G31): parameter konstruktor bertipe `X | null` memancarkan metadata `Object`, sehingga Nest menyuntik
 * `null` diam-diam untuk `@Optional()` tanpa `@Inject(X)` (consumer MGM tidak terdaftar; sebelum G31 juga kebijakan
 * poin per PT, saga bayar di kasir, info hangus poin /v1/me). Setiap parameter wajib bertipe kelas nyata atau punya
 * token `@Inject` eksplisit.
 */
const SELF_PARAMTYPES = 'self:paramtypes';
type Ctor = abstract new (...args: never[]) => unknown;

function untokenized(cls: Ctor): number[] {
  const types = (Reflect.getMetadata('design:paramtypes', cls) ?? []) as unknown[];
  const tokens = ((Reflect.getMetadata(SELF_PARAMTYPES, cls) ?? []) as { index: number }[]).map((t) => t.index);
  return types.flatMap((t, i) => (t === Object && !tokens.includes(i) ? [i] : []));
}

function injected(cls: Ctor): unknown[] {
  return ((Reflect.getMetadata(SELF_PARAMTYPES, cls) ?? []) as { param: unknown }[]).map((t) => t.param);
}

describe('metadata DI konstruktor (G31)', () => {
  it.each([
    ['MgmEngineService', MgmEngineService],
    ['MgmPolicyService', MgmPolicyService],
    ['MgmReferralService', MgmReferralService],
    ['PosLoyaltyService', PosLoyaltyService],
    ['PgOnlinePoints', PgOnlinePoints],
    ['LoyaltyPolicyService', LoyaltyPolicyService],
    ['LoyaltyInactivityJob', LoyaltyInactivityJob],
    ['LoyaltyAccrualService', LoyaltyAccrualService],
    ['AuthService', AuthService],
    ['QuoteService', QuoteService],
    ['OrdersService', OrdersService],
    ['OnlineAdminService', OnlineAdminService],
    ['NotifyPushConsumer', NotifyPushConsumer],
  ] as [string, Ctor][])('%s: tidak ada parameter Object tanpa token', (_n, cls) => {
    expect(untokenized(cls)).toEqual([]);
  });

  it('hook MGM di jalur earn POS & online memakai token kelas', () => {
    expect(injected(PosLoyaltyService)).toContain(MgmEngineService);
    expect(injected(PgOnlinePoints)).toContain(MgmEngineService);
  });
});
