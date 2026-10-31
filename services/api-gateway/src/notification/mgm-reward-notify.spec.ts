import { AmqpService, PermanentMessageError } from '../common/amqp/amqp.service';
import { PII_AAD } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';
import { FakeDb, FakeWhatsAppSender, testCipher, testConfig } from '../common/testing/fakes';
import { NotifyPushConsumer } from './notify-push.consumer';

/** G33 docs/04 §20.11: notify.push `source=mgm_reward` — penerima referrer, tanpa parameter, publisher hanya efn-bff. */
const REFERRER = '6281299990001';
const REWARD = '7b9e2c41-1a2b-4c3d-8e9f-0a1b2c3d4e5f';
const BFF = { userId: 'efn-bff' };

function setup(opts: { referrerHash?: string; memberActive?: boolean; enc?: 'ok' | 'wrong_aad' } = {}) {
  const config = testConfig();
  const cipher = testCipher(config);
  const hash = memberHash(config.memberHashPepper, REFERRER);
  const rewardHash = opts.referrerHash ?? hash;
  const enc =
    opts.enc === 'wrong_aad' ? cipher.encrypt(REFERRER, PII_AAD.PRICE_HUNTER_REPORT_USER) : cipher.encrypt(REFERRER, PII_AAD.MEMBER_REF);
  const rewards = new Map<string, { referrer_hash: string }>([[REWARD, { referrer_hash: rewardHash }]]);
  const processed = new Set<string>();
  const db = new FakeDb((text, params) => {
    if (text.includes('FROM platform.mgm_reward w')) {
      const r = rewards.get(String(params[0]));
      if (!r) return { rows: [], rowCount: 0 };
      const active = opts.memberActive !== false && r.referrer_hash === hash;
      return { rows: [{ referrer_hash: r.referrer_hash, member_ref_enc: active ? enc : null }], rowCount: 1 };
    }
    if (!text.includes('platform.processed_message')) return undefined;
    const k = `${String(params[0])}|${String(params[1])}`;
    if (processed.has(k)) return { rows: [], rowCount: 0 };
    processed.add(k);
    return { rows: [{ consumer: params[0] }], rowCount: 1 };
  });
  const sender = new FakeWhatsAppSender();
  const consumer = new NotifyPushConsumer({} as AmqpService, db, sender, config, cipher);
  return { hash, db, sender, consumer };
}

const msg = (hash: string, over: Record<string, unknown> = {}): Record<string, unknown> => ({
  schema_version: 2,
  idempotency_key: `notify:mgm_reward:${REWARD}`,
  member_hash: hash,
  source: 'mgm_reward',
  source_ref: REWARD,
  template: 'mgm_reward_settled',
  params: {},
  ...over,
});

describe('G33 notify.push mgm_reward (§20.11)', () => {
  it('kirim template tanpa parameter ke referrer; duplikat dilewati; HP tidak jadi parameter query', async () => {
    const ctx = setup();
    expect(await ctx.consumer.handle(msg(ctx.hash), BFF)).toBe('sent');
    expect(await ctx.consumer.handle(msg(ctx.hash), BFF)).toBe('duplicate');
    expect(ctx.sender.templates).toEqual([{ to: REFERRER, name: 'efn_mgm_reward_settled', params: [] }]);
    for (const q of ctx.db.queries) expect(q.params).not.toContain(REFERRER);
  });

  it('params liar (poin/nama) tidak pernah masuk ke WA', async () => {
    const ctx = setup();
    await ctx.consumer.handle(msg(ctx.hash, { params: { points: 200, name: 'Budi' } }), BFF);
    expect(ctx.sender.templates[0]?.params).toEqual([]);
  });

  it('referrer sudah di-erase (member tidak aktif) → skipped, tanpa WA', async () => {
    const ctx = setup({ memberActive: false });
    expect(await ctx.consumer.handle(msg(ctx.hash), BFF)).toBe('skipped');
    expect(ctx.sender.templates).toEqual([]);
  });

  it('member_hash pesan ≠ referrer reward / reward tidak ada / enc AAD lain → DLQ', async () => {
    const a = setup({ referrerHash: 'b'.repeat(64) });
    await expect(a.consumer.handle(msg(a.hash), BFF)).rejects.toBeInstanceOf(PermanentMessageError);
    const b = setup();
    await expect(b.consumer.handle(msg(b.hash, { source_ref: '00000000-0000-4000-8000-000000000000' }), BFF)).rejects.toBeInstanceOf(
      PermanentMessageError,
    );
    const c = setup({ enc: 'wrong_aad' });
    await expect(c.consumer.handle(msg(c.hash), BFF)).rejects.toBeInstanceOf(PermanentMessageError);
    expect([...a.sender.templates, ...b.sender.templates, ...c.sender.templates]).toEqual([]);
  });

  it('publisher selain efn-bff dan template sumber lain → DLQ', async () => {
    const ctx = setup();
    await expect(ctx.consumer.handle(msg(ctx.hash), { userId: 'efn-ocr' })).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.consumer.handle(msg(ctx.hash, { template: 'online_order_ready' }), BFF)).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.consumer.handle(msg(ctx.hash, { source: 'price_hunter' }), BFF)).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.sender.templates).toEqual([]);
  });
});
