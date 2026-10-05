import { Module } from '@nestjs/common';
import { PrivacyModule } from '../privacy/privacy.module';
import { ImageProcessor } from './image/image-processor';
import { MarketScoutController } from './market-scout.controller';
import { MarketScoutService } from './market-scout.service';
import { objectStorageProvider } from './storage/object-storage';

@Module({
  imports: [PrivacyModule],
  controllers: [MarketScoutController],
  providers: [MarketScoutService, ImageProcessor, objectStorageProvider],
})
export class MarketScoutModule {}
