import { Logger, Module } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { AuthModule } from '../auth/auth.module';
import { AltchaService } from '../auth/altcha.service';
import { LoyaltyModule } from '../loyalty/loyalty.module';
import { ODOO_RPC_GATES, OdooRpcGates, createJson2Transport } from '../odoo-rpc/json2-client';
import { OdooRpcModule } from '../odoo-rpc/odoo-rpc.module';
import { PosModule } from '../pos/pos.module';
import { PgFnbEarn } from './fnb-earn';
import { FnbEventHmacGuard } from './fnb-event.guard';
import { FnbJobsService } from './fnb-jobs.service';
import { FnbMetrics } from './fnb-metrics';
import { FNB_EARN, FnbPosService } from './fnb-pos.service';
import { FnbController, FnbPosController } from './fnb.controller';
import { FNB_REPO, FnbRepo, PgFnbRepo } from './fnb.repository';
import { FNB_ALTCHA, FnbService } from './fnb.service';
import { Json2OdooQrClient, MockOdooQr, ODOO_QR, OdooQrApi, OdooQrError } from './odoo-qr';

/**
 * QR meja F&B (docs/04 §14.A; ADR-032 Accepted opsi B; L-70..L-76). Kill switch `FNB_QR_ENABLED` (default false).
 * Klien json2 QR lazy (mTLS + API key per PT dibaca saat panggilan pertama) → BFF dengan QR mati tidak butuh secret QR.
 */
@Module({
  imports: [OdooRpcModule, AuthModule, LoyaltyModule, PosModule],
  controllers: [FnbController, FnbPosController],
  providers: [
    FnbMetrics,
    { provide: FNB_REPO, inject: [DB], useFactory: (db: Db): FnbRepo => new PgFnbRepo(db, db) },
    { provide: FNB_ALTCHA, useExisting: AltchaService },
    { provide: FNB_EARN, useClass: PgFnbEarn },
    {
      provide: ODOO_QR,
      inject: [AppConfig, ODOO_RPC_GATES, FnbMetrics],
      useFactory: (config: AppConfig, gates: OdooRpcGates, metrics: FnbMetrics): OdooQrApi => {
        let impl: OdooQrApi | null = null;
        const logger = new Logger('OdooQr');
        const get = (): OdooQrApi => {
          if (impl) return impl;
          try {
            if (config.odooOnlineMock) {
              logger.warn('ODOO_ONLINE_MOCK aktif — stub QR meja in-memory (DEV/TEST SAJA)');
              impl = new MockOdooQr();
            } else {
              impl = new Json2OdooQrClient(config, createJson2Transport(config), gates, metrics);
            }
          } catch (err) {
            logger.error(`Klien Odoo QR tidak dapat dibuat: ${err instanceof Error ? err.message : 'config'}`);
            throw new OdooQrError('auth', null, 'klien Odoo QR tidak terkonfigurasi');
          }
          return impl;
        };
        return {
          menu: (...a) => get().menu(...a),
          submit: (...a) => get().submit(...a),
          status: (...a) => get().status(...a),
          isCircuitOpen: (c) => (impl ? impl.isCircuitOpen(c) : false),
        };
      },
    },
    FnbService,
    FnbPosService,
    FnbJobsService,
    FnbEventHmacGuard,
  ],
})
export class FnbModule {}
