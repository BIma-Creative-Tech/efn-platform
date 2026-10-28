import { Logger, Module } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { AuthModule } from '../auth/auth.module';
import { catalogStorageProvider } from '../catalog/catalog-storage';
import { ODOO_RPC_GATES, OdooRpcGates, createJson2Transport } from '../odoo-rpc/json2-client';
import { OdooRpcModule } from '../odoo-rpc/odoo-rpc.module';
import { Json2OdooPromoClient, MockOdooPromo, ODOO_PROMO, OdooPromoApi, OdooPromoError } from './odoo-promo';
import { PromoController, PushTokenController } from './promo.controller';
import { PUSH_SENDER, PromoService, PushSender } from './promo.service';
import { ConsolePushSender, ExpoPushSender } from './push-sender';

/**
 * Promo Super-App G27 (docs/04 §18; CLAUDE.md K-27): banner slider, pop-up, push promo. Dikelola di Odoo (`efn_promo`).
 * Klien json2 lazy → BFF dengan promo mati tidak butuh API key promo. Push: Expo bila `PROMO_PUSH_ENABLED` dan bukan
 * dev; dev = console (tanpa pengiriman nyata).
 */
@Module({
  imports: [OdooRpcModule, AuthModule],
  controllers: [PromoController, PushTokenController],
  providers: [
    catalogStorageProvider,
    {
      provide: ODOO_PROMO,
      inject: [AppConfig, ODOO_RPC_GATES],
      useFactory: (config: AppConfig, gates: OdooRpcGates): OdooPromoApi => {
        let impl: OdooPromoApi | null = null;
        const logger = new Logger('OdooPromo');
        const get = (): OdooPromoApi => {
          if (impl) return impl;
          try {
            impl = config.odooOnlineMock ? new MockOdooPromo() : new Json2OdooPromoClient(config, createJson2Transport(config), gates);
          } catch (err) {
            logger.error(`Klien Odoo promo tidak dapat dibuat: ${err instanceof Error ? err.message : 'config'}`);
            throw new OdooPromoError('auth');
          }
          return impl;
        };
        return {
          feed: (...a) => get().feed(...a),
          image: (...a) => get().image(...a),
          pushClaim: (...a) => get().pushClaim(...a),
        };
      },
    },
    {
      provide: PUSH_SENDER,
      inject: [AppConfig],
      useFactory: (config: AppConfig): PushSender => (config.isDevelopment ? new ConsolePushSender() : new ExpoPushSender(config.expoPushUrl)),
    },
    PromoService,
  ],
})
export class PromoModule {}
