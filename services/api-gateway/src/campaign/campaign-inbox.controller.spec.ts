import { randomUUID } from 'node:crypto';
import { FakeDb, catchApiError, testCipher, testConfig } from '../common/testing/fakes';
import { CampaignInboxService } from './campaign-inbox.controller';
import { eraseCampaignMember, exportCampaignMember } from './campaign-privacy';
import { VOUCHER_CODE_AAD, decryptVoucherCode, voucherCodeAad } from './campaign-sync.service';

const user = { memberId: randomUUID(), memberHash: 'a'.repeat(64) };

describe('CampaignInboxService (GET /v1/me/inbox, usulan kontrak G50-A)', () => {
  it('flag mati → 404', async () => {
    const config = testConfig();
    const svc = new CampaignInboxService(config, new FakeDb(), testCipher(config));
    expect((await catchApiError(() => svc.list(user))).getStatus()).toBe(404);
  });

  it('kode anonymous_code hanya didekripsi untuk member yang ditugaskan; public_code untuk semua penerima', async () => {
    const config = testConfig({ PROMO_CAMPAIGN_ENABLED: 'true' });
    const cipher = testCipher(config);
    const row = (mode: string, owner: string | null, code: string, aad: 'v1' | 'v2' | 'swapped' = 'v2') => {
      const vId = randomUUID();
      const key = aad === 'v1' ? VOUCHER_CODE_AAD : voucherCodeAad(aad === 'swapped' ? randomUUID() : vId);
      return {
        id: randomUUID(),
        title: 'Promo',
        body: 'Isi',
        valid_to: new Date('2026-12-01T00:00:00Z'),
        created_at: new Date('2026-11-20T00:00:00Z'),
        v_id: vId,
        code_enc: cipher.encrypt(code, key),
        mode,
        v_member: owner,
      };
    };
    const db = new FakeDb((t) =>
      t.includes('FROM platform.member_inbox i')
        ? {
            rows: [
              row('anonymous_code', user.memberId, 'MINE-0001'),
              row('anonymous_code', randomUUID(), 'OTHER-0001'),
              row('public_code', null, 'PUBLIC10'),
              row('anonymous_code', user.memberId, 'SWAP-0001', 'swapped'), // ciphertext baris lain → tidak terbaca
              row('public_code', null, 'OLDV1', 'v1'), // tanpa cadangan v1 (re-review SEC-G50-11) → tidak terbaca
            ],
            rowCount: 5,
          }
        : undefined,
    );
    const res = await new CampaignInboxService(config, db, cipher).list(user);
    expect(res.items.map((i) => i.voucher?.code ?? null)).toEqual(['MINE-0001', null, 'PUBLIC10', null, null]);
    expect(db.queries[0]!.params).toEqual([user.memberId]);
  });

  it('SEC-G50-11: ciphertext v2 terikat id voucher (tidak bisa ditukar antar-baris)', () => {
    const config = testConfig();
    const cipher = testCipher(config);
    const a = randomUUID();
    const blob = cipher.encrypt('CODE-A', voucherCodeAad(a));
    expect(decryptVoucherCode(cipher, a.toUpperCase(), blob)).toBe('CODE-A');
    expect(() => decryptVoucherCode(cipher, randomUUID(), blob)).toThrow();
    // tanpa cadangan v1 kolom
    expect(() => decryptVoucherCode(cipher, a, cipher.encrypt('CODE-A', VOUCHER_CODE_AAD))).toThrow();
  });
});

describe('campaign-privacy', () => {
  it('erasure: inbox & delivery dihapus, kode voucher hanya dilepas tautannya (tidak kembali ke pool)', async () => {
    const db = new FakeDb((t) => (t.includes('to_regclass') ? { rows: [{ ok: true }], rowCount: 1 } : undefined));
    await eraseCampaignMember(db, user.memberId);
    expect(db.find('DELETE FROM platform.member_inbox')).toHaveLength(1);
    expect(db.find('DELETE FROM platform.campaign_delivery')).toHaveLength(1);
    const upd = db.find('UPDATE platform.member_voucher SET member_id = NULL')[0]!;
    expect(upd.text).not.toContain('assigned_at = NULL');
    expect(await exportCampaignMember(db, user.memberId)).toEqual({ inbox: [], vouchers: [] });
  });

  it('skema belum ada → no-op', async () => {
    const db = new FakeDb((t) => (t.includes('to_regclass') ? { rows: [{ ok: false }], rowCount: 1 } : undefined));
    await eraseCampaignMember(db, user.memberId);
    expect(db.queries).toHaveLength(1);
  });
});
