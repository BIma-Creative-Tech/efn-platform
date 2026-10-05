import { Logger } from '@nestjs/common';
import { testConfig } from '../../common/testing/fakes';
import { CloudApiOptions, CloudApiWhatsAppSender, FetchInit, FetchLike } from './cloud-api-whatsapp.sender';
import { ConsoleWhatsAppSender } from './console-whatsapp.sender';
import { createWhatsAppSender } from './whatsapp.module';

const TO = '6281234567890';

function options(over: Partial<CloudApiOptions> = {}): CloudApiOptions {
  return {
    baseUrl: 'https://graph.example.test/',
    apiVersion: 'v21.0',
    phoneNumberId: '1234567890',
    token: 'dummy-token-for-test-only',
    otpTemplate: 'efn_otp',
    languageCode: 'id',
    otpCopyCodeButton: true,
    timeoutMs: 1000,
    ...over,
  };
}

describe('CloudApiWhatsAppSender', () => {
  it('mengirim template OTP ke endpoint Graph dengan body + tombol copy-code', async () => {
    const calls: { url: string; init: FetchInit }[] = [];
    const fetchImpl: FetchLike = async (url, init) => {
      calls.push({ url, init });
      return { ok: true, status: 200, json: async () => ({ messages: [{ id: 'wamid.x' }] }) };
    };
    const sender = new CloudApiWhatsAppSender(options(), fetchImpl);
    await sender.sendOtp(TO, '123456');

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('https://graph.example.test/v21.0/1234567890/messages');
    expect(calls[0].init.headers.Authorization).toBe('Bearer dummy-token-for-test-only');
    const body = JSON.parse(calls[0].init.body) as {
      to: string;
      type: string;
      template: { name: string; language: { code: string }; components: { type: string }[] };
    };
    expect(body.to).toBe(TO);
    expect(body.type).toBe('template');
    expect(body.template.name).toBe('efn_otp');
    expect(body.template.language.code).toBe('id');
    expect(body.template.components.map((c) => c.type)).toEqual(['body', 'button']);
  });

  it('error HTTP → Error tanpa token, kode, maupun nomor utuh', async () => {
    const fetchImpl: FetchLike = async () => ({ ok: false, status: 401, json: async () => ({ error: { code: 190 } }) });
    const sender = new CloudApiWhatsAppSender(options(), fetchImpl);
    let message = '';
    try {
      await sender.sendOtp(TO, '654321');
    } catch (err) {
      message = (err as Error).message;
    }
    expect(message).toContain('401');
    expect(message).toContain('190');
    expect(message).not.toContain('654321');
    expect(message).not.toContain(TO);
    expect(message).not.toContain('dummy-token');
  });

  it('menolak konfigurasi versi API / phone number id yang tidak valid', () => {
    expect(() => new CloudApiWhatsAppSender(options({ apiVersion: '21' }))).toThrow();
    expect(() => new CloudApiWhatsAppSender(options({ phoneNumberId: '12/../x' }))).toThrow();
  });
});

describe('ConsoleWhatsAppSender', () => {
  it('tidak pernah me-log kode OTP di luar development', async () => {
    const logs: string[] = [];
    const spyLog = jest.spyOn(Logger.prototype, 'log').mockImplementation((m: unknown) => void logs.push(String(m)));
    const spyWarn = jest.spyOn(Logger.prototype, 'warn').mockImplementation((m: unknown) => void logs.push(String(m)));
    await new ConsoleWhatsAppSender(false).sendOtp(TO, '987654');
    expect(logs.join('\n')).not.toContain('987654');
    expect(logs.join('\n')).not.toContain(TO);
    spyLog.mockRestore();
    spyWarn.mockRestore();
  });

  it('di development kode dicetak tetapi nomor tetap di-mask', async () => {
    const logs: string[] = [];
    const spyWarn = jest.spyOn(Logger.prototype, 'warn').mockImplementation((m: unknown) => void logs.push(String(m)));
    await new ConsoleWhatsAppSender(true).sendOtp(TO, '987654');
    expect(logs.join('\n')).toContain('987654');
    expect(logs.join('\n')).toContain('62812****890');
    expect(logs.join('\n')).not.toContain(TO);
    spyWarn.mockRestore();
  });
});

describe('createWhatsAppSender', () => {
  it('WHATSAPP_ENABLED=false → console sender', () => {
    jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    expect(createWhatsAppSender(testConfig({ WHATSAPP_ENABLED: 'false' }))).toBeInstanceOf(ConsoleWhatsAppSender);
  });

  it('WHATSAPP_ENABLED=true → Cloud API sender', () => {
    const sender = createWhatsAppSender(
      testConfig({ WHATSAPP_ENABLED: 'true', WHATSAPP_TOKEN: 'x'.repeat(40), WHATSAPP_PHONE_NUMBER_ID: '1234' }),
    );
    expect(sender).toBeInstanceOf(CloudApiWhatsAppSender);
  });
});
