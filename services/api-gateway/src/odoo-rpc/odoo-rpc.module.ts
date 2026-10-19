import { Logger, Module } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { Json2OdooOnlineClient, ODOO_RPC_GATES, OdooRpcGates, createJson2Transport, createOdooRpcGates } from './json2-client';
import { MockOdooOnline } from './odoo-online.mock';
import { ODOO_ONLINE, OdooOnlineApi, OdooRpcError } from './odoo-online.types';
import { OnlineMetrics } from './online-metrics';

/**
 * Klien json2 Odoo untuk kanal online (docs/04 §10.4, ADR-034 Proposed). Provider lazy: transport mTLS dibuat saat
 * panggilan pertama sehingga BFF dengan kanal online mati (`ONLINE_ENABLED=false`, default production) tidak butuh
 * sertifikat/secret kanal. Mock (`ODOO_ONLINE_MOCK=true`) hanya dev/test — `AppConfig.odooOnlineMock` melempar di
 * staging/production (dicek juga saat start, `assertStartupSecrets`).
 */
@Module({
  providers: [
    OnlineMetrics,
    // G22 §14.A.7 (L-20): satu pasang semaphore per proses, dipakai klien online DAN klien QR meja (pool publik)
    { provide: ODOO_RPC_GATES, inject: [AppConfig], useFactory: (config: AppConfig): OdooRpcGates => createOdooRpcGates(config) },
    {
      provide: ODOO_ONLINE,
      inject: [AppConfig, OnlineMetrics, ODOO_RPC_GATES],
      useFactory: (config: AppConfig, metrics: OnlineMetrics, gates: OdooRpcGates): OdooOnlineApi => {
        let impl: OdooOnlineApi | null = null;
        const logger = new Logger('OdooOnline');
        const get = (): OdooOnlineApi => {
          if (impl) return impl;
          try {
            if (config.odooOnlineMock) {
              logger.warn('ODOO_ONLINE_MOCK aktif — stub json2 in-memory (DEV/TEST SAJA)');
              impl = new MockOdooOnline();
            } else {
              impl = new Json2OdooOnlineClient(config, createJson2Transport(config), metrics, gates);
            }
          } catch (err) {
            logger.error(`Klien Odoo online tidak dapat dibuat: ${err instanceof Error ? err.message : 'config'}`);
            throw new OdooRpcError('auth', null, 'klien Odoo online tidak terkonfigurasi');
          }
          return impl;
        };
        return {
          catalog: (...a) => get().catalog(...a),
          stock: (...a) => get().stock(...a),
          image: (...a) => get().image(...a),
          quote: (...a) => get().quote(...a),
          place: (...a) => get().place(...a),
          markPaid: (...a) => get().markPaid(...a),
          cancel: (...a) => get().cancel(...a),
          refund: (...a) => get().refund(...a),
          refundSettled: (...a) => get().refundSettled(...a),
          status: (...a) => get().status(...a),
          counterRefundAuthorize: (...a) => get().counterRefundAuthorize(...a),
          setInvoiceParty: (...a) => get().setInvoiceParty(...a),
          isCircuitOpen: (ch) => (impl ? impl.isCircuitOpen(ch) : false),
        };
      },
    },
  ],
  exports: [ODOO_ONLINE, OnlineMetrics, ODOO_RPC_GATES],
})
export class OdooRpcModule {}
