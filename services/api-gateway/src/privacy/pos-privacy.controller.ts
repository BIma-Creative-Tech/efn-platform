import { Body, Controller, Get, HttpCode, HttpStatus, Logger, Param, Post, Query, UseGuards } from '@nestjs/common';
import { Errors } from '../common/errors/api-error';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { parseErasureComplete, parseErasureStoreQuery, parseRequestId } from './dto/privacy.dto';
import { ErasureService } from './erasure.service';

/**
 * Pull erasure oleh Odoo (docs/04 §8.4), service key, in-cluster saja (`/v1/pos` tidak dirutekan publik).
 * `GET /v1/pos/privacy/erasures?store=odoo` → `{ items: [{ request_id, member_hash }] }` (pola §8.9, dibakukan lead);
 * `POST /v1/pos/privacy/erasures/:request_id/complete` `{ store: "odoo", records: n }` → `200 { ok, status }`.
 * Hanya `store=odoo` lewat HTTP; ClickHouse lewat event `privacy.erasure.completed`.
 */
@Controller('pos/privacy')
@UseGuards(ServiceKeyGuard)
export class PosPrivacyController {
  private readonly logger = new Logger(PosPrivacyController.name);

  constructor(private readonly erasure: ErasureService) {}

  @Get('erasures')
  async list(@Query('store') store: unknown): Promise<{ items: { request_id: string; member_hash: string }[] }> {
    return { items: await this.erasure.pendingFor(parseErasureStoreQuery(store)) };
  }

  @Post('erasures/:request_id/complete')
  @HttpCode(HttpStatus.OK)
  async complete(@Param('request_id') requestId: string, @Body() body: unknown): Promise<{ ok: true; status: string }> {
    const id = parseRequestId(requestId);
    const dto = parseErasureComplete(body);
    const res = await this.erasure.completeStore(dto.store, id);
    if (!res.ok) {
      if (res.reason === 'not_found') throw Errors.notFound();
      throw Errors.conflict('not_processing', 'Permintaan penghapusan tidak dalam status diproses.');
    }
    this.logger.log(`Erasure ${id} store=${dto.store} selesai (${dto.records} record) → ${res.status}`);
    return { ok: true, status: res.status };
  }
}
