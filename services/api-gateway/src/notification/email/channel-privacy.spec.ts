import { randomUUID } from 'node:crypto';
import { FakeDb, testCipher, testConfig } from '../../common/testing/fakes';
import { exportChannelMember } from './channel-privacy';
import { MEMBER_EMAIL_AAD, encryptMemberEmail } from './email.constants';

const PHONE = '6281234567890';
const H = 'ab'.repeat(32);

function setup(opts: { migrated?: boolean; emailEnc?: (memberId: string) => Buffer } = {}) {
  const app = testConfig();
  const cipher = testCipher(app);
  const memberId = randomUUID();
  const db = new FakeDb((t, p) => {
    if (t.includes("to_regclass('platform.member_email')")) return { rows: [{ ok: opts.migrated !== false }], rowCount: 1 };
    if (t.includes('FROM platform.member_email')) {
      return opts.emailEnc
        ? {
            rows: [{ email_enc: opts.emailEnc(String(p[0])), verified_at: '2026-11-19T01:00:00Z', status: 'active', status_reason: null, updated_at: '2026-11-19T01:00:00Z' }],
            rowCount: 1,
          }
        : { rows: [], rowCount: 0 };
    }
    if (t.includes('FROM platform.email_outbound')) {
      return { rows: [{ source: 'restock', source_ref: 'restock:1', template: 'restock_available', status: 'sent', queued_at: '2026-11-19T02:00:00Z', done_at: '2026-11-19T02:01:00Z' }], rowCount: 1 };
    }
    if (t.includes('FROM platform.wa_unofficial_recipient')) return { rows: [{ to_hash: H, last_queued_at: '2026-11-19T03:00:00Z' }], rowCount: 1 };
    if (t.includes('FROM platform.wa_unofficial_outbound')) {
      return {
        rows: [
          {
            category: 'notify_restock',
            source: 'restock',
            source_ref: 'restock:1',
            template: 'restock_available',
            status: 'failed',
            reason: 'not_on_whatsapp',
            queued_at: '2026-11-19T03:00:00Z',
            expires_at: '2026-11-20T03:00:00Z',
            result_at: '2026-11-19T03:00:10Z',
          },
        ],
        rowCount: 1,
      };
    }
    return undefined;
  });
  return { cipher, memberId, db };
}

describe('DSAR export kanal G50-B (SEC-G50-05)', () => {
  it('email didekripsi untuk subjek + status verifikasi; jejak email & WA; nomor subjek sendiri saja', async () => {
    // kunci test acak per config → enkripsi & export memakai cipher yang sama (ctx.cipher)
    const ctx = setup();
    const ctx2 = setup({ emailEnc: (id) => encryptMemberEmail(ctx.cipher, 'siti@contoh.id', id) });
    const out = await exportChannelMember(ctx2.db, ctx.cipher, ctx2.memberId, PHONE);
    expect(out.email).toEqual({
      address: 'siti@contoh.id',
      readable: true,
      verified: true,
      verified_at: '2026-11-19T01:00:00.000Z',
      status: 'active',
      status_reason: null,
      updated_at: '2026-11-19T01:00:00.000Z',
    });
    expect(out.email_deliveries).toEqual([
      { source: 'restock', source_ref: 'restock:1', template: 'restock_available', status: 'sent', queued_at: '2026-11-19T02:00:00.000Z', done_at: '2026-11-19T02:01:00.000Z' },
    ]);
    expect(out.wa_unofficial.recipients).toEqual([{ phone: PHONE, phone_masked: '62812****890', to_hash: H, last_queued_at: '2026-11-19T03:00:00.000Z' }]);
    expect(out.wa_unofficial.deliveries[0]).toMatchObject({ category: 'notify_restock', status: 'failed', reason: 'not_on_whatsapp' });
    expect(out.truncated).toBe(false);
    // semua query dibatasi member_id pemanggil
    for (const q of ctx2.db.queries.filter((x) => x.text.includes('WHERE member_id = $1'))) expect(q.params[0]).toBe(ctx2.memberId);
  });

  it('email v1 lama tetap diekspor; ciphertext milik member lain → address null (export tidak gagal)', async () => {
    const base = setup();
    const legacy = setup({ emailEnc: () => base.cipher.encrypt('lama@contoh.id', MEMBER_EMAIL_AAD) });
    expect((await exportChannelMember(legacy.db, base.cipher, legacy.memberId, PHONE)).email?.address).toBe('lama@contoh.id');
    const swapped = setup({ emailEnc: () => encryptMemberEmail(base.cipher, 'orang-lain@contoh.id', randomUUID()) });
    const out = await exportChannelMember(swapped.db, base.cipher, swapped.memberId, PHONE);
    expect(out.email).toMatchObject({ address: null, readable: false });
    expect(JSON.stringify(out)).not.toContain('orang-lain@contoh.id');
  });

  it('tanpa baris email → email null; migrasi 21 belum ada → kosong tanpa query tabel', async () => {
    const ctx = setup();
    expect((await exportChannelMember(ctx.db, ctx.cipher, ctx.memberId, PHONE)).email).toBeNull();
    const old = setup({ migrated: false });
    expect(await exportChannelMember(old.db, old.cipher, old.memberId, PHONE)).toEqual({
      email: null,
      email_deliveries: [],
      wa_unofficial: { recipients: [], deliveries: [] },
      truncated: false,
    });
    expect(old.db.queries).toHaveLength(1);
  });
});
