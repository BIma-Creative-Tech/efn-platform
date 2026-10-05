import { GUARDS_METADATA } from '@nestjs/common/constants';
import { FastifyRequest } from 'fastify';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { catchApiError, httpContext, testConfig } from '../common/testing/fakes';
import { PosController } from './pos.controller';
import { PosService } from './pos.service';

function fakeReq(ip = '10.0.0.5'): FastifyRequest {
  return { ip, headers: {} } as unknown as FastifyRequest;
}

describe('PosController', () => {
  const pos = {
    requestOtp: jest.fn().mockResolvedValue({ challenge_id: 'c', expires_in: 180 }),
    verifyOtp: jest.fn(),
    consumeToken: jest.fn().mockResolvedValue({ ok: true }),
    balance: jest.fn().mockResolvedValue({ points_balance: 5 }),
  };
  const controller = new PosController(pos as unknown as PosService);

  beforeEach(() => jest.clearAllMocks());

  it('seluruh controller dilindungi ServiceKeyGuard', () => {
    const guards = Reflect.getMetadata(GUARDS_METADATA, PosController) as unknown[];
    expect(guards).toContain(ServiceKeyGuard);
  });

  it('request: body valid diteruskan ter-normalisasi beserta IP klien', async () => {
    await controller.requestOtp(
      { member_ref: '6281234567890', order_ref: '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10', amount: 125000, company_code: 'FNB', pos_config_id: 3 },
      fakeReq(),
    );
    expect(pos.requestOtp).toHaveBeenCalledWith(
      { memberRef: '6281234567890', orderRef: '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10', amount: 125000, companyCode: 'FNB', posConfigId: 3 },
      '10.0.0.5',
    );
  });

  it.each([
    [{}],
    [{ member_ref: 'abc', order_ref: 'x', amount: 1, company_code: 'FNB', pos_config_id: 1 }],
    [{ member_ref: '6281234567890', order_ref: 'x', amount: 1, company_code: 'XYZ', pos_config_id: 1 }],
    [{ member_ref: '6281234567890', order_ref: '', amount: 1, company_code: 'FNB', pos_config_id: 1 }],
    [{ member_ref: '6281234567890', order_ref: 'x'.repeat(65), amount: 1, company_code: 'FNB', pos_config_id: 1 }],
    [{ member_ref: '6281234567890', order_ref: 'ordér-ü', amount: 1, company_code: 'FNB', pos_config_id: 1 }],
    [{ member_ref: '6281234567890', order_ref: 'x', amount: -1, company_code: 'FNB', pos_config_id: 1 }],
    [{ member_ref: '6281234567890', order_ref: 'x', amount: 1, company_code: 'FNB', pos_config_id: 1.5 }],
  ])('request: body tidak valid → 400 invalid_request / invalid_phone (%j)', async (body) => {
    const err = await catchApiError(() => controller.requestOtp(body, fakeReq()));
    expect(err.getStatus()).toBe(400);
    expect(err.body).toEqual({ error: { code: expect.stringMatching(/^invalid_(request|phone)$/), message: expect.any(String) } });
    expect(pos.requestOtp).not.toHaveBeenCalled();
  });

  it('verify: challenge_id bukan UUID → 400', async () => {
    const err = await catchApiError(() =>
      controller.verifyOtp({ challenge_id: 'not-a-uuid', order_ref: 'x', code: '123456' }, fakeReq()),
    );
    expect(err.getStatus()).toBe(400);
  });

  it('loyalty balance: member_ref query wajib valid', async () => {
    expect(await controller.balance('6281234567890')).toEqual({ points_balance: 5 });
    const err = await catchApiError(() => controller.balance(undefined));
    expect(err.getStatus()).toBe(400);
  });

  it('consume: member_ref (SEC-07) diteruskan ter-normalisasi', async () => {
    await controller.consume({ discount_token: 'tok', order_ref: 'o-1', member_ref: '081234567890', amount_total: 125000 });
    expect(pos.consumeToken).toHaveBeenCalledWith({
      discountToken: 'tok',
      orderRef: 'o-1',
      memberRef: '6281234567890',
      amountTotal: 125000,
    });
  });
});

describe('ServiceKeyGuard', () => {
  const config = testConfig();
  const guard = new ServiceKeyGuard(config);

  it('key benar → lolos', () => {
    const req = { headers: { 'x-efn-service-key': config.odooBffApiKey } };
    expect(guard.canActivate(httpContext(req))).toBe(true);
  });

  it.each([[undefined], [''], ['salah'], [['array', 'header']]])('key %j → 401 unauthorized', async (key) => {
    const req = { headers: { 'x-efn-service-key': key } };
    const err = await catchApiError(() => guard.canActivate(httpContext(req)));
    expect(err.getStatus()).toBe(401);
    expect(err.body).toEqual({ error: { code: 'unauthorized', message: expect.any(String) } });
  });

  it('key dengan prefix benar tetapi lebih panjang → 401', async () => {
    const req = { headers: { 'x-efn-service-key': `${config.odooBffApiKey}x` } };
    const err = await catchApiError(() => guard.canActivate(httpContext(req)));
    expect(err.getStatus()).toBe(401);
  });
});
