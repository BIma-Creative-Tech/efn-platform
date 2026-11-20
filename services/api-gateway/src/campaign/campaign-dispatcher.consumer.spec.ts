import { randomUUID } from 'node:crypto';
import { AmqpService, PermanentMessageError } from '../common/amqp/amqp.service';
import { FakeDb, testCipher, testConfig } from '../common/testing/fakes';
import { ConsolePushSender } from '../promo/push-sender';
import { CampaignDispatcherConsumer } from './campaign-dispatcher.consumer';
import { EmailNotifierPort, WaUnofficialNotifierPort } from './channel-ports';
import { parseCampaignPayload } from './odoo-campaign';
import { campaignPayload } from './testing/campaign-fixtures';

const NOW = Date.UTC(2026, 10, 21, 3, 0, 0);
const KEY = 'campaign:SMD:7:2';
const MEMBERS = [randomUUID(), randomUUID(), randomUUID()].sort();

interface Opts {
  env?: Record<string, string>;
  status?: string;
  payload?: Record<string, unknown>;
  pool?: number;
  freqCount?: number;
  missing?: boolean;
  members?: string[];
  /** Redelivery: baris campaign_delivery (kampanye x member) sudah ada. */
  alreadyDelivered?: boolean;
}

function setup(opts: Opts = {}) {
  const config = testConfig({ PROMO_CAMPAIGN_ENABLED: 'true', ...opts.env });
  const { payload } = parseCampaignPayload(campaignPayload(opts.payload), 'SMD');
  let pool = opts.pool ?? 10;
  const members = opts.members ?? MEMBERS.slice(0, 2);
  const db = new FakeDb((t, p) => {
    if (t.includes('FROM platform.campaign WHERE campaign_key = $1 FOR UPDATE')) {
      return opts.missing ? { rows: [], rowCount: 0 } : { rows: [{ status: opts.status ?? 'queued', payload }], rowCount: 1 };
    }
    if (t.includes('FROM behavior.segment_member') || t.includes('FROM platform.member_wishlist w')) {
      const after = p[2] as string;
      const rows = members.filter((m) => m > after).map((member_id) => ({ member_id }));
      return { rows, rowCount: rows.length };
    }
    if (t.includes('count(*) AS n FROM platform.campaign_delivery')) return { rows: [{ n: opts.freqCount ?? 0 }], rowCount: 1 };
    if (t.includes('count(*) AS n FROM platform.member_voucher')) return { rows: [{ n: 0 }], rowCount: 1 };
    if (t.includes('INSERT INTO platform.campaign_delivery') && t.includes("'delivered'")) {
      return opts.alreadyDelivered ? { rows: [], rowCount: 0 } : { rows: [{ member_id: p[1] }], rowCount: 1 };
    }
    if (t.includes('INSERT INTO platform.campaign_delivery')) return { rows: [], rowCount: 1 };
    if (t.includes('UPDATE platform.member_voucher SET member_id')) {
      if (pool <= 0) return { rows: [], rowCount: 0 };
      pool -= 1;
      return { rows: [{ id: randomUUID() }], rowCount: 1 };
    }
    if (t.includes("mode = 'public_code' LIMIT 1")) return { rows: [{ id: 'pub-voucher' }], rowCount: 1 };
    return undefined;
  });
  const email = { enqueue: jest.fn(async () => 'queued' as const) } as unknown as EmailNotifierPort & { enqueue: jest.Mock };
  const wa = { enqueue: jest.fn(async () => 'queued' as const) } as unknown as WaUnofficialNotifierPort & { enqueue: jest.Mock };
  const c = new CampaignDispatcherConsumer({} as AmqpService, db, config, testCipher(config), email, wa, new ConsolePushSender());
  c.now = () => NOW;
  return { c, db, email, wa, payload };
}

const msg = { schema_version: 1, idempotency_key: KEY };
const bff = { userId: 'efn-bff' };

