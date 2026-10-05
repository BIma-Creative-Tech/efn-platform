import { AmqpService, PermanentMessageError } from '../common/amqp/amqp.service';
import { PII_AAD } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';
import { FakeDb, FakeWhatsAppSender, testCipher, testConfig } from '../common/testing/fakes';
import { NotifyPushConsumer } from './notify-push.consumer';

const MEMBER = '6281234567890';
const REPORT = '3f2c1b8e-6d1a-4c55-9a7e-1f0d2a9b8c7d';
const OCR = { userId: 'efn-ocr' };

function setup() {
  const config = testConfig();
  const cipher = testCipher(config);
  const hash = memberHash(config.memberHashPepper, MEMBER);
  const reports = new Map<string, { status: string; user_hash: string; user_enc: Buffer }>([
    [REPORT, { status: 'valid', user_hash: hash, user_enc: cipher.encrypt(MEMBER, PII_AAD.PRICE_HUNTER_REPORT_USER) }],
  ]);
  const processed = new Set<string>();
  const db = new FakeDb((text, params) => {
    if (text.includes('FROM platform.price_hunter_report')) {
      const r = reports.get(String(params[0]));
      return r ? { rows: [{ id: params[0], ...r }], rowCount: 1 } : { rows: [], rowCount: 0 };
    }
    if (!text.includes('platform.processed_message')) return undefined;
    const k = `${String(params[0])}|${String(params[1])}`;
    if (processed.has(k)) return { rows: [], rowCount: 0 };
    processed.add(k);
    return { rows: [{ consumer: params[0] }], rowCount: 1 };
  });
  const sender = new FakeWhatsAppSender();
  const consumer = new NotifyPushConsumer({} as AmqpService, db, sender, config, cipher);
  return { config, cipher, hash, reports, db, sender, consumer, processed };
}

function msg(hash: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: 2,
    idempotency_key: `notify:price_hunter_valid:${REPORT}`,
    member_hash: hash,
    source: 'price_hunter',
    source_ref: REPORT,
    template: 'price_hunter_valid',
    params: { product_name: 'Minyak Goreng 2L\nbaris baru', points: 50 },
    ...over,
  };
}

describe('NotifyPushConsumer v2', () => {
  it('resolve penerima dengan dekripsi user_enc report; kirim sekali; duplikat dilewati', async () => {
    const ctx = setup();
    expect(await ctx.consumer.handle(msg(ctx.hash), OCR)).toBe('sent');
    expect(await ctx.consumer.handle(msg(ctx.hash), OCR)).toBe('duplicate');
    expect(ctx.sender.templates).toEqual([
      { to: MEMBER, name: 'efn_price_hunter_valid', params: ['Minyak Goreng 2L baris baru', '50'] },
    ]);
    // nomor HP tidak pernah dikirim sebagai parameter query
    for (const q of ctx.db.queries) expect(q.params).not.toContain(MEMBER);
  });

  it('member_hash ≠ user_hash report → DLQ, tidak ada WA', async () => {
    const ctx = setup();
    const other = memberHash(ctx.config.memberHashPepper, '6285700000000');
    await expect(ctx.consumer.handle(msg(other), OCR)).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.sender.templates).toHaveLength(0);
  });

  it('user_enc rusak / dienkripsi untuk kolom lain → DLQ', async () => {
    const ctx = setup();
    ctx.reports.get(REPORT)!.user_enc = ctx.cipher.encrypt(MEMBER, PII_AAD.LOYALTY_LEDGER_MEMBER_REF);
    await expect(ctx.consumer.handle(msg(ctx.hash), OCR)).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.sender.templates).toHaveLength(0);
  });

  it('report sumber tidak ada → DLQ', async () => {
    const ctx = setup();
    await expect(
      ctx.consumer.handle(msg(ctx.hash, { source_ref: '1b2e1c4d-5f6a-4b7c-8d9e-0f1a2b3c4d5e' }), OCR),
    ).rejects.toBeInstanceOf(PermanentMessageError);
  });

  it.each([
    ['template tidak dikenal', { template: 'tidak_ada' }],
    ['template = properti prototype', { template: 'constructor' }],
    ['schema v1 dengan user_id mentah', { schema_version: 1, user_id: MEMBER }],
    ['v2 tetapi membawa user_id', { user_id: MEMBER }],
    ['source tidak didukung', { source: 'mgm' }],
    ['source_ref bukan UUID', { source_ref: 'x' }],
  ])('%s → PermanentMessageError', async (_n, over) => {
    const ctx = setup();
    await expect(ctx.consumer.handle(msg(ctx.hash, over), OCR)).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.sender.templates).toHaveLength(0);
  });

  it('publisher tidak diizinkan (user_id lain / kosong) → DLQ', async () => {
    const ctx = setup();
    await expect(ctx.consumer.handle(msg(ctx.hash), { userId: 'efn-etl' })).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.consumer.handle(msg(ctx.hash), { userId: undefined })).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.sender.templates).toHaveLength(0);
  });

  it('kontrak lintas-service: payload v2 bentuk ai-ocr-worker (efn_ocr/messages.py) terisi lengkap', async () => {
    const ctx = setup();
    await ctx.consumer.handle(
      {
        schema_version: 2,
        idempotency_key: `price_hunter_notify:${REPORT}`,
        member_hash: ctx.hash,
        source: 'price_hunter',
        source_ref: REPORT,
        template: 'price_hunter_rejected',
        params: { report_id: REPORT, product_name: 'Gula 1kg', reason: 'price_mismatch' },
      },
      OCR,
    );
    expect(ctx.sender.templates).toEqual([{ to: MEMBER, name: 'efn_price_hunter_rejected', params: ['Gula 1kg', 'price_mismatch'] }]);
    expect(ctx.sender.templates[0].params).not.toContain('-');
  });

  it('pengiriman gagal → error dilempar (transaksi rollback → requeue/DLQ)', async () => {
    const ctx = setup();
    ctx.sender.failNext = true;
    await expect(ctx.consumer.handle(msg(ctx.hash), OCR)).rejects.toThrow('simulated WA failure');
  });
});
