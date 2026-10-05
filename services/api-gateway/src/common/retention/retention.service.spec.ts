import { FakeDb, testConfig } from '../testing/fakes';
import { RETENTION_LOCK_KEY, RetentionService } from './retention.service';

describe('RetentionService (UU PDP, SEC-11)', () => {
  it('menghapus otp_audit > 90 hari, processed_message & outbox terkirim > 14 hari (default)', async () => {
    const db = new FakeDb((text) => {
      if (text.includes('pg_try_advisory_xact_lock')) return { rows: [{ ok: true }], rowCount: 1 };
      if (text.includes('DELETE FROM platform.otp_audit')) return { rows: [], rowCount: 3 };
      if (text.includes('DELETE FROM platform.outbox')) return { rows: [], rowCount: 2 };
      return { rows: [], rowCount: 0 };
    });
    const svc = new RetentionService(db, testConfig());
    const res = await svc.purgeOnce();
    expect(res).toEqual({ otpAudit: 3, processedMessage: 0, outbox: 2, reportUserEncNulled: 0, reportDeleted: 0, skipped: false });

    expect(db.find('pg_try_advisory_xact_lock')[0].params).toEqual([RETENTION_LOCK_KEY]);
    expect(db.find('DELETE FROM platform.otp_audit')[0].params).toEqual([90, 5000]);
    expect(db.find('DELETE FROM platform.processed_message')[0].params).toEqual([14, 5000]);
    const outbox = db.find('DELETE FROM platform.outbox')[0];
    expect(outbox.params).toEqual([14, 5000]);
    // outbox yang belum terkirim tidak pernah dihapus
    expect(outbox.text).toContain('published_at IS NOT NULL');
  });

  it('batas retensi dari env', async () => {
    const db = new FakeDb((text) => (text.includes('advisory') ? { rows: [{ ok: true }], rowCount: 1 } : undefined));
    const svc = new RetentionService(
      db,
      testConfig({ OTP_AUDIT_RETENTION_DAYS: '30', PROCESSED_MESSAGE_RETENTION_DAYS: '7', OUTBOX_RETENTION_DAYS: '3' }),
    );
    await svc.purgeOnce();
    expect(db.find('platform.otp_audit')[0].params[0]).toBe(30);
    expect(db.find('platform.processed_message')[0].params[0]).toBe(7);
    expect(db.find('platform.outbox')[0].params[0]).toBe(3);
  });

  it('docs/04 §8.4: user_enc laporan → NULL > 90 hari, baris laporan dihapus > 730 hari (env)', async () => {
    const db = new FakeDb((text) => {
      if (text.includes('advisory')) return { rows: [{ ok: true }], rowCount: 1 };
      if (text.includes('SET user_enc = NULL')) return { rows: [], rowCount: 4 };
      if (text.includes('DELETE FROM platform.price_hunter_report')) return { rows: [], rowCount: 1 };
      return { rows: [], rowCount: 0 };
    });
    const res = await new RetentionService(db, testConfig()).purgeOnce();
    expect(res.reportUserEncNulled).toBe(4);
    expect(res.reportDeleted).toBe(1);
    expect(db.find('SET user_enc = NULL')[0].params).toEqual([90, 5000]);
    expect(db.find('SET user_enc = NULL')[0].text).not.toContain('updated_at');
    expect(db.find('DELETE FROM platform.price_hunter_report')[0].params).toEqual([730, 5000]);

    const db2 = new FakeDb((text) => (text.includes('advisory') ? { rows: [{ ok: true }], rowCount: 1 } : undefined));
    await new RetentionService(
      db2,
      testConfig({ PRICE_HUNTER_USER_ENC_RETENTION_DAYS: '30', PRICE_HUNTER_REPORT_RETENTION_DAYS: '365' }),
    ).purgeOnce();
    expect(db2.find('SET user_enc = NULL')[0].params[0]).toBe(30);
    expect(db2.find('DELETE FROM platform.price_hunter_report')[0].params[0]).toBe(365);
  });

  it('replika lain memegang lock → dilewati tanpa DELETE', async () => {
    const db = new FakeDb((text) => (text.includes('advisory') ? { rows: [{ ok: false }], rowCount: 1 } : undefined));
    const res = await new RetentionService(db, testConfig()).purgeOnce();
    expect(res.skipped).toBe(true);
    expect(db.find('DELETE')).toHaveLength(0);
  });

  it('DB error tidak melempar (job berikutnya mencoba lagi)', async () => {
    const db = new FakeDb(() => {
      throw new Error('db down');
    });
    await expect(new RetentionService(db, testConfig()).purgeOnce()).resolves.toMatchObject({ skipped: true });
  });
});
