import { ALL_QUEUES, QueueAsserter, QueueMissingError, assertQueueWithDlq, checkQueueWithDlq } from './queues';

describe('topologi RabbitMQ (kontrak §4)', () => {
  it('queue quorum durable + DLQ <queue>.dlq via default exchange', async () => {
    const calls: { queue: string; options: { durable: boolean; arguments: Record<string, string> } }[] = [];
    const ch: QueueAsserter = {
      assertQueue: async (queue, options) => {
        calls.push({ queue, options });
        return {};
      },
    };
    await assertQueueWithDlq(ch, 'ocr.requested');
    expect(calls).toEqual([
      { queue: 'ocr.requested.dlq', options: { durable: true, arguments: { 'x-queue-type': 'quorum' } } },
      {
        queue: 'ocr.requested',
        options: {
          durable: true,
          arguments: {
            'x-queue-type': 'quorum',
            'x-dead-letter-exchange': '',
            'x-dead-letter-routing-key': 'ocr.requested.dlq',
          },
        },
      },
    ]);
  });

  it('§8.10: passive declare (checkQueue) DLQ + queue; queue hilang → QueueMissingError berpesan jelas', async () => {
    const checked: string[] = [];
    await checkQueueWithDlq({ checkQueue: async (q) => checked.push(q) }, 'privacy.erasure.completed');
    expect(checked).toEqual(['privacy.erasure.completed.dlq', 'privacy.erasure.completed']);
    const missing = checkQueueWithDlq(
      {
        checkQueue: async () => {
          throw Object.assign(new Error('NOT_FOUND'), { code: 404 });
        },
      },
      'notify.push',
    );
    await expect(missing).rejects.toBeInstanceOf(QueueMissingError);
    await expect(missing).rejects.toThrow("'notify.push.dlq' belum dideklarasikan broker");
  });

  it('nama queue sesuai kontrak', () => {
    expect([...ALL_QUEUES]).toEqual(['ocr.requested', 'loyalty.earn.requested', 'notify.push', 'privacy.erasure.completed']);
  });
});
