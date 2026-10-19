import { Module } from '@nestjs/common';
import { LoyaltyEarnConsumer } from './loyalty-earn.consumer';
import { LoyaltyPolicyService } from './loyalty-policy.service';
import { LoyaltyService } from './loyalty.service';

@Module({
  providers: [LoyaltyService, LoyaltyEarnConsumer, LoyaltyPolicyService],
  exports: [LoyaltyService, LoyaltyPolicyService],
})
export class LoyaltyModule {}
