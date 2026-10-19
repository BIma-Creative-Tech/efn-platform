import { Module } from '@nestjs/common';
import { DB, Db } from '../common/db/db';
import { CartModule } from '../cart/cart.module';
import { CatalogModule } from '../catalog/catalog.module';
import { OdooRpcModule } from '../odoo-rpc/odoo-rpc.module';
import { ONLINE_PAYMENTS } from '../payments/online-payments';
import { PaymentsModule } from '../payments/payments.module';
import { PaymentsService } from '../payments/payments.service';
import { OnlineEventHmacGuard } from './order-events.guard';
import { OrderEventsService } from './order-events.service';
import { OnlineAdminService } from './online-admin.service';
import { OnlineConsumers } from './online-consumers';
import { OnlineJobsService } from './online-jobs.service';
import { OnlinePrivacyService } from './online-privacy.service';
import { ONLINE_REPO, OnlineRepo, PgOnlineRepo } from './online.repository';
import { OnlineSagaService } from './online-saga.service';
import { LoyaltyModule } from '../loyalty/loyalty.module';
import { OrderEventsController, OrdersController } from './orders.controller';
import { OrdersService } from './orders.service';

/**
 * Order online + saga (docs/04 §10.3.5–10.3.6, §10.5–10.7, §10.9; ADR-034 & ADR-019 Proposed): checkout, status,
 * batal, kode ambil, event Odoo, consumer saga, sweeper/rekonsiliasi/retensi, operasi CS (CLI) dan hook privasi.
 */
@Module({
  imports: [OdooRpcModule, CatalogModule, CartModule, PaymentsModule, LoyaltyModule],
  controllers: [OrdersController, OrderEventsController],
  providers: [
    { provide: ONLINE_REPO, inject: [DB], useFactory: (db: Db): OnlineRepo => new PgOnlineRepo(db, db) },
    { provide: ONLINE_PAYMENTS, useExisting: PaymentsService },
    OnlineSagaService,
    OrdersService,
    OrderEventsService,
    OnlineEventHmacGuard,
    OnlineConsumers,
    OnlineJobsService,
    OnlineAdminService,
    OnlinePrivacyService,
  ],
  exports: [OnlinePrivacyService],
})
export class OrdersModule {}
