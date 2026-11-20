import { randomBytes, randomUUID } from 'node:crypto';
import { AmqpService, PermanentMessageError } from '../../common/amqp/amqp.service';
import { PII_AAD } from '../../common/pii/crypto';
import { memberHash } from '../../common/pii/pii';
import { RateLimiter } from '../../common/rate-limit/rate-limiter';
import { FakeClock, FakeKvStore, testCipher, testConfig } from '../../common/testing/fakes';
import { ChannelEnv } from './channel-env';
import { ChannelTables } from './channel-testing';
import { MEMBER_EMAIL_AAD, maskEmail } from './email.constants';
import { EmailConfig } from './email.config';
import { EmailSenderConsumer, parseNotifyEmailMessage } from './email-sender.consumer';
import { OutgoingEmail, SmtpTransport } from './smtp-transport';
import { verifyUnsubscribeToken } from './unsubscribe-token';

const ADDRESS = 'siti.aminah@contoh.id';
const PHONE = '6281298765432';
const BFF = { userId: 'efn-bff' };

class FakeTransport implements SmtpTransport {
  readonly sent: OutgoingEmail[] = [];
  failWith: unknown = null;
  async send(mail: OutgoingEmail): Promise<void> {
    if (this.failWith) {
      const e = this.failWith;
      this.failWith = null;
      throw e;
    }
    this.sent.push(mail);
  }
  close(): void {
    /* no-op */
  }
}

function setup(env: Record<string, string> = {}) {
  const app = testConfig();
  const cipher = testCipher(app);
  const clock = new FakeClock(Date.UTC(2026, 10, 20, 3, 0, 0));
  const t = new ChannelTables();
  const memberId = randomUUID();
  const hash = memberHash(app.memberHashPepper, PHONE);
  t.members.set(memberId, { member_hash: hash, member_ref_enc: cipher.encrypt(PHONE, PII_AAD.MEMBER_REF), deleted: false });
  t.emails.set(memberId, { email_enc: cipher.encrypt(ADDRESS, MEMBER_EMAIL_AAD), verified_at: new Date(), status: 'active', status_reason: null });
  t.grantMarketing(memberId);
  const unsubKey = randomBytes(32).toString('hex');
  const config = new EmailConfig(
    new ChannelEnv(
      {
        NODE_ENV: 'test',
        EMAIL_ENABLED: 'true',
        EMAIL_SMTP_HOST: 'mailpit',
        EMAIL_SMTP_TLS_MODE: 'starttls',
        EMAIL_FROM: 'EFN Promo <promo@mail.contoh.id>',
        EMAIL_UNSUBSCRIBE_URL: 'https://superapp.contoh.id/berhenti-langganan',
        EMAIL_UNSUBSCRIBE_KEY_FILE: '/s/key',
        EMAIL_UNSUBSCRIBE_MAILTO: 'unsubscribe@mail.contoh.id',
        EMAIL_BRAND_NAME: 'SMD',
        ...env,
      },
      (p) => {
        if (p === '/s/key') return Buffer.from(unsubKey, 'utf8');
        throw new Error('ENOENT');
      },
    ),
  );
  const kv = new FakeKvStore(clock.now);
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  const transport = new FakeTransport();
  const consumer = new EmailSenderConsumer({} as AmqpService, t.db, app, config, cipher, limiter, transport);
  consumer.now = clock.now;
  const sleeps: number[] = [];
  consumer.sleep = async (ms) => {
    sleeps.push(ms);
    clock.ms += ms;
  };
  const key = `email:restock:restock:1:${hash}`;
  t.emailOutbound.set(key, { member_id: memberId, status: 'queued' });
  const msg = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
    schema_version: 1,
    idempotency_key: key,
    member_hash: hash,
    source: 'restock',
    source_ref: 'restock:1',
    template: 'restock_available',
    params: { product_name: 'Beras <b>5kg</b>', outlet_name: 'SMD Outlet 2' },
    ...over,
  });
  return { app, t, memberId, hash, transport, consumer, msg, key, unsubKey: Buffer.from(unsubKey, 'utf8'), clock, sleeps };
}

