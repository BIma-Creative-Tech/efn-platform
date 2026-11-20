import { randomBytes, randomUUID } from 'node:crypto';
import { PII_AAD } from '../../common/pii/crypto';
import { memberHash } from '../../common/pii/pii';
import { FakeKvStore, testCipher, testConfig } from '../../common/testing/fakes';
import { ChannelEnv } from '../email/channel-env';
import { ChannelTables } from '../email/channel-testing';
import { WaUnofficialConfig } from './wa-unofficial.config';
import { WA_CATEGORIES, WaContractError, waToHash } from './wa-unofficial.contract';
import { WA_CHANNEL_DOWN_KEY, WaEnqueueInput, WaUnofficialNotifier } from './wa-unofficial.notifier';
import { WaQueuePublisher } from './wa-unofficial.publisher';

const PHONE = '6281311112222';
const NOW = Date.UTC(2026, 10, 20, 3, 0, 0);

class FakePublisher implements WaQueuePublisher {
  readonly sent: { payload: Record<string, unknown>; messageId: string; expirationMs: number }[] = [];
  fail = false;
  async publish(payload: object, messageId: string, expirationMs: number): Promise<void> {
    if (this.fail) throw new Error('broker mati');
    this.sent.push({ payload: payload as Record<string, unknown>, messageId, expirationMs });
  }
}

function waConfig(env: Record<string, string>, hashKey: string | null): WaUnofficialConfig {
  return new WaUnofficialConfig(
    new ChannelEnv({ NODE_ENV: 'test', ...(hashKey ? { WA_UNOFFICIAL_HASH_KEY_FILE: '/s/h' } : {}), ...env }, (p) => {
      if (p === '/s/h' && hashKey) return Buffer.from(hashKey, 'utf8');
      throw new Error('ENOENT');
    }),
  );
}

function setup(env: Record<string, string> = { WA_UNOFFICIAL_ENABLED: 'true', APP_ENV: 'staging' }, withKey = true) {
  const app = testConfig();
  const cipher = testCipher(app);
  const t = new ChannelTables();
  const memberId = randomUUID();
  t.members.set(memberId, {
    member_hash: memberHash(app.memberHashPepper, PHONE),
    member_ref_enc: cipher.encrypt(PHONE, PII_AAD.MEMBER_REF),
    deleted: false,
  });
  const hashKey = randomBytes(32).toString('hex');
  const kv = new FakeKvStore(() => NOW);
  const pub = new FakePublisher();
  const notifier = new WaUnofficialNotifier(waConfig(env, withKey ? hashKey : null), app, t.db, cipher, kv, pub);
  notifier.now = () => NOW;
  const input = (over: Partial<WaEnqueueInput> = {}): WaEnqueueInput => ({
    memberId,
    category: 'notify_restock',
    source: 'restock',
    sourceRef: 'restock:SMD:OUT1:42:3',
    template: 'restock_available',
    params: { product_name: 'Telur 1kg', outlet_name: 'SMD Outlet 1' },
    expiresAt: new Date(NOW + 3600_000),
    ...over,
  });
  return { t, memberId, notifier, pub, kv, input, hashKey: Buffer.from(hashKey, 'utf8') };
}

