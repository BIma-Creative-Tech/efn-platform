import { Module } from '@nestjs/common';
import { CatalogModule } from '../catalog/catalog.module';
import { CHANNEL_BINDING_IMPORTS, channelBindingProviders } from '../campaign/channel-bindings';
import { AlertDispatcherConsumer } from './alert-dispatcher.consumer';
import { AlertWatcherService } from './alert-watcher.service';
import { WishlistController } from './wishlist.controller';
import { WishlistService } from './wishlist.service';

/**
 * G50-A: wishlist, notify-me, price alert (docs/04 §28.7–§28.8, ADR-047 Proposed). Kill switch `WISHLIST_ENABLED`,
 * `RESTOCK_ALERT_ENABLED`, `PRICE_ALERT_ENABLED` (default false). Kanal email/WA dari BFF-B (`channel-bindings.ts`).
 */
@Module({
  imports: [CatalogModule, ...CHANNEL_BINDING_IMPORTS],
  controllers: [WishlistController],
  providers: [WishlistService, AlertWatcherService, AlertDispatcherConsumer, ...channelBindingProviders],
})
export class WishlistModule {}