describe('EmailSenderConsumer (notify.email)', () => {
  it('kirim sekali: alamat didekripsi saat kirim, HTML di-escape, List-Unsubscribe bertoken valid; duplikat dilewati', async () => {
    const ctx = setup();
    expect(await ctx.consumer.handle(ctx.msg(), BFF)).toBe('sent');
    expect(await ctx.consumer.handle(ctx.msg(), BFF)).toBe('duplicate');
    expect(ctx.transport.sent).toHaveLength(1);
    const m = ctx.transport.sent[0];
    expect(m.to).toBe(ADDRESS);
    expect(m.subject).toBe('SMD: Beras <b>5kg</b> tersedia lagi');
    expect(m.html).toContain('Beras &lt;b&gt;5kg&lt;/b&gt;');
    expect(m.html).not.toContain('<b>5kg</b>');
    const lu = m.headers['List-Unsubscribe'];
    expect(lu).toMatch(/^<https:\/\/superapp\.contoh\.id\/berhenti-langganan#t=eu1\.[^>]+>, <mailto:unsubscribe@mail\.contoh\.id\?subject=unsubscribe>$/);
    const token = /#t=([^>]+)>/.exec(lu)![1];
    expect(verifyUnsubscribeToken(ctx.unsubKey, token, ctx.clock.ms)).toMatchObject({ memberId: ctx.memberId });
    expect(m.text).toContain('Berhenti berlangganan: https://superapp.contoh.id/berhenti-langganan#t=');
    expect(ctx.t.emailOutbound.get(ctx.key)!.status).toBe('sent');
    // tidak ada alamat/nomor di parameter SQL
    for (const q of ctx.t.db.queries) expect(JSON.stringify(q.params)).not.toContain(ADDRESS);
  });

  it('payload yang memuat alamat / idempotency_key tidak cocok / template salah sumber → DLQ (permanen)', () => {
    const ctx = setup();
    expect(() => parseNotifyEmailMessage(ctx.msg({ email: ADDRESS }))).toThrow(PermanentMessageError);
    expect(() => parseNotifyEmailMessage(ctx.msg({ idempotency_key: 'email:x' }))).toThrow(PermanentMessageError);
    expect(() => parseNotifyEmailMessage(ctx.msg({ template: 'promo_generic' }))).toThrow(PermanentMessageError);
    expect(() => parseNotifyEmailMessage(ctx.msg({ schema_version: 2 }))).toThrow(PermanentMessageError);
  });

  it('publisher selain efn-bff → DLQ, tidak ada email', async () => {
    const ctx = setup();
    await expect(ctx.consumer.handle(ctx.msg(), { userId: 'efn-wa-unofficial' })).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.consumer.handle(ctx.msg(), { userId: undefined })).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.transport.sent).toHaveLength(0);
  });

  it('consent marketing dicabut setelah diantre → skipped (tidak dikirim)', async () => {
    const ctx = setup();
    ctx.t.grantMarketing(ctx.memberId, false);
    expect(await ctx.consumer.handle(ctx.msg(), BFF)).toBe('skipped');
    expect(ctx.transport.sent).toHaveLength(0);
    expect(ctx.t.emailOutbound.get(ctx.key)!.status).toBe('skipped');
  });

  it('5xx pada RCPT TO → member_email invalid, ack (bukan DLQ)', async () => {
    const ctx = setup();
    ctx.transport.failWith = Object.assign(new Error('mailbox unavailable'), { responseCode: 550, command: 'RCPT TO' });
    expect(await ctx.consumer.handle(ctx.msg(), BFF)).toBe('invalid_address');
    expect(ctx.t.emails.get(ctx.memberId)!.status).toBe('invalid');
    expect(ctx.t.emailOutbound.get(ctx.key)!.status).toBe('invalid_address');
  });

  it('galat transien (koneksi/auth 535) → lempar (rollback, requeue lalu DLQ); pesan error tanpa alamat', async () => {
    const ctx = setup();
    ctx.transport.failWith = Object.assign(new Error(`auth failed for ${ADDRESS}`), { responseCode: 535, command: 'AUTH PLAIN' });
    const err = await ctx.consumer.handle(ctx.msg(), BFF).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(PermanentMessageError);
    expect(String((err as Error).message)).not.toContain(ADDRESS);
    expect(ctx.t.emails.get(ctx.memberId)!.status).toBe('active');
  });

  it('kuota EMAIL_RATE_PER_MIN habis → menunggu jendela berikut lalu kirim', async () => {
    const ctx = setup({ EMAIL_RATE_PER_MIN: '1' });
    const second = `email:restock:restock:2:${ctx.hash}`;
    expect(await ctx.consumer.handle(ctx.msg(), BFF)).toBe('sent');
    expect(await ctx.consumer.handle(ctx.msg({ idempotency_key: second, source_ref: 'restock:2' }), BFF)).toBe('sent');
    expect(ctx.sleeps.length).toBeGreaterThanOrEqual(1);
    expect(ctx.transport.sent).toHaveLength(2);
  });

  it('flag dimatikan setelah antre → skipped tanpa menyentuh DB', async () => {
    const ctx = setup({ EMAIL_ENABLED: 'false' });
    expect(await ctx.consumer.handle(ctx.msg(), BFF)).toBe('skipped');
    expect(ctx.t.db.queries).toHaveLength(0);
  });

  it('maskEmail tidak membocorkan alamat', () => {
    expect(maskEmail(ADDRESS)).toBe('s***@c***.id');
    expect(maskEmail('tanpa-at')).toBe('***');
  });
});