describe('WaUnofficialNotifier.enqueue (antarmuka BFF-A, ADR-044)', () => {
  it('flag mati / bukan persis "true" → skipped_disabled, tanpa query & tanpa publish', async () => {
    for (const v of ['false', 'TRUE', '1', 'yes']) {
      const ctx = setup({ WA_UNOFFICIAL_ENABLED: v, APP_ENV: 'staging' });
      ctx.t.grantMarketing(ctx.memberId);
      expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_disabled');
      expect(ctx.t.db.queries).toHaveLength(0);
      expect(ctx.pub.sent).toHaveLength(0);
    }
  });

  it('APP_ENV=production tanpa WA_UNOFFICIAL_RISK_ACCEPTANCE_REF → skipped_disabled; dengan ref → aktif', async () => {
    const off = setup({ WA_UNOFFICIAL_ENABLED: 'true', APP_ENV: 'production' });
    off.t.grantMarketing(off.memberId);
    expect(await off.notifier.enqueue(off.input())).toBe('skipped_disabled');
    const on = setup({ WA_UNOFFICIAL_ENABLED: 'true', APP_ENV: 'production', WA_UNOFFICIAL_RISK_ACCEPTANCE_REF: 'LGL-2027-001' });
    on.t.grantMarketing(on.memberId);
    expect(await on.notifier.enqueue(on.input())).toBe('queued');
  });

  it('kunci hash belum ada → skipped_disabled', async () => {
    const ctx = setup(undefined, false);
    ctx.t.grantMarketing(ctx.memberId);
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_disabled');
  });

  it('tanpa consent marketing → skipped_no_consent (juga untuk notify-me, Q-SM25 default)', async () => {
    const ctx = setup();
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_no_consent');
    expect(ctx.pub.sent).toHaveLength(0);
  });

  it('member tidak ada / dihapus / nomor tidak konsisten → skipped_no_phone', async () => {
    const ctx = setup();
    ctx.t.grantMarketing(ctx.memberId);
    expect(await ctx.notifier.enqueue(ctx.input({ memberId: randomUUID() }))).toBe('skipped_no_phone');
    ctx.t.members.get(ctx.memberId)!.member_hash = 'f'.repeat(64);
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_no_phone');
    ctx.t.members.get(ctx.memberId)!.deleted = true;
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_no_phone');
  });

  it('queued: payload kontrak §28.7, to_hash HMAC, expiration = sisa waktu, pemetaan STOP tersimpan; idempoten', async () => {
    const ctx = setup();
    ctx.t.grantMarketing(ctx.memberId);
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('queued');
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('queued');
    expect(ctx.pub.sent).toHaveLength(1);
    const toHash = waToHash(ctx.hashKey, PHONE);
    const s = ctx.pub.sent[0];
    expect(s.messageId).toBe(`wa:restock:restock:SMD:OUT1:42:3:${toHash}`);
    expect(s.expirationMs).toBe(3600_000);
    expect(s.payload).toEqual({
      schema_version: 1,
      idempotency_key: s.messageId,
      to_e164: PHONE,
      to_hash: toHash,
      category: 'notify_restock',
      template: 'restock_available',
      params: { product_name: 'Telur 1kg', outlet_name: 'SMD Outlet 1' },
      expires_at: new Date(NOW + 3600_000).toISOString(),
    });
    expect(ctx.t.waRecipients.get(toHash)).toBe(ctx.memberId);
    // nomor tidak pernah masuk parameter SQL
    for (const q of ctx.t.db.queries) expect(JSON.stringify(q.params)).not.toContain(PHONE);
  });

  it('expiresAt > 24 jam dipangkas ke 24 jam; sudah lewat → WaContractError', async () => {
    const ctx = setup();
    ctx.t.grantMarketing(ctx.memberId);
    await ctx.notifier.enqueue(ctx.input({ expiresAt: new Date(NOW + 72 * 3600_000) }));
    expect(ctx.pub.sent[0].expirationMs).toBe(24 * 3600_000);
    await expect(ctx.notifier.enqueue(ctx.input({ sourceRef: 'x2', expiresAt: new Date(NOW - 1) }))).rejects.toBeInstanceOf(WaContractError);
  });

  it('category di luar allowlist / template beda category / param asing → WaContractError (OTP dsb. tidak lewat kanal ini)', async () => {
    const ctx = setup();
    ctx.t.grantMarketing(ctx.memberId);
    expect(WA_CATEGORIES).toEqual(['promo', 'notify_restock', 'price_alert']);
    await expect(ctx.notifier.enqueue(ctx.input({ category: 'otp' as never }))).rejects.toBeInstanceOf(WaContractError);
    await expect(ctx.notifier.enqueue(ctx.input({ category: 'promo' }))).rejects.toBeInstanceOf(WaContractError);
    await expect(ctx.notifier.enqueue(ctx.input({ template: 'otp_code' }))).rejects.toBeInstanceOf(WaContractError);
    await expect(ctx.notifier.enqueue(ctx.input({ params: { product_name: 'x', outlet_name: 'y', code: '123456' } }))).rejects.toBeInstanceOf(WaContractError);
    expect(ctx.pub.sent).toHaveLength(0);
  });

  it('kanal ditandai banned/disconnected → skipped_disabled (tanpa pengalihan ke Cloud API)', async () => {
    const ctx = setup();
    ctx.t.grantMarketing(ctx.memberId);
    await ctx.kv.setEx(WA_CHANNEL_DOWN_KEY, 'banned', 3600);
    expect(await ctx.notifier.enqueue(ctx.input())).toBe('skipped_disabled');
  });

  it('publish gagal → error dilempar (transaksi rollback oleh Db asli)', async () => {
    const ctx = setup();
    ctx.t.grantMarketing(ctx.memberId);
    ctx.pub.fail = true;
    await expect(ctx.notifier.enqueue(ctx.input())).rejects.toThrow('broker mati');
  });
});
