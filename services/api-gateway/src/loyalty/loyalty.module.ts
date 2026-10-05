import { Module } from '@nestjs/common';
import { LoyaltyEarnConsumer } from './loyalty-earn.consumer';
import { LoyaltyService } from './loyalty.service';

@Module({
  providers: [LoyaltyService, LoyaltyEarnConsumer],
  exports: [LoyaltyService],
})
export class LoyaltyModule {}
