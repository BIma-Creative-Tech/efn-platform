import { randomUUID } from 'node:crypto';
import { OutboxService } from '../../common/outbox/outbox.service';
import { PII_AAD } from '../../common/pii/crypto';
import { memberHash } from '../../common/pii/pii';
import { testCipher, testConfig } from '../../common/testing/fakes';
import { ChannelEnv } from './channel-env';
import { ChannelTables } from './channel-testing';
import { NOTIFY_EMAIL_QUEUE, encryptMemberEmail } from './email.constants';
import { EmailConfig } from './email.config';
import { EmailTemplateError } from './email-templates';
import { EmailEnqueueInput, EmailNotifier } from './email.notifier';

const ADDRESS = 'budi.santoso@contoh.id';
const PHONE = '6281234567890';

function setup(enabled = true) {
  const app = testConfig();
  const cipher = testCipher(app);
  const t = new ChannelTables();
  const memberId = randomUUID();
  const hash = memberHash(app.memberHashPepper, PHONE);
  t.members.set(memberId, { member_hash: hash, member_ref_enc: cipher.encrypt(PHONE, PII_AAD.MEMBER_REF), deleted: false });
  t.emails.set(memberId, { email_enc: encryptMemberEmail(cipher, ADDRESS, memberId), verified_at: new Date(), status: 'active', status_reason: null });
  const config = new EmailConfig(new ChannelEnv({ NODE_ENV: 'test', EMAIL_ENABLED: enabled ? 'true' : 'false' }));
  // OutboxService asli: enqueue = INSERT platform.outbox dalam transaksi pemanggil (relay tidak dijalankan)
  const outbox = new OutboxService(t.db, {} as never, app);
  const notifier = new EmailNotifier(config, t.db, outbox);
  const input = (over: Partial<EmailEnqueueInput> = {}): EmailEnqueueInput => ({
    memberId,
    source: 'restock',
    sourceRef: 'restock:GL:OUT1:42:7',
    template: 'restock_available',
    params: { product_name: 'Susu UHT 1L', outlet_name: 'SMD Outlet 1' },
    ...over,
  });
  return { app, cipher, t, memberId, hash, notifier, input };
}

describe('EmailNotifier.enqueue (antarmuka BFF-A)', () => {
  it('EMAIL_ENABLED=false → skipped_disabled tanpa query', async () => {
    const ctx = setup(false);
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_disabled');
    expect(ctx.t.db.queries).toHaveLength(0);
  });

  it('tanpa consent marketing → skipped_no_consent (restock/campaign/price_alert, Q-SM25)', async () => {
    const ctx = setup();
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_no_consent');
    ctx.t.grantMarketing(ctx.memberId, true);
    ctx.t.grantMarketing(ctx.memberId, false);
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_no_consent');
    expect(ctx.t.outbox).toHaveLength(0);
  });

  it('email belum terverifikasi / invalid / tidak ada / member dihapus → skipped_no_email', async () => {
    const ctx = setup();
    ctx.t.grantMarketing(ctx.memberId);
    ctx.t.emails.get(ctx.memberId)!.verified_at = null;
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_no_email');
    ctx.t.emails.get(ctx.memberId)!.verified_at = new Date();
    ctx.t.emails.get(ctx.memberId)!.status = 'invalid';
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_no_email');
    ctx.t.emails.delete(ctx.memberId);
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_no_email');
    expect(await ctx.notifier.enqueue(ctx.input({ memberId: randomUUID() }))).toBe('skipped_no_email');
    expect(await ctx.notifier.enqueue(ctx.input({ memberId: 'bukan-uuid' }))).toBe('skipped_no_email');
    expect(ctx.t.outbox).toHaveLength(0);
  });

  it('queued: satu baris outbox notify.email TANPA alamat email; panggilan ulang idempoten', async () => {
    const ctx = setup();
    ctx.t.grantMarketing(ctx.memberId);
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('queued');
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('queued');
    expect(ctx.t.outbox).toHaveLength(1);
    const o = ctx.t.outbox[0];
    expect(o.queue).toBe(NOTIFY_EMAIL_QUEUE);
    expect(o.messageId).toBe(`email:restock:restock:GL:OUT1:42:7:${ctx.hash}`);
    expect(o.payload).toEqual({
      schema_version: 1,
      idempotency_key: o.messageId,
      member_hash: ctx.hash,
      source: 'restock',
      source_ref: 'restock:GL:OUT1:42:7',
      template: 'restock_available',
      params: { product_name: 'Susu UHT 1L', outlet_name: 'SMD Outlet 1' },
    });
    const serialized = JSON.stringify(ctx.t.outbox) + JSON.stringify(ctx.t.db.queries.map((q) => q.params));
    expect(serialized).not.toContain(ADDRESS);
    expect(serialized).not.toContain(PHONE);
  });

  it('transactional tidak butuh consent marketing (tanpa template saat ini → ditolak sebagai kesalahan pemanggil)', async () => {
    const ctx = setup();
    await expect(ctx.notifier.enqueue(ctx.input({ source: 'transactional' }))).rejects.toBeInstanceOf(EmailTemplateError);
  });

  it('template tidak sesuai sumber / parameter asing / wajib hilang → EmailTemplateError', async () => {
    const ctx = setup();
    ctx.t.grantMarketing(ctx.memberId);
    await expect(ctx.notifier.enqueue(ctx.input({ template: 'promo_generic' }))).rejects.toBeInstanceOf(EmailTemplateError);
    await expect(ctx.notifier.enqueue(ctx.input({ params: { product_name: 'X', outlet_name: 'Y', extra: 'z' } }))).rejects.toBeInstanceOf(EmailTemplateError);
    await expect(ctx.notifier.enqueue(ctx.input({ params: { product_name: 'X' } }))).rejects.toBeInstanceOf(EmailTemplateError);
    await expect(ctx.notifier.enqueue(ctx.input({ template: 'constructor' }))).rejects.toBeInstanceOf(EmailTemplateError);
    await expect(ctx.notifier.enqueue(ctx.input({ sourceRef: 'ada spasi' }))).rejects.toBeInstanceOf(EmailTemplateError);
    expect(ctx.t.outbox).toHaveLength(0);
  });
});
