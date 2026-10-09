import { Module } from '@nestjs/common';
import { CatalogModule } from '../catalog/catalog.module';
import { OdooRpcModule } from '../odoo-rpc/odoo-rpc.module';
import { cartStoreProvider } from './cart-store';
import { CartController } from './cart.controller';
import { CartService } from './cart.service';
import { QuoteService } from './quote.service';

/** Cart Redis per member + quote Odoo (docs/04 §10.3.3–10.3.4; ADR-034 Proposed). */
@Module({
  imports: [CatalogModule, OdooRpcModule],
  controllers: [CartController],
  providers: [CartService, QuoteService, cartStoreProvider],
  exports: [CartService, QuoteService],
})
export class CartModule {}
