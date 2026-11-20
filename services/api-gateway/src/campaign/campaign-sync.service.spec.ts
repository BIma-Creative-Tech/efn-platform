import { OutboxService } from '../common/outbox/outbox.service';
import { FakeDb, testCipher, testConfig } from '../common/testing/fakes';
import { CampaignSyncService, VOUCHER_CODE_AAD, voucherCodeAad, voucherCodeHash } from './campaign-sync.service';
import { MockOdooCampaign, OdooCampaignApi, OdooCampaignError } from './odoo-campaign';
import { campaignPayload } from './testing/campaign-fixtures';

function setup(opts: { existing?: boolean; ackFails?: boolean; dbFails?: boolean } = {}) {
  const config = testConfig({ PROMO_CAMPAIGN_ENABLED: 'true', CAMPAIGN_COMPANIES: 'SMD', ODOO_ONLINE_MOCK: 'true', APP_ENV: 'test' });
  const cipher = testCipher(config);
  const odoo = new MockOdooCampaign();
  if (opts.ackFails) odoo.ack = async () => Promise.reject(new OdooCampaignError('transport'));
  const enqueued: { queue: string; payload: Record<string, unknown>; id: string }[] = [];
  const outbox = { enqueue: jest.fn(async (_q: unknown, queue: string, payload: Record<string, unknown>, id: string) => void enqueued.push({ queue, payload, id })) } as unknown as OutboxService;
  const db = new FakeDb((t) => {
    if (opts.dbFails && t.includes('INSERT INTO platform.campaign')) throw new Error('db down');
    if (t.includes('INSERT INTO platform.campaign ')) return opts.existing ? { rows: [], rowCount: 0 } : { rows: [{ campaign_key: 'k' }], rowCount: 1 };
    return undefined;
  });
  const svc = new CampaignSyncService(config, db, odoo as OdooCampaignApi, outbox, cipher);
  return { svc, db, odoo, enqueued, config, cipher };
}

describe('CampaignSyncService (klaim outbox Odoo → PG + outbox → ack)', () => {
  it('kampanye baru: baris campaign + kode terenkripsi + outbox promo.campaign.requested (tanpa kode) → ack', async () => {
    const ctx = setup();
    ctx.odoo.queued.set('SMD', [{ id: 41, approval_rev: 2, payload: campaignPayload() }]);
    expect(await ctx.svc.syncCompany('SMD')).toEqual({ claimed: 1, stored: 1, acked: 1, rejected: 0 });
    const ins = ctx.db.find('INSERT INTO platform.member_voucher')[0]!;
    const [key, mode, , ids, encs, hashes] = ins.params as [string, string, string, string[], Buffer[], string[]];
    expect(key).toBe('campaign:SMD:7:2');
    expect(mode).toBe('anonymous_code');
    // SEC-G50-11: ditulis dengan AAD v2 terikat id baris; AAD v1 kolom atau id lain tidak bisa membukanya
    expect(ctx.cipher.decrypt(encs[0]!, voucherCodeAad(ids[0]!))).toBe('SMD-AAAA-0001');
    expect(() => ctx.cipher.decrypt(encs[0]!, VOUCHER_CODE_AAD)).toThrow();
    expect(() => ctx.cipher.decrypt(encs[0]!, voucherCodeAad(ids[1]!))).toThrow();
    expect(hashes[1]).toBe(voucherCodeHash(ctx.config.redisKeyPepper, 'SMD-AAAA-0002'));
    expect(JSON.stringify(ins.params)).not.toContain('SMD-AAAA-0001');
    expect(ctx.enqueued).toHaveLength(1);
    expect(ctx.enqueued[0]).toMatchObject({ queue: 'promo.campaign.requested', id: 'campaign:SMD:7:2', payload: { idempotency_key: 'campaign:SMD:7:2', schema_version: 1 } });
    expect(JSON.stringify(ctx.enqueued[0]!.payload)).not.toContain('SMD-AAAA');
    expect(JSON.stringify(ctx.db.find('INSERT INTO platform.campaign ')[0]!.params)).not.toContain('SMD-AAAA');
    expect(ctx.odoo.acked).toEqual([{ company: 'SMD', ids: [41] }]);
  });

  it('klaim ulang (campaign_key sudah ada) → tidak menulis/menerbitkan ulang, tetap ack', async () => {
    const ctx = setup({ existing: true });
    ctx.odoo.queued.set('SMD', [{ id: 41, approval_rev: 2, payload: campaignPayload() }]);
    expect(await ctx.svc.syncCompany('SMD')).toMatchObject({ stored: 0, acked: 1 });
    expect(ctx.enqueued).toHaveLength(0);
    expect(ctx.db.find('INSERT INTO platform.member_voucher')).toHaveLength(0);
  });

  it('baris tidak valid tidak di-ack; gagal DB tidak di-ack; ack gagal tidak melempar', async () => {
    const ctx = setup();
    ctx.odoo.queued.set('SMD', [{ id: 1, approval_rev: 0, payload: campaignPayload({ company_code: 'PB' }) }]);
    expect(await ctx.svc.syncCompany('SMD')).toEqual({ claimed: 0, stored: 0, acked: 0, rejected: 1 });
    expect(ctx.odoo.acked).toEqual([]);
    const dbDown = setup({ dbFails: true });
    dbDown.odoo.queued.set('SMD', [{ id: 2, approval_rev: 0, payload: campaignPayload() }]);
    expect(await dbDown.svc.syncCompany('SMD')).toMatchObject({ stored: 0, acked: 0 });
    const ackDown = setup({ ackFails: true });
    ackDown.odoo.queued.set('SMD', [{ id: 3, approval_rev: 0, payload: campaignPayload() }]);
    await expect(ackDown.svc.syncCompany('SMD')).resolves.toMatchObject({ stored: 1, acked: 0 });
  });

  it('flag mati → runOnce tidak mengklaim apa pun', async () => {
    const ctx = setup();
    const off = new CampaignSyncService(testConfig(), ctx.db, ctx.odoo, {} as OutboxService, ctx.cipher);
    ctx.odoo.queued.set('SMD', [{ id: 1, approval_rev: 0, payload: campaignPayload() }]);
    await off.runOnce();
    expect(ctx.odoo.queued.get('SMD')).toHaveLength(1);
  });
});
