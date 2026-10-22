import { Controller, Get, Param, Query, Req, Res } from '@nestjs/common';
import { FastifyReply, FastifyRequest } from 'fastify';
import { AppConfig, ONLINE_CHANNEL_RE } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { BRAND_CODE_RE } from '../odoo-rpc/odoo-online.parse';
import { OnlineErrors } from '../odoo-rpc/online-errors';
import { CatalogService, ChannelInfo, ProductSummary } from './catalog.service';
import { limitByIp, requireCatalog } from './online-http';

const OUTLET_RE = /^[A-Z0-9][A-Z0-9-]{1,23}$/;
const CONTROL_RE = /[\u0000-\u001f\u007f]/;

function channelParam(v: unknown): string {
  if (typeof v !== 'string' || !ONLINE_CHANNEL_RE.test(v)) throw Errors.invalidRequest();
  return v;
}
/** `?brand=` (§11.3 no. 3): tidak ada → null; ada tetapi bukan string berbentuk brand_code → 400 invalid_request. */
export function brandParam(v: unknown): string | null {
  if (v === undefined) return null;
  if (typeof v !== 'string' || !BRAND_CODE_RE.test(v)) throw OnlineErrors.invalidInput();
  return v;
}
function optInt(v: unknown, min: number, max: number): number | null {
  if (v === undefined || v === '') return null;
  if (typeof v !== 'string' || !/^\d{1,10}$/.test(v)) throw Errors.invalidRequest();
  const n = Number(v);
  if (n < min || n > max) throw Errors.invalidRequest();
  return n;
}

/**
 * Katalog PUBLIK (docs/04 §10.3.2): tanpa login, rate limit per IP (HMAC), `Cache-Control: public, max-age=60`;
 * gambar `public, max-age=31536000, immutable` (alamat berbasis hash). Kill switch `ONLINE_CATALOG_ENABLED`.
 */
@Controller('catalog')
export class CatalogController {
  constructor(
    private readonly config: AppConfig,
    private readonly catalog: CatalogService,
    private readonly limiter: RateLimiter,
  ) {}

  private async guard(req: FastifyRequest, reply: FastifyReply, name: 'CATALOG' | 'STOCK' = 'CATALOG'): Promise<void> {
    requireCatalog(this.config);
    await limitByIp(this.limiter, this.config, name, req);
    void reply.header('Cache-Control', name === 'STOCK' ? 'no-store' : 'public, max-age=60');
  }

  /**
   * G19 §11.3 no. 3: `?brand=<brand_code>` opsional (tanpa parameter = semua kanal aktif). Nilai tidak cocok
   * `^[A-Z][A-Z0-9]{1,9}$` (termasuk kosong, huruf kecil, atau parameter berulang) → `400 invalid_request`. Pembatasan UI
   * saja: cart/quote/order tidak bergantung pada parameter ini.
   */
  @Get('channels')
  async channels(
    @Query('brand') brand: unknown,
    @Req() req: FastifyRequest,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<{ items: ChannelInfo[] }> {
    await this.guard(req, reply);
    return { items: await this.catalog.channels(brandParam(brand)) };
  }

  @Get('categories')
  async categories(@Query('channel') channel: unknown, @Req() req: FastifyRequest, @Res({ passthrough: true }) reply: FastifyReply): Promise<unknown> {
    await this.guard(req, reply);
    return this.catalog.categories(channelParam(channel));
  }

  @Get('products')
  async products(
    @Query('channel') channel: unknown,
    @Query('category') category: unknown,
    @Query('q') q: unknown,
    @Query('cursor') cursor: unknown,
    @Query('limit') limit: unknown,
    @Req() req: FastifyRequest,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<unknown> {
    await this.guard(req, reply);
    let text: string | null = null;
    if (q !== undefined && q !== '') {
      if (typeof q !== 'string' || q.length > 64 || CONTROL_RE.test(q)) throw Errors.invalidRequest();
      text = q.trim() || null;
    }
    if (cursor !== undefined && (typeof cursor !== 'string' || cursor.length > 40)) throw Errors.invalidRequest();
    return this.catalog.products(channelParam(channel), {
      category: optInt(category, 1, 2_147_483_647),
      q: text,
      cursor: typeof cursor === 'string' && cursor !== '' ? cursor : null,
      limit: optInt(limit, 1, 50) ?? 20,
    });
  }

  @Get('products/:product_id')
  async product(
    @Param('product_id') productId: string,
    @Query('channel') channel: unknown,
    @Req() req: FastifyRequest,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<ProductSummary & { description: string }> {
    await this.guard(req, reply);
    if (!/^\d{1,10}$/.test(productId)) throw Errors.notFound();
    return this.catalog.product(channelParam(channel), Number(productId));
  }

  @Get('images/:image_sha')
  async image(
    @Param('image_sha') sha: string,
    @Query('size') size: unknown,
    @Req() req: FastifyRequest,
    @Res() reply: FastifyReply,
  ): Promise<void> {
    // G22 §14.A.3: gambar menu QR memakai endpoint ini → juga aktif bila FNB_QR_ENABLED walau katalog online mati
    if (!this.config.fnbQrEnabled) requireCatalog(this.config);
    await limitByIp(this.limiter, this.config, 'CATALOG', req);
    const s = size === undefined || size === '' ? 512 : size === '512' ? 512 : size === '1024' ? 1024 : null;
    if (s === null) throw Errors.invalidRequest();
    const data = await this.catalog.image(sha, s);
    void reply
      .header('Content-Type', 'image/jpeg')
      .header('Cache-Control', 'public, max-age=31536000, immutable')
      .header('X-Content-Type-Options', 'nosniff')
      .send(data);
  }

  /** G23 §15.4: `GET /v1/catalog/slots?channel=&outlet=&date=YYYY-MM-DD` (publik, cache 30 s). */
  @Get('slots')
  async slots(
    @Query('channel') channel: unknown,
    @Query('outlet') outlet: unknown,
    @Query('date') date: unknown,
    @Req() req: FastifyRequest,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<unknown> {
    await this.guard(req, reply, 'STOCK');
    void reply.header('Cache-Control', 'public, max-age=30');
    if (typeof outlet !== 'string' || !OUTLET_RE.test(outlet)) throw Errors.invalidRequest();
    if (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`))) throw Errors.invalidRequest();
    return this.catalog.slots(channelParam(channel), outlet, date);
  }

  @Get('stock')
  async stock(
    @Query('channel') channel: unknown,
    @Query('outlet') outlet: unknown,
    @Query('ids') ids: unknown,
    @Req() req: FastifyRequest,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<unknown> {
    await this.guard(req, reply, 'STOCK');
    if (typeof outlet !== 'string' || !OUTLET_RE.test(outlet)) throw Errors.invalidRequest();
    if (typeof ids !== 'string' || !/^\d{1,10}(,\d{1,10}){0,49}$/.test(ids)) throw Errors.invalidRequest();
    return this.catalog.stock(channelParam(channel), outlet, ids.split(',').map(Number));
  }
}
