import { testConfig } from '../../common/testing/fakes';
import { AmqpWaPublisher } from './wa-unofficial.publisher';

interface SentCall {
  queue: string;
  body: Buffer;
  opts: Record<string, unknown>;
}

function fakeAmqp(missingQueue?: string) {
  const sent: SentCall[] = [];
  const checked: string[] = [];
  let closed = 0;
  const confirm = {
    on: jest.fn(),
    sendToQueue: (queue: string, body: Buffer, opts: Record<string, unknown>, cb: (err: unknown) => void) => {
      sent.push({ queue, body, opts });
      cb(null);
      return true;
    },
  };
  const probe = {
    on: jest.fn(),
    checkQueue: async (q: string) => {
      checked.push(q);
      if (q === missingQueue) throw Object.assign(new Error('NOT_FOUND'), { code: 404 });
      return {};
    },
    close: async () => undefined,
  };
  const conn = {
    on: jest.fn(),
    createChannel: async () => probe,
    createConfirmChannel: async () => confirm,
    close: async () => {
      closed += 1;
    },
  };
  const connect = jest.fn(async () => conn);
  return { sent, checked, connect, closedCount: () => closed };
}

describe('AmqpWaPublisher (properti expiration, §28.7)', () => {
  const config = testConfig({ AMQP_URL: 'amqp://efn-bff@rabbitmq:5672', EFN_ALLOW_PLAINTEXT: 'true' });

  it('passive check queue + DLQ, publish persistent dengan expiration, message_id, user_id; koneksi dipakai ulang', async () => {
    const f = fakeAmqp();
    const pub = new AmqpWaPublisher(config);
    pub.connectFn = f.connect as never;
    await pub.publish({ a: 1 }, 'wa:k1', 3600_000);
    await pub.publish({ a: 2 }, 'wa:k2', 1000);
    expect(f.connect).toHaveBeenCalledTimes(1);
    expect(f.checked).toEqual(['notify.wa_unofficial.dlq', 'notify.wa_unofficial']);
    expect(f.sent[0]).toMatchObject({
      queue: 'notify.wa_unofficial',
      opts: { persistent: true, messageId: 'wa:k1', userId: 'efn-bff', expiration: '3600000', contentType: 'application/json' },
    });
    expect(JSON.parse(f.sent[1].body.toString('utf8'))).toEqual({ a: 2 });
    await pub.onModuleDestroy();
  });

  it('expiration di luar 1 ms..24 jam → ditolak sebelum konek', async () => {
    const f = fakeAmqp();
    const pub = new AmqpWaPublisher(config);
    pub.connectFn = f.connect as never;
    await expect(pub.publish({}, 'k', 0)).rejects.toThrow(/24 jam/);
    await expect(pub.publish({}, 'k', 24 * 3600_000 + 1)).rejects.toThrow(/24 jam/);
    expect(f.connect).not.toHaveBeenCalled();
  });

  it('queue belum dideklarasikan broker → error jelas, koneksi ditutup', async () => {
    const f = fakeAmqp('notify.wa_unofficial');
    const pub = new AmqpWaPublisher(config);
    pub.connectFn = f.connect as never;
    await expect(pub.publish({}, 'k', 1000)).rejects.toThrow(/belum dideklarasikan/);
    expect(f.closedCount()).toBe(1);
    expect(f.sent).toHaveLength(0);
  });
});
