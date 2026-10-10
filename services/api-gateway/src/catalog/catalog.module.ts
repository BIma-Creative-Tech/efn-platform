import { Module } from '@nestjs/common';
import { OdooRpcModule } from '../odoo-rpc/odoo-rpc.module';
import { catalogStorageProvider } from './catalog-storage';
import { CatalogSyncService } from './catalog-sync.service';
import { CatalogController } from './catalog.controller';
import { CatalogService } from './catalog.service';

/** Katalog online publik + sinkron delta dari Odoo (docs/04 §10.3.2, §10.8.2; ADR-034 Proposed). */
@Module({
  imports: [OdooRpcModule],
  controllers: [CatalogController],
  providers: [CatalogService, CatalogSyncService, catalogStorageProvider],
  exports: [CatalogService],
})
export class CatalogModule {}
