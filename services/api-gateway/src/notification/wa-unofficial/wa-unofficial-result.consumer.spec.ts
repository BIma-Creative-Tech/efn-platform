import { randomUUID } from 'node:crypto';
import { AmqpService, PermanentMessageError } from '../../common/amqp/amqp.service';
import { FakeKvStore, testConfig } from '../../common/testing/fakes';
import { ChannelEnv } from '../email/channel-env';
import { ChannelTables } from '../email/channel-testing';
import { WaUnofficialConfig } from './wa-unofficial.config';
import { WA_CHANNEL_DOWN_KEY } from './wa-unofficial.notifier';
import { WaUnofficialResultConsumer, parseWaResultMessage } from './wa-unofficial-result.consumer';

const H = 'ab'.repeat(32);
const SVC = { userId: 'efn-wa-unofficial', messageId: undefined as string | undefined };

function setup() {
  const app = testConfig();
  const t = new ChannelTables();
  const memberId = randomUUID();
  t.grantMarketing(memberId);
  t.waRecipients.set(H, memberId);
  const key = `wa:restock:r1:${H}`;
  t.waOutbound.set(key, { member_id: memberId, to_hash: H, status: 'queued', reason: null });
  const kv = new FakeKvStore();
  const cfg = new WaUnofficialConfig(new ChannelEnv({ NODE_ENV: 'test' }));
  const consumer = new WaUnofficialResultConsumer({} as AmqpService, t.db, app, cfg, kv);
  return { t, memberId, key, kv, consumer };
}

const result = (key: string, status: string, over: Record<string, unknown> = {}) => ({
  schema_version: 1,
  ref_idempotency_key: key,
  kind: 'result',
  status,
  to_hash: H,
  at: '2026-11-20T03:00:00Z',
  ...over,
});

describe('WaUnofficialResultConsumer (notify.wa_unofficial.result)', () => {
  it('result sent → status outbound diperbarui sekali; duplikat (message_id sama) dilewati', async () => {
    const ctx = setup();
    const meta = { ...SVC, messageId: `warslt:${ctx.key}` };
    expect(await ctx.consumer.handle(result(ctx.key, 'sent'), meta)).toBe('updated');
    expect(await ctx.consumer.handle(result(ctx.key, 'sent'), meta)).toBe('duplicate');
    expect(ctx.t.waOutbound.get(ctx.key)!.status).toBe('sent');
  });

  it('hasil akhir tidak ditimpa; to_hash lain tidak bisa mengubah baris orang lain', async () => {
    const ctx = setup();
    await ctx.consumer.handle(result(ctx.key, 'failed', { reason: 'not_on_whatsapp' }), { ...SVC, messageId: 'm1' });
    expect(await ctx.consumer.handle(result(ctx.key, 'sent'), { ...SVC, messageId: 'm2' })).toBe('ignored');
    expect(ctx.t.waOutbound.get(ctx.key)).toMatchObject({ status: 'failed', reason: 'not_on_whatsapp' });
    // disconnected bukan hasil akhir: pesan yang diputar ulang dari DLQ lalu sent tetap memperbarui
    const k3 = `wa:restock:r3:${H}`;
    ctx.t.waOutbound.set(k3, { member_id: ctx.memberId, to_hash: H, status: 'queued', reason: null });
    await ctx.consumer.handle(result(k3, 'disconnected'), { ...SVC, messageId: `warslt:${k3}:disconnected` });
    expect(await ctx.consumer.handle(result(k3, 'sent'), { ...SVC, messageId: `warslt:${k3}:sent` })).toBe('updated');
    expect(await ctx.consumer.handle(result(ctx.key, 'sent', { to_hash: 'cd'.repeat(32) }), { ...SVC, messageId: 'm3' })).toBe('ignored');
  });

  it('opt_out STOP → consent marketing dicabut (source wa_stop)', async () => {
    const ctx = setup();
    const out = await ctx.consumer.handle(
      { schema_version: 1, ref_idempotency_key: null, kind: 'opt_out', to_hash: H, at: '2026-11-20T03:01:00Z' },
      { ...SVC, messageId: `waoptout:${H}:1` },
    );
    expect(out).toBe('opted_out');
    expect(ctx.t.marketingGranted(ctx.memberId)).toBe(false);
    expect(ctx.t.consents[ctx.t.consents.length - 1]).toMatchObject({ source: 'wa_stop', granted: false });
  });

  it('opt_out untuk to_hash tak dikenal → di-ack (opt_out_unknown), tanpa consent baru', async () => {
    const ctx = setup();
    const before = ctx.t.consents.length;
    const out = await ctx.consumer.handle(
      { schema_version: 1, ref_idempotency_key: null, kind: 'opt_out', to_hash: 'ef'.repeat(32), at: '2026-11-20T03:01:00Z' },
      SVC,
    );
    expect(out).toBe('opt_out_unknown');
    expect(ctx.t.consents.length).toBe(before);
  });

  it('banned → penanda kanal tidak tersedia di Redis; sent berikutnya menghapus penanda', async () => {
    const ctx = setup();
    await ctx.consumer.handle(result(ctx.key, 'banned'), { ...SVC, messageId: 'b1' });
    expect(await ctx.kv.get(WA_CHANNEL_DOWN_KEY)).toBe('banned');
    const k2 = `wa:restock:r2:${H}`;
    ctx.t.waOutbound.set(k2, { member_id: ctx.memberId, to_hash: H, status: 'queued', reason: null });
    await ctx.consumer.handle(result(k2, 'sent'), { ...SVC, messageId: 's2' });
    expect(await ctx.kv.get(WA_CHANNEL_DOWN_KEY)).toBeNull();
  });

  it('publisher selain efn-wa-unofficial → DLQ', async () => {
    const ctx = setup();
    await expect(ctx.consumer.handle(result(ctx.key, 'sent'), { userId: 'efn-bff', messageId: 'x' })).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.t.waOutbound.get(ctx.key)!.status).toBe('queued');
  });

  it('payload tidak valid (memuat nomor, status/kind asing, result tanpa ref) → permanen', () => {
    expect(() => parseWaResultMessage({ ...result('wa:x', 'sent'), to_e164: '6281' })).toThrow(PermanentMessageError);
    expect(() => parseWaResultMessage(result('wa:x', 'delivered'))).toThrow(PermanentMessageError);
    expect(() => parseWaResultMessage(result('wa:x', 'sent', { kind: 'other' }))).toThrow(PermanentMessageError);
    expect(() => parseWaResultMessage(result('wa:x', 'sent', { ref_idempotency_key: null }))).toThrow(PermanentMessageError);
    expect(() => parseWaResultMessage(result('wa:x', 'sent', { to_hash: 'xyz' }))).toThrow(PermanentMessageError);
    // reason di luar pola dibuang (bukan error)
    expect(parseWaResultMessage(result('wa:x', 'failed', { reason: 'Ada Spasi!' })).reason).toBeUndefined();
  });
});
