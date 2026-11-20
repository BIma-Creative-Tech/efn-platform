import { Logger, Module } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { ODOO_RPC_GATES, OdooRpcGates, createJson2Transport } from '../odoo-rpc/json2-client';
import { OdooRpcModule } from '../odoo-rpc/odoo-rpc.module';
import { CampaignDispatcherConsumer } from './campaign-dispatcher.consumer';
import { CampaignInboxController, CampaignInboxService } from './campaign-inbox.controller';
import { CampaignSyncService } from './campaign-sync.service';
import { CHANNEL_BINDING_IMPORTS, channelBindingProviders } from './channel-bindings';
import { Json2OdooCampaignClient, MockOdooCampaign, ODOO_CAMPAIGN, OdooCampaignApi, OdooCampaignError } from './odoo-campaign';
import { SegmentIngestService } from './segment-ingest.service';
import { SegmentInternalServer } from './segment-internal.server';

/**
 * G50-A: kampanye tertarget (docs/04 §28.7, ADR-047 Proposed; L-155..L-157, L-164). Kill switch
 * `PROMO_CAMPAIGN_ENABLED` (default false, production OFF sampai DPO + Marketing + Finance). Klien json2 lazy → BFF
 * dengan kampanye mati tidak butuh API key kampanye.
 */
@Module({
  imports: [OdooRpcModule, ...CHANNEL_BINDING_IMPORTS],
  controllers: [CampaignInboxController],
  providers: [
    {
      provide: ODOO_CAMPAIGN,
      inject: [AppConfig, ODOO_RPC_GATES],
      useFactory: (config: AppConfig, gates: OdooRpcGates): OdooCampaignApi => {
        let impl: OdooCampaignApi | null = null;
        const logger = new Logger('OdooCampaign');
        const get = (): OdooCampaignApi => {
          if (impl) return impl;
          try {
            impl = config.odooOnlineMock ? new MockOdooCampaign() : new Json2OdooCampaignClient(config, createJson2Transport(config), gates);
          } catch (err) {
            logger.error(`Klien Odoo kampanye tidak dapat dibuat: ${err instanceof Error ? err.message : 'config'}`);
            throw new OdooCampaignError('auth');
          }
          return impl;
        };
        return { claim: (...a) => get().claim(...a), ack: (...a) => get().ack(...a) };
      },
    },
    CampaignSyncService,
    CampaignDispatcherConsumer,
    CampaignInboxService,
    SegmentIngestService,
    SegmentInternalServer,
    ...channelBindingProviders,
  ],
})
export class CampaignModule {}
