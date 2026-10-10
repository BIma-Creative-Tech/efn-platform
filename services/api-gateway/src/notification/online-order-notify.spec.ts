import { AmqpService, PermanentMessageError } from '../common/amqp/amqp.service';
import { PII_AAD } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';
import { FakeDb, FakeWhatsAppSender, testCipher, testConfig } from '../common/testing/fakes';
import { NotifyPushConsumer, parseNotifyPushMessage } from './notify-push.consumer';

const PART = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10';
const PHONE = '6281234567890';

function setup(row: Record<string, unknown> | null) {
  const config = testConfig();
  const cipher = testCipher(config);
  const hash = memberHash(config.memberHashPepper, PHONE);
  const db = new FakeDb((t) => {
    if (t.includes('FROM platform.online_order_part p')) return row ? { rows: [row], rowCount: 1 } : { rows: [], rowCount: 0 };
    if (t.includes('INSERT INTO platform.processed_message')) return { rows: [{ consumer: 'x' }], rowCount: 1 };
    return undefined;
  });
  const sender = new FakeWhatsAppSender();
  const consumer = new NotifyPushConsumer({} as AmqpService, db, sender, config, cipher);
  const msg = (template = 'online_order_ready', h = hash) => ({
    schema_version: 2,
    idempotency_key: `notify:${template}:${PART}`,
    member_hash: h,
    source: 'online_order',
    source_ref: PART,
    template,
    params: {},
  });
  return { config, cipher, hash, sender, consumer, msg };
}

describe('notify.push online_order (§10.9)', () => {
  it('penerima dari online_order.member_id → member.member_ref_enc; WA tanpa parameter (tanpa kode ambil)', async () => {
    const self = setup(null);
    const row = { member_hash: self.hash, member_ref_enc: self.cipher.encrypt(PHONE, PII_AAD.MEMBER_REF), part_found: true };
    const ok = new NotifyPushConsumer({} as AmqpService, new FakeDb((t) => (t.includes('online_order_part') ? { rows: [row], rowCount: 1 } : t.includes('processed_message') ? { rows: [{}], rowCount: 1 } : undefined)), self.sender, self.config, self.cipher);
    expect(await ok.handle(self.msg(), { userId: 'efn-bff' })).toBe('sent');
    expect(self.sender.templates).toEqual([{ to: PHONE, name: 'efn_online_order_ready', params: [] }]);
  });

  it('tautan member dilepas (retensi/erasure) → skipped; publisher selain efn-bff / template sumber lain → DLQ', async () => {
    const ctx = setup({ member_hash: null, member_ref_enc: null, part_found: true });
    expect(await ctx.consumer.handle(ctx.msg(), { userId: 'efn-bff' })).toBe('skipped');
    expect(ctx.sender.templates).toHaveLength(0);
    await expect(ctx.consumer.handle(ctx.msg(), { userId: 'efn-ocr' })).rejects.toBeInstanceOf(PermanentMessageError);
    expect(() => parseNotifyPushMessage({ ...ctx.msg(), template: 'price_hunter_valid' })).toThrow(PermanentMessageError);
    expect(() => parseNotifyPushMessage({ ...ctx.msg(), source: 'price_hunter', template: 'online_order_ready' })).toThrow(PermanentMessageError);
  });
});