describe('CampaignDispatcherConsumer (promo.campaign.requested → fan-out)', () => {
  it('segment + anonymous_code: tugaskan kode per member, inbox, email tanpa kode voucher, status done', async () => {
    const ctx = setup();
    const out = await ctx.c.handle(msg, bff);
    expect(out).toEqual({ status: 'done', stats: { delivered: 2, skipped: 0, push_sent: 0, email_queued: 2, wa_queued: 0, stop_reason: null } });
    expect(ctx.db.find('UPDATE platform.member_voucher SET member_id')).toHaveLength(2);
    const inbox = ctx.db.find('INSERT INTO platform.member_inbox');
    expect(inbox).toHaveLength(2);
    expect(inbox[0]!.params[5]).toEqual(expect.any(String)); // voucher_id
    const emailArg = ctx.email.enqueue.mock.calls[0]![0] as { source: string; sourceRef: string; template: string; params: Record<string, unknown> };
    expect(emailArg).toMatchObject({ source: 'campaign', sourceRef: KEY, template: 'promo_generic' });
    // allowlist BFF-B promo_generic; voucher_code sengaja tidak dikirim lewat email/WA
    expect(Object.keys(emailArg.params).sort()).toEqual(['body', 'title', 'valid_to']);
    expect(String(emailArg.params.valid_to)).toContain('2026');
    expect(ctx.db.find("SET status = 'done'")).toHaveLength(1);
    // segmen: consent marketing DAN tracking disyaratkan di query target
    const target = ctx.db.find('FROM behavior.segment_member')[0]!.text;
    expect(target).toContain("c.purpose = 'marketing'");
    expect(target).toContain("c.purpose = 'tracking'");
    // SEC-G50-08: anggota segmen difilter PT kampanye
    const tq = ctx.db.find('FROM behavior.segment_member')[0]!;
    expect(tq.text).toContain('s.company_code = $5');
    expect(tq.params[4]).toBe('SMD');
  });

  it('pool kode habis → berhenti, member berikut dilewati voucher_exhausted', async () => {
    const ctx = setup({ pool: 1, members: MEMBERS });
    const out = await ctx.c.handle(msg, bff);
    expect(out).toMatchObject({ status: 'done', stats: { delivered: 1, skipped: 1, stop_reason: 'voucher_exhausted' } });
    expect(ctx.email.enqueue).toHaveBeenCalledTimes(1);
  });

  it('cap anggaran HANYA bila CAMPAIGN_BUDGET_CAP_ENABLED (default nonaktif, L-157)', async () => {
    const payload = { budget_idr: 15000, voucher_value_idr: 10000 };
    const off = await setup({ payload, members: MEMBERS }).c.handle(msg, bff);
    expect(off).toMatchObject({ stats: { delivered: 3, stop_reason: null } });
    const on = await setup({ payload, members: MEMBERS, env: { CAMPAIGN_BUDGET_CAP_ENABLED: 'true' } }).c.handle(msg, bff);
    expect(on).toMatchObject({ stats: { delivered: 1, skipped: 1, stop_reason: 'budget_cap' } });
  });

  it('cap frekuensi HANYA bila CAMPAIGN_FREQUENCY_CAP_ENABLED', async () => {
    const off = setup({ freqCount: 99 });
    expect(await off.c.handle(msg, bff)).toMatchObject({ stats: { delivered: 2 } });
    expect(off.db.find('count(*) AS n FROM platform.campaign_delivery')).toHaveLength(0);
    const on = setup({ freqCount: 2, env: { CAMPAIGN_FREQUENCY_CAP_ENABLED: 'true', CAMPAIGN_MAX_PER_MEMBER_PER_WEEK: '2' } });
    expect(await on.c.handle(msg, bff)).toMatchObject({ stats: { delivered: 0, skipped: 2 } });
    expect(on.db.find("'skipped', $3")[0]!.params[2]).toBe('frequency_cap');
  });

  it('member_bound: tanpa penugasan kode (hanya notifikasi); public_code: kode bersama di inbox', async () => {
    const mb = setup({ payload: { voucher_mode: 'member_bound', voucher_codes: undefined } });
    await mb.c.handle(msg, bff);
    expect(mb.db.find('UPDATE platform.member_voucher')).toHaveLength(0);
    expect(mb.db.find('INSERT INTO platform.member_inbox')[0]!.params[5]).toBeNull();
    const pub = setup({ payload: { voucher_mode: 'public_code', voucher_codes: undefined, public_code: 'PUBLIC10' } });
    await pub.c.handle(msg, bff);
    expect(pub.db.find('INSERT INTO platform.member_inbox')[0]!.params[5]).toBe('pub-voucher');
  });

  it('markdown tanpa segmen → target dari wishlist produk + consent marketing; WA kedaluwarsa ≤ 24 jam & ≤ valid_to', async () => {
    const ctx = setup({
      payload: {
        kind: 'markdown',
        segment_run_id: null,
        channels: ['wa_unofficial'],
        voucher_mode: null,
        voucher_codes: undefined,
        valid_to: '2026-11-21T10:00:00Z',
        markdown: { channel_code: 'SMD', product_ids: [5], outlet_codes: ['SMD01'], valid_to: '2026-11-21T10:00:00Z' },
      },
    });
    await ctx.c.handle(msg, bff);
    expect(ctx.db.find('FROM platform.member_wishlist w')[0]!.params.slice(0, 2)).toEqual([[5], 'SMD']);
    const arg = ctx.wa.enqueue.mock.calls[0]![0] as { category: string; expiresAt: Date };
    expect(arg.category).toBe('promo');
    expect(arg.expiresAt.toISOString()).toBe('2026-11-21T10:00:00.000Z');
    expect(ctx.db.find('INSERT INTO platform.member_inbox')).toHaveLength(0);
  });

  it('lewat valid_to → expired tanpa target; sudah done → duplicate; flag mati → disabled', async () => {
    const exp = setup({ payload: { valid_to: '2026-11-21T00:00:00Z' } });
    expect(await exp.c.handle(msg, bff)).toEqual({ status: 'expired' });
    expect(exp.db.find('FROM behavior.segment_member')).toHaveLength(0);
    expect(await setup({ status: 'done' }).c.handle(msg, bff)).toEqual({ status: 'duplicate' });
    expect(await setup({ env: { PROMO_CAMPAIGN_ENABLED: 'false' } }).c.handle(msg, bff)).toEqual({ status: 'disabled' });
  });

  it('redelivery (campaign_delivery sudah ada) → tidak menugaskan kode, tidak mengisi inbox, tidak mengirim ulang kanal', async () => {
    const ctx = setup({ alreadyDelivered: true, members: MEMBERS });
    const out = await ctx.c.handle(msg, bff);
    expect(out).toMatchObject({ status: 'done', stats: { delivered: 0, skipped: 0, email_queued: 0, push_sent: 0 } });
    expect(ctx.db.find('UPDATE platform.member_voucher SET member_id')).toHaveLength(0);
    expect(ctx.db.find('INSERT INTO platform.member_inbox')).toHaveLength(0);
    expect(ctx.email.enqueue).not.toHaveBeenCalled();
  });

  it('target = member dengan consent marketing (+ tracking) DAN akun aktif; dievaluasi ulang pada setiap halaman (consent dicabut di tengah jalan)', async () => {
    const ctx = setup({ members: MEMBERS });
    await ctx.c.handle(msg, bff);
    const targetQueries = ctx.db.find('FROM behavior.segment_member');
    // satu halaman berisi (3 member < PAGE) + satu halaman kosong: consent dibaca di SQL tiap halaman, bukan di-cache
    expect(targetQueries.length).toBeGreaterThanOrEqual(2);
    for (const t of targetQueries) {
      expect(t.text).toContain('m.deleted_at IS NULL');
      expect(t.text).toContain('IS TRUE');
    }
  });

  it('publisher asing, key rusak, kampanye tidak ada → DLQ', async () => {
    const ctx = setup();
    await expect(ctx.c.handle(msg, { userId: 'efn-ocr' })).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.c.handle({ schema_version: 1, idempotency_key: 'campaign:x' }, bff)).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(setup({ missing: true }).c.handle(msg, bff)).rejects.toBeInstanceOf(PermanentMessageError);
  });
});
