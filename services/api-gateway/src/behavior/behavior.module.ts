import { Module } from '@nestjs/common';
import { BehaviorJobsService } from './behavior-jobs.service';
import { BehaviorWriterConsumer } from './behavior-writer.consumer';
import { BehaviorController, PublicConfigController } from './behavior.controller';
import { BehaviorService } from './behavior.service';
import { PurchaseEventsService } from './purchase-events.service';

/**
 * G50-A: event perilaku visitor (docs/04 §28.6–§28.7, ADR-047 Proposed). Kill switch `BEHAVIOR_TRACKING_ENABLED`
 * (default false, production OFF sampai DPO). Infrastruktur (DB, Redis, AMQP, rate limit, token) dari CommonModule global.
 */
@Module({
  controllers: [BehaviorController, PublicConfigController],
  providers: [BehaviorService, BehaviorWriterConsumer, BehaviorJobsService, PurchaseEventsService],
  exports: [BehaviorService],
})
export class BehaviorModule {}
