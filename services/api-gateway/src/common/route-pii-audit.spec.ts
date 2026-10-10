import { ROUTE_ARGS_METADATA } from '@nestjs/common/constants';
import { RouteParamtypes } from '@nestjs/common/enums/route-paramtypes.enum';
import { AuthController } from '../auth/auth.controller';
import { CartController } from '../cart/cart.controller';
import { CatalogController } from '../catalog/catalog.controller';
import { OrderEventsController, OrdersController } from '../orders/orders.controller';
import { MeController } from '../auth/me.controller';
import { HealthController } from '../health/health.controller';
import { MarketScoutController } from '../market-scout/market-scout.controller';
import { MigrationController } from '../migration/migration.controller';
import { PaymentWebhookController, PosPaymentsController } from '../payments/payments.controller';
import { PosLoyaltyController } from '../pos/pos-loyalty.controller';
import { PosController } from '../pos/pos.controller';
import { PosPrivacyController } from '../privacy/pos-privacy.controller';
import { ConsentController, DsarController, DsarReceiptController } from '../privacy/privacy.controller';

/**
 * R2 (docs/04 §8.13): nomor HP, token, dan identitas tidak pernah dikirim lewat URL/query (masuk access log).
 * Audit statis: setiap `@Query()`/`@Param()` di semua controller harus ada di allowlist berisi pengenal NON-PII
 * (UUID request/payment, enum). Menambah parameter URL baru = tinjau dulu lalu tambahkan di sini.
 */
// Fase 3 (§10.3): kode kanal/outlet, id kategori/produk Odoo, teks pencarian katalog, cursor opak, ukuran gambar.
const ALLOWED_QUERY = new Set(['store', 'channel', 'category', 'q', 'cursor', 'limit', 'size', 'outlet', 'ids', 'confirm_open_orders']); // confirm_open_orders: boolean W-18, non-PII
const ALLOWED_PARAM = new Set(['id', 'payment_id', 'provider', 'request_id', 'purpose', 'product_id', 'image_sha', 'order_id']);

const CONTROLLERS: (new (...args: never[]) => unknown)[] = [
  AuthController,
  MeController,
  HealthController,
  MarketScoutController,
  PosPaymentsController,
  PaymentWebhookController,
  PosController,
  PosLoyaltyController,
  MigrationController,
  PosPrivacyController,
  ConsentController,
  DsarController,
  DsarReceiptController,
  CatalogController,
  CartController,
  OrdersController,
  OrderEventsController,
];

describe('Audit URL tanpa PII (R2)', () => {
  it('semua @Query/@Param ada di allowlist non-PII; tidak ada @Query() utuh', () => {
    const found: string[] = [];
    for (const ctrl of CONTROLLERS) {
      for (const method of Object.getOwnPropertyNames(ctrl.prototype)) {
        if (method === 'constructor') continue;
        const args = (Reflect.getMetadata(ROUTE_ARGS_METADATA, ctrl, method) ?? {}) as Record<string, { data?: unknown }>;
        for (const [key, meta] of Object.entries(args)) {
          const type = Number(key.split(':')[0]);
          if (type !== RouteParamtypes.QUERY && type !== RouteParamtypes.PARAM) continue;
          const name = typeof meta.data === 'string' ? meta.data : '<semua>';
          found.push(`${ctrl.name}.${method}:${type === RouteParamtypes.QUERY ? 'query' : 'param'}:${name}`);
          const allowed = type === RouteParamtypes.QUERY ? ALLOWED_QUERY : ALLOWED_PARAM;
          expect({ where: `${ctrl.name}.${method}`, name, ok: allowed.has(name) }).toEqual({
            where: `${ctrl.name}.${method}`,
            name,
            ok: true,
          });
        }
      }
    }
    // Sanity: audit benar-benar membaca metadata (bukan lolos karena kosong)
    expect(found).toEqual(
      expect.arrayContaining(['PosPrivacyController.list:query:store', 'DsarReceiptController.status:param:id', 'OrdersController.get:param:order_id']),
    );
    expect(found.some((f) => f.includes('member_ref'))).toBe(false);
  });
});
