import { Logger, Module } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { MetricsService } from '../common/metrics/metrics.service';
import { AuthModule } from '../auth/auth.module';
import { CatalogModule } from '../catalog/catalog.module';
import { ODOO_RPC_GATES, OdooRpcGates, createJson2Transport } from '../odoo-rpc/json2-client';
import { OdooRpcModule } from '../odoo-rpc/odoo-rpc.module';
import { BookingController, BookingEventHmacGuard, BookingPosController } from './booking.controller';
import { BOOKING_REPO, BookingRepo, PgBookingRepo } from './booking.repository';
import { BookingService } from './booking.service';
import { BookingMetrics, Json2OdooBookingClient, MockOdooBooking, ODOO_BOOKING, OdooBookingApi, OdooBookingError } from './odoo-booking';

/**
 * Reservasi meja (docs/04 §15; G23 W-35; ADR-013 dibuka ulang: modul CE `efn_table_booking`, bukan pos_appointment EE).
 * Kill switch `BOOKING_ENABLED` (default false). Klien json2 lazy → BFF dengan booking mati tidak butuh secret booking.
 */
@Module({
  imports: [OdooRpcModule, AuthModule, CatalogModule],
  controllers: [BookingController, BookingPosController],
  providers: [
    { provide: BookingMetrics, inject: [MetricsService], useFactory: (m: MetricsService): BookingMetrics => new BookingMetrics(m) },
    { provide: BOOKING_REPO, inject: [DB], useFactory: (db: Db): BookingRepo => new PgBookingRepo(db, db) },
    {
      provide: ODOO_BOOKING,
      inject: [AppConfig, ODOO_RPC_GATES, BookingMetrics],
      useFactory: (config: AppConfig, gates: OdooRpcGates, metrics: BookingMetrics): OdooBookingApi => {
        let impl: OdooBookingApi | null = null;
        const logger = new Logger('OdooBooking');
        const get = (): OdooBookingApi => {
          if (impl) return impl;
          try {
            if (config.odooOnlineMock) {
              logger.warn('ODOO_ONLINE_MOCK aktif — stub reservasi in-memory (DEV/TEST SAJA)');
              impl = new MockOdooBooking();
            } else {
              impl = new Json2OdooBookingClient(config, createJson2Transport(config), gates, metrics);
            }
          } catch (err) {
            logger.error(`Klien Odoo booking tidak dapat dibuat: ${err instanceof Error ? err.message : 'config'}`);
            throw new OdooBookingError('auth');
          }
          return impl;
        };
        return {
          availability: (...a) => get().availability(...a),
          create: (...a) => get().create(...a),
          cancel: (...a) => get().cancel(...a),
          status: (...a) => get().status(...a),
        };
      },
    },
    BookingService,
    BookingEventHmacGuard,
  ],
})
export class BookingModule {}
