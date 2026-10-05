import { Logger } from '@nestjs/common';
import { AmqpService } from '../amqp/amqp.service';
import { FakeDb, testConfig } from '../testing/fakes';
import { OutboxService } from './outbox.service';

describe('OutboxService.relayOnce', () => {
  beforeAll(() => jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined));

  const rows = [
    { id: '1', queue: 'ocr.requested', payload: { report_id: 'a' }, message_id: 'ocr.requested:a' },
    { id: '2', queue: 'ocr.requested', payload: { report_id: 'b' }, message_id: 'ocr.requested:b' },
  ];

  function setup(publish: jest.Mock, connected = true) {
    const db = new FakeDb((text) => (text.includes('FROM platform.outbox') ? { rows, rowCount: rows.length } : undefined));
    const amqp = { isConnected: () => connected, publish } as unknown as AmqpService;
    return { db, outbox: new OutboxService(db, amqp, testConfig()) };
  }

  it('publish dengan message_id lalu menandai published_at', async () => {
    const publish = jest.fn().mockResolvedValue(undefined);
    const { db, outbox } = setup(publish);
    expect(await outbox.relayOnce()).toBe(2);
    expect(publish).toHaveBeenNthCalledWith(1, 'ocr.requested', { report_id: 'a' }, 'ocr.requested:a');
    expect(db.find('UPDATE platform.outbox SET published_at').map((q) => q.params[0])).toEqual(['1', '2']);
    expect(db.find('FOR UPDATE SKIP LOCKED')).toHaveLength(1);
  });

  it('publish gagal → berhenti, baris berikut tidak ditandai (dicoba lagi nanti)', async () => {
    const publish = jest.fn().mockRejectedValueOnce(new Error('broker down'));
    const { db, outbox } = setup(publish);
    expect(await outbox.relayOnce()).toBe(0);
    expect(publish).toHaveBeenCalledTimes(1);
    expect(db.find('UPDATE platform.outbox')).toHaveLength(0);
  });

  it('broker belum terhubung → tidak menyentuh DB', async () => {
    const { db, outbox } = setup(jest.fn(), false);
    expect(await outbox.relayOnce()).toBe(0);
    expect(db.queries).toHaveLength(0);
  });

  it('enqueue menulis payload JSON + message_id di transaksi pemanggil', async () => {
    const { db, outbox } = setup(jest.fn());
    await outbox.enqueue(db, 'ocr.requested', { a: 1 }, 'm-1');
    expect(db.queries[0].params).toEqual(['ocr.requested', '{"a":1}', 'm-1']);
  });
});
