import { Module } from '@nestjs/common';
import { DB, Db } from '../common/db/db';
import { ONLINE_POINTS, PgOnlinePoints } from '../orders/online-points';
import { LoyaltyPolicySignatureGuard } from '../pos/loyalty-policy.guard';
import { EXPIRY_STORE, ExpiryStore, LoyaltyInactivityJob, PgExpiryStore } from './loyalty-inactivity.job';
import { LoyaltyEarnConsumer } from './loyalty-earn.consumer';
import { LoyaltyPolicyService } from './loyalty-policy.service';
import { LoyaltyService } from './loyalty.service';
import { MgmEngineService } from './mgm/mgm-engine.service';
import { MgmPolicyService } from './mgm/mgm-policy.service';
import { MgmReferralService } from './mgm/mgm-referral.service';
import { MgmPolicyController, MgmReferralController } from './mgm/mgm.controller';
import { MGM_STORE, PgMgmStore } from './mgm/mgm.repository';

@Module({
  // G31 docs/04 §20: engine MGM (kill switch MGM_ENABLED, default false). Publikasi kebijakan selalu terbuka (signed).
  controllers: [MgmReferralController, MgmPolicyController],
  providers: [
    LoyaltyService,
    LoyaltyEarnConsumer,
    LoyaltyPolicyService,
    // G22 §14.D (W-17): job hangus tidak aktif — OFF kecuali LOYALTY_EXPIRY_ENABLED=true (L-73)
    { provide: EXPIRY_STORE, inject: [DB, LoyaltyService], useFactory: (db: Db, loyalty: LoyaltyService): ExpiryStore => new PgExpiryStore(db, db, loyalty) },
    LoyaltyInactivityJob,
    // G22 §14.C: poin online (dipakai cart/quote, orders/saga, /v1/me) — kill switch ONLINE_LOYALTY_*_ENABLED
    { provide: ONLINE_POINTS, useClass: PgOnlinePoints },
    // G31 §20
    { provide: MGM_STORE, useClass: PgMgmStore },
    MgmPolicyService,
    MgmReferralService,
    MgmEngineService,
    LoyaltyPolicySignatureGuard,
  ],
  exports: [LoyaltyService, LoyaltyPolicyService, LoyaltyInactivityJob, ONLINE_POINTS, MgmEngineService, MGM_STORE],
})
export class LoyaltyModule {}
