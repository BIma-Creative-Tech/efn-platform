import { testConfig } from '../common/testing/fakes';
import { Semaphore } from '../common/concurrency/semaphore';
import { Json2OdooCampaignClient, OdooCampaignError, parseCampaignPayload, parseClaim } from './odoo-campaign';
import { campaignPayload } from './testing/campaign-fixtures';

describe('parse klaim kampanye Odoo (allowlist, §28.7)', () => {
  it('payload valid → kode dipisah dari payload antrean', () => {
    const { payload, codes } = parseCampaignPayload(campaignPayload({ extra_field: 'x' }), 'SMD');
    expect(codes).toEqual(['SMD-AAAA-0001', 'SMD-AAAA-0002']);
    expect(payload).not.toHaveProperty('voucher_codes');
    expect(payload).not.toHaveProperty('extra_field');
    expect(payload.channels).toEqual(['inbox', 'push', 'email']);
  });

  it('PT lain, segmen kosong, kode tanpa mode, kanal asing, rentang tanggal terbalik → ditolak (tidak di-ack)', () => {
    const res = parseClaim(
      {
        items: [
          { id: 1, approval_rev: 0, payload: campaignPayload() },
          { id: 2, approval_rev: 0, payload: campaignPayload({ company_code: 'PB' }) },
          { id: 3, approval_rev: 0, payload: campaignPayload({ segment_run_id: null }) },
          { id: 4, approval_rev: 0, payload: campaignPayload({ voucher_mode: 'member_bound' }) },
          { id: 5, approval_rev: 0, payload: campaignPayload({ channels: ['sms'] }) },
          { id: 6, approval_rev: 0, payload: campaignPayload({ valid_to: '2026-11-19T00:00:00Z' }) },
          { id: 7, approval_rev: 1, payload: campaignPayload({ voucher_codes: ['bad code!'] }) },
          { approval_rev: 0, payload: campaignPayload() },
        ],
      },
      'SMD',
    );
    expect(res.items.map((i) => i.outboxId)).toEqual([1]);
    expect(res.rejected.map((r) => [r.outboxId, r.reason])).toEqual([
      [2, 'company_mismatch'],
      [3, 'segment_required'],
      [4, 'codes_without_mode'],
      [5, 'channels'],
      [6, 'valid_range'],
      [7, 'voucher_codes'],
      [null, 'id'],
    ]);
  });

  it('markdown tanpa segmen + public_code; member_bound tanpa kode', () => {
    const md = parseCampaignPayload(
      campaignPayload({
        kind: 'markdown',
        segment_run_id: null,
        voucher_mode: 'public_code',
        voucher_codes: undefined,
        public_code: 'MARKDOWN50',
        markdown: { channel_code: 'SMD', product_ids: [5, 5, 6], outlet_codes: ['SMD01'], valid_to: '2026-11-21T00:00:00Z' },
      }),
      'SMD',
    );
    expect(md.codes).toEqual(['MARKDOWN50']);
    expect(md.payload.markdown?.product_ids).toEqual([5, 6]);
    const mb = parseCampaignPayload(campaignPayload({ voucher_mode: 'member_bound', voucher_codes: undefined }), 'SMD');
    expect(mb.codes).toEqual([]);
  });

  it('SATU format (SEC-G50-02): bentuk lama Odoo (title/body datar, voucher{}, tanggal YYYY-MM-DD, ref integer) DITOLAK', () => {
    const legacy = campaignPayload({ message: undefined, title: 'X', body: 'Y' });
    delete legacy.message;
    expect(() => parseCampaignPayload(legacy, 'SMD')).toThrow();
    expect(() => parseCampaignPayload(campaignPayload({ valid_from: '2026-11-20', valid_to: '2026-11-27' }), 'SMD')).toThrow('valid_from');
    expect(() => parseCampaignPayload(campaignPayload({ voucher_program_ref: 12 }), 'SMD')).toThrow('voucher_program_ref');
    const nested = campaignPayload({ voucher_codes: undefined, voucher: { mode: 'anonymous_code', codes: ['SMD-AAAA-0001'] } });
    expect(() => parseCampaignPayload(nested, 'SMD')).toThrow('voucher_codes');
  });

  it('SEC-G50-20: schema_version ≠ 1 / tidak ada → unsupported_schema (baris klaim ditolak, tidak di-ack)', () => {
    expect(() => parseCampaignPayload(campaignPayload({ schema_version: 2 }), 'SMD')).toThrow('unsupported_schema');
    expect(() => parseCampaignPayload(campaignPayload({ schema_version: '1' }), 'SMD')).toThrow('unsupported_schema');
    const missing = campaignPayload();
    delete missing.schema_version;
    expect(() => parseCampaignPayload(missing, 'SMD')).toThrow('unsupported_schema');
    const res = parseClaim({ items: [{ id: 3, approval_rev: 0, payload: campaignPayload({ schema_version: 2 }) }] }, 'SMD');
    expect(res.rejected).toEqual([{ outboxId: 3, reason: 'unsupported_schema' }]);
    expect(parseCampaignPayload(campaignPayload(), 'SMD').payload).not.toHaveProperty('schema_version');
  });

  it('respons bukan {items: []} → invalid_response', () => {
    expect(() => parseClaim({ rows: [] }, 'SMD')).toThrow(OdooCampaignError);
  });
});

describe('Json2OdooCampaignClient', () => {
  const gates = { public: new Semaphore(2, 16, 2000), saga: new Semaphore(2, 16, 2000) };
  const config = testConfig({ ODOO_API_KEY_CAMPAIGN_SMD: 'k'.repeat(32), APP_ENV: 'dev' });

  it('claim memanggil path allowlist dengan Bearer per PT dan TIDAK diulang saat transport gagal', async () => {
    const transport = jest.fn(async () => ({ status: 503, body: '' }));
    const c = new Json2OdooCampaignClient(config, transport, gates);
    c.sleep = async () => undefined;
    await expect(c.claim('SMD', 50)).rejects.toMatchObject({ kind: 'transport' });
    expect(transport).toHaveBeenCalledTimes(1);
    const [path, body, headers] = transport.mock.calls[0] as unknown as [string, string, Record<string, string>];
    expect(path).toBe('efn.campaign/efn_campaign_claim');
    expect(JSON.parse(body).limit).toBe(20);
    expect(headers.Authorization).toBe(`Bearer ${'k'.repeat(32)}`);
  });

  it('ack idempoten → diulang saat transport gagal; error bisnis tidak diulang', async () => {
    const transport = jest.fn().mockResolvedValueOnce({ status: 502, body: '' }).mockResolvedValueOnce({ status: 200, body: '{"ok":true}' });
    const c = new Json2OdooCampaignClient(config, transport, gates);
    c.sleep = async () => undefined;
    await c.ack('SMD', [1, 2]);
    expect(transport).toHaveBeenCalledTimes(2);
    const biz = jest.fn(async () => ({ status: 422, body: JSON.stringify({ message: 'efn_campaign:company_mismatch' }) }));
    const c2 = new Json2OdooCampaignClient(config, biz, gates);
    await expect(c2.ack('SMD', [1])).rejects.toMatchObject({ kind: 'business', code: 'company_mismatch' });
    expect(biz).toHaveBeenCalledTimes(1);
  });

  it('API key PT belum dikonfigurasi → auth (tanpa memanggil Odoo)', async () => {
    const transport = jest.fn();
    const c = new Json2OdooCampaignClient(testConfig(), transport, gates);
    await expect(c.claim('SMD', 1)).rejects.toMatchObject({ kind: 'auth' });
    expect(transport).not.toHaveBeenCalled();
  });
});
