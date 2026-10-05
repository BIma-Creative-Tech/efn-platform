import { createHmac } from 'node:crypto';
import { RawBodyRequest } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { catchApiError, httpContext, randomSecret, testConfig } from '../testing/fakes';
import { WebhookHmacGuard } from './webhook-hmac.guard';
import { computeWebhookSignature, parseSignatureHeader, verifyWebhookSignature } from './webhook-signature';

const SECRET = randomSecret();
const NOW = 1_790_000_000;
const BODY = Buffer.from('{"event":"payment.paid","id":"evt_1","amount":125000}');

function header(t: number, body: Buffer = BODY, secret: string = SECRET): string {
  return `t=${t},v1=${computeWebhookSignature(secret, t, body)}`;
}

describe('verifyWebhookSignature', () => {
  it('signature valid dalam window → ok', () => {
    expect(verifyWebhookSignature({ header: header(NOW - 10), rawBody: BODY, secret: SECRET, nowSeconds: NOW })).toEqual({
      ok: true,
      timestamp: NOW - 10,
    });
  });

  it('string yang ditandatangani = "<t>." + raw body', () => {
    const expected = createHmac('sha256', SECRET).update(`${NOW}.${BODY.toString('utf8')}`).digest('hex');
    expect(computeWebhookSignature(SECRET, NOW, BODY)).toBe(expected);
  });

  it('body diubah satu byte → signature_mismatch', () => {
    const tampered = Buffer.from(BODY.toString().replace('125000', '125001'));
    expect(verifyWebhookSignature({ header: header(NOW), rawBody: tampered, secret: SECRET, nowSeconds: NOW })).toEqual({
      ok: false,
      reason: 'signature_mismatch',
    });
  });

  it('secret salah → signature_mismatch', () => {
    const res = verifyWebhookSignature({ header: header(NOW, BODY, randomSecret()), rawBody: BODY, secret: SECRET, nowSeconds: NOW });
    expect(res).toEqual({ ok: false, reason: 'signature_mismatch' });
  });

  it('replay di luar 300 s (lampau & masa depan) → timestamp_out_of_window', () => {
    for (const t of [NOW - 301, NOW + 301]) {
      expect(verifyWebhookSignature({ header: header(t), rawBody: BODY, secret: SECRET, nowSeconds: NOW })).toEqual({
        ok: false,
        reason: 'timestamp_out_of_window',
      });
    }
  });

  it('mengganti t tanpa menandatangani ulang → mismatch (timestamp ikut ditandatangani)', () => {
    const sig = computeWebhookSignature(SECRET, NOW - 1000, BODY);
    const res = verifyWebhookSignature({ header: `t=${NOW},v1=${sig}`, rawBody: BODY, secret: SECRET, nowSeconds: NOW });
    expect(res).toEqual({ ok: false, reason: 'signature_mismatch' });
  });

  it('beberapa v1 (rotasi secret): salah satu cocok → ok', () => {
    const good = computeWebhookSignature(SECRET, NOW, BODY);
    const bad = computeWebhookSignature(randomSecret(), NOW, BODY);
    const res = verifyWebhookSignature({ header: `t=${NOW},v1=${bad},v1=${good}`, rawBody: BODY, secret: SECRET, nowSeconds: NOW });
    expect(res.ok).toBe(true);
  });

  it.each([
    [undefined, 'missing_signature'],
    ['', 'missing_signature'],
    ['v1=abc', 'malformed_signature'],
    [`t=${NOW}`, 'malformed_signature'],
    [`t=abc,v1=${'a'.repeat(64)}`, 'malformed_signature'],
    [`t=${NOW},v1=${'z'.repeat(64)}`, 'malformed_signature'],
    [`t=${NOW},t=${NOW},v1=${'a'.repeat(64)}`, 'malformed_signature'],
  ])('header %j → %s', (h, reason) => {
    expect(verifyWebhookSignature({ header: h, rawBody: BODY, secret: SECRET, nowSeconds: NOW })).toEqual({ ok: false, reason });
  });

  it('raw body tidak tersedia → missing_body', () => {
    expect(verifyWebhookSignature({ header: header(NOW), rawBody: undefined, secret: SECRET, nowSeconds: NOW })).toEqual({
      ok: false,
      reason: 'missing_body',
    });
  });

  it('parseSignatureHeader mengabaikan skema tak dikenal', () => {
    expect(parseSignatureHeader(`t=${NOW},v0=xyz,v1=${'A'.repeat(64)}`)).toEqual({ timestamp: NOW, signatures: ['a'.repeat(64)] });
  });
});

describe('WebhookHmacGuard', () => {
  const config = testConfig();
  const guard = new WebhookHmacGuard(config);
  guard.now = () => NOW * 1000;

  function req(h: string | undefined, raw: Buffer | undefined): RawBodyRequest<FastifyRequest> {
    return { headers: { 'x-efn-signature': h }, rawBody: raw } as unknown as RawBodyRequest<FastifyRequest>;
  }

  it('lolos dengan signature benar atas raw body', () => {
    const h = `t=${NOW},v1=${computeWebhookSignature(config.webhookPaymentSecret, NOW, BODY)}`;
    expect(guard.canActivate(httpContext(req(h, BODY)))).toBe(true);
  });

  it('signature salah → 401 invalid_signature', async () => {
    const err = await catchApiError(() => guard.canActivate(httpContext(req(header(NOW), BODY))));
    expect(err.getStatus()).toBe(401);
    expect(err.body).toEqual({ error: { code: 'invalid_signature', message: expect.any(String) } });
  });
});
