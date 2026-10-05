import { BadRequestException, Logger, NotFoundException } from '@nestjs/common';
import { ApiError, Errors } from './api-error';
import { ContractExceptionFilter } from './contract-exception.filter';

describe('ContractExceptionFilter', () => {
  const filter = new ContractExceptionFilter();
  beforeAll(() => jest.spyOn(Logger.prototype, 'error').mockImplementation(() => undefined));

  it('ApiError diteruskan apa adanya (termasuk header)', () => {
    const res = filter.toResponse(Errors.rateLimited(30));
    expect(res.status).toBe(429);
    expect(res.body).toEqual({ error: { code: 'rate_limited', message: expect.any(String) } });
    expect(res.headers).toEqual({ 'Retry-After': '30' });
  });

  it('body khusus OTP ({ok:false,...}) tidak dibungkus ulang', () => {
    const res = filter.toResponse(new ApiError(400, { ok: false, reason: 'locked', attempts_left: 0 }));
    expect(res.body).toEqual({ ok: false, reason: 'locked', attempts_left: 0 });
  });

  it('HttpException Nest → format kontrak, pesan library tidak bocor', () => {
    expect(filter.toResponse(new NotFoundException('Cannot GET /v1/rahasia'))).toEqual({
      status: 404,
      body: { error: { code: 'not_found', message: 'Data tidak ditemukan.' } },
      headers: {},
    });
    const bad = filter.toResponse(new BadRequestException('Unexpected token } in JSON at position 3'));
    expect(JSON.stringify(bad.body)).not.toContain('Unexpected token');
  });

  it('error Fastify ber-statusCode 4xx (body terlalu besar) → kode kontrak', () => {
    const err = Object.assign(new Error('Request body is too large'), { statusCode: 413, code: 'FST_ERR_CTP_BODY_TOO_LARGE' });
    expect(filter.toResponse(err)).toEqual({
      status: 413,
      body: { error: { code: 'payload_too_large', message: expect.any(String) } },
      headers: {},
    });
  });

  it('error tak terduga → 500 internal_error tanpa detail', () => {
    const res = filter.toResponse(new Error('password authentication failed for user "efn"'));
    expect(res.status).toBe(500);
    expect(res.body).toEqual({ error: { code: 'internal_error', message: expect.any(String) } });
    expect(JSON.stringify(res.body)).not.toContain('password');
  });
});
