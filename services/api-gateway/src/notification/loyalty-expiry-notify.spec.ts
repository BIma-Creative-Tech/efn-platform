import { Logger } from '@nestjs/common';
import { AmqpService, PermanentMessageError } from '../common/amqp/amqp.service';
import { MetricsService } from '../common/metrics/metrics.service';
import { PII_AAD } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';
import { FakeDb, FakeWhatsAppSender, testCipher, testConfig } from '../common/testing/fakes';
import { NotifyPushConsumer, parseNotifyPushMessage } from './notify-push.consumer';

/** G22 §14.D.4 + L-76: notify.push `source: loyalty_expiry` — penerima dari notice, param hanya tanggal, bukti tanpa identitas. */

const MEMBER = '6281234567890';
const NOTICE = '7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d';
const BFF = { userId: 'efn-bff' };

function setup(status = 'queued', withMember = true) {
  const config = testConfig();
  const cipher = testCipher(config);
  const hash = memberHash(config.memberHashPepper, MEMBER);
  const processed = new Set<string>();
  const db = new FakeDb((text, params) => {
    if (text.includes('FROM platform.loyalty_expiry_notice WHERE id = $1')) {
      return params[0] === NOTICE ? { rows: [{ member_hash: hash, status }], rowCount: 1 } : { rows: [], rowCount: 0 };
    }
    if (text.includes('FROM platform.member WHERE member_hash = $1')) {
      return withMember ? { rows: [{ member_ref_enc: cipher.encrypt(MEMBER, PII_AAD.MEMBER_REF) }], rowCount: 1 } : { rows: [], rowCount: 0 };
    }
    if (text.includes('FROM platform.loyalty_ledger WHERE member_ref_hash = $1')) {
      return { rows: [{ member_ref_enc: cipher.encrypt(MEMBER, PII_AAD.LOYALTY_LEDGER_MEMBER_REF) }], rowCount: 1 };
    }
    if (!text.includes('platform.processed_message')) return undefined;
    const k = `${String(params[0])}|${String(params[1])}`;
    if (processed.has(k)) return { rows: [], rowCount: 0 };
    processed.add(k);
    return { rows: [{ consumer: params[0] }], rowCount: 1 };
  });
  const sender = new FakeWhatsAppSender();
  const metrics = new MetricsService();
  const consumer = new NotifyPushConsumer({} as AmqpService, db, sender, config, cipher, metrics);
  const msg = (over: Record<string, unknown> = {}) => ({
    schema_version: 2,
    idempotency_key: `notify:loyalty_inactivity:${NOTICE}`,
    member_hash: hash,
    source: 'loyalty_expiry',
    source_ref: NOTICE,
    template: 'loyalty_inactivity_notice',
    params: { expire_date: '2027-12-02', points: 1200 },
    ...over,
  });
  return { config, hash, db, sender, consumer, msg, metrics };
}

describe('notify.push loyalty_expiry (§14.D.4, L-76)', () => {
  it('kirim template efn_loyalty_inactivity_notice dengan HANYA tanggal; bukti tanpa identitas disimpan; duplikat dilewati', async () => {
    const ctx = setup();
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    try {
      expect(await ctx.consumer.handle(ctx.msg(), BFF)).toBe('sent');
      expect(await ctx.consumer.handle(ctx.msg(), BFF)).toBe('duplicate');
    } finally {
      log.mockRestore();
    }
    expect(ctx.sender.templates).toEqual([{ to: MEMBER, name: 'efn_loyalty_inactivity_notice', params: ['2027-12-02'] }]);
    const proof = ctx.db.find('INSERT INTO platform.loyalty_expiry_notice_proof');
    expect(proof).toHaveLength(1);
    expect(proof[0].params.slice(0, 2)).toEqual([NOTICE, '2027-12-02']);
    expect(JSON.stringify(proof[0].params)).not.toContain(ctx.hash);
  });

  it('subjek hanya di ledger POS → nomor dari entri ledger; notice superseded → skipped tanpa WA', async () => {
    const pos = setup('queued', false);
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    try {
      expect(await pos.consumer.handle(pos.msg(), BFF)).toBe('sent');
      const sup = setup('superseded');
      expect(await sup.consumer.handle(sup.msg(), BFF)).toBe('skipped');
      expect(sup.sender.templates).toEqual([]);
    } finally {
      log.mockRestore();
    }
  });

  it('publisher selain efn-bff / template lain untuk source ini → DLQ; gagal kirim dihitung metrik', async () => {
    const ctx = setup();
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      await expect(ctx.consumer.handle(ctx.msg(), { userId: 'efn-ocr' })).rejects.toThrow(PermanentMessageError);
    } finally {
      warn.mockRestore();
    }
    expect(() => parseNotifyPushMessage(ctx.msg({ template: 'online_order_ready' }))).toThrow(PermanentMessageError);
    ctx.sender.failNext = true;
    await expect(ctx.consumer.handle(ctx.msg(), BFF)).rejects.toThrow();
    expect(await ctx.metrics.render()).toMatch(/efn_loyalty_expiry_notice_failed_total 1/);
  });
});
