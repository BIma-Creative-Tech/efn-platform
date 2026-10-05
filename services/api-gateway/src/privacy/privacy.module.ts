import { Module } from '@nestjs/common';
import { LoyaltyModule } from '../loyalty/loyalty.module';
import { objectStorageProvider } from '../market-scout/storage/object-storage';
import { OtpModule } from '../otp/otp.module';
import { ConsentService } from './consent.service';
import { DsarService } from './dsar.service';
import { ErasureCompletedConsumer } from './erasure-completed.consumer';
import { ErasureService } from './erasure.service';
import { PosPrivacyController } from './pos-privacy.controller';
import { ConsentController, DsarController } from './privacy.controller';

/** Privasi UU PDP (docs/04 §8.4, §8.7, §8.9): consent, DSAR, erasure lintas-store. */
@Module({
  imports: [OtpModule, LoyaltyModule],
  controllers: [ConsentController, DsarController, PosPrivacyController],
  providers: [ConsentService, DsarService, ErasureService, ErasureCompletedConsumer, objectStorageProvider],
  exports: [ConsentService],
})
export class PrivacyModule {}
