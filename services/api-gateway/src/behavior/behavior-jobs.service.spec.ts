import { randomUUID } from 'node:crypto';
import { FakeDb, testConfig } from '../common/testing/fakes';
import { BehaviorJobsService } from './behavior-jobs.service';
import { eraseBehaviorMember, exportBehaviorMember, withdrawBehaviorTracking } from './behavior-privacy';
import { anonSubjectKey, deterministicUuid, deviceKey, memberSubjectKey } from './subject-key';

describe('BehaviorJobsService', () => {
  function setup(opts: { lock?: boolean; candidates?: Record<string, unknown>[]; dedupFresh?: boolean; env?: Record<string, string> } = {}) {
    const db = new FakeDb((t) => {
      if (t.includes('pg_try_advisory_xact_lock')) return { rows: [{ ok: opts.lock !== false }], rowCount: 1 };
      if (t.includes('ensure_event_partitions')) return { rows: [{ n: 2 }], rowCount: 1 };
      if (t.includes('drop_old_event_partitions')) return { rows: [{ n: 1 }], rowCount: 1 };
      if (t.includes('WITH last_add')) return { rows: opts.candidates ?? [], rowCount: (opts.candidates ?? []).length };
      if (t.includes('INSERT INTO behavior.event_dedup')) return opts.dedupFresh === false ? { rows: [], rowCount: 0 } : { rows: [{}], rowCount: 1 };
      if (t.startsWith('DELETE')) return { rows: [], rowCount: 3 };
      return undefined;
    });
    const svc = new BehaviorJobsService(testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true', ...opts.env }), db);
    return { db, svc };
  }

  it('lock dipegang replika lain → dilewati', async () => {
    const ctx = setup({ lock: false });
    expect((await ctx.svc.runOnce()).skipped).toBe(true);
    expect(ctx.db.find('ensure_event_partitions')).toHaveLength(0);
  });

  it('partisi + retensi (7 hari staging, 180 hari tautan, 30 hari segmen, 100 hari daftar erasure)', async () => {
    const ctx = setup();
    const r = await ctx.svc.runOnce();
    expect(r).toMatchObject({ skipped: false, partitionsCreated: 2, partitionsDropped: 1, purged: 24 });
    expect(ctx.db.find('drop_old_event_partitions')[0]!.params).toEqual([7]);
    expect(ctx.db.find('DELETE FROM behavior.device_link')[0]!.params).toEqual([180]);
    expect(ctx.db.find('DELETE FROM behavior.segment_member')[0]!.params).toEqual([30]);
    expect(ctx.db.find('DELETE FROM behavior.erased_subject')[0]!.params).toEqual([100]);
    // antrean erasure ETL: ≥ 100 hari (> TTL ClickHouse 90 hari)
    expect(ctx.db.find('DELETE FROM behavior.subject_erasure')[0]!.params).toEqual([100]);
  });

  it('retensi subject_erasure < 100 hari ditolak konfigurasi', () => {
    expect(() => testConfig({ BEHAVIOR_ERASED_SUBJECT_RETENTION_DAYS: '95' }).behaviorErasedSubjectRetentionDays).toThrow();
  });

  it('cart_abandon diturunkan server: event_id deterministik, channel/PT disalin dari cart_add (bukan "server"); sudah ada → tidak ditulis ulang', async () => {
    const cand = { subject_key: 'e'.repeat(64), subject_kind: 'device', event_id: randomUUID(), channel: 'app', channel_code: 'SMD', outlet_code: 'SMD01', company_code: 'SMD' };
    const ctx = setup({ candidates: [cand] });
    expect((await ctx.svc.runOnce()).cartAbandon).toBe(1);
    const expected = deterministicUuid(`efn:cart_abandon:${cand.subject_key}:${cand.event_id}`);
    expect(ctx.db.find('INSERT INTO behavior.event_dedup')[0]!.params).toEqual([cand.subject_key, expected]);
    const ins = ctx.db.find("'cart_abandon', now()")[0]!;
    expect(ins.params[3]).toBe(expected);
    expect(ins.params.slice(6)).toEqual(['app', 'SMD']);
    expect(ins.text).not.toContain("'server'");
    // V-01: kandidat hanya dari cart_add subject ber-consent; baris turunan ditandai consented
    expect(ctx.db.find('WITH last_add')[0]!.text).toContain("type = 'cart_add' AND consented");
    expect(ins.text).toMatch(/consented\)\s+VALUES/);
    const again = setup({ candidates: [cand], dedupFresh: false });
    expect((await again.svc.runOnce()).cartAbandon).toBe(0);
    expect(again.db.find('INSERT INTO behavior.event_raw')).toHaveLength(0);
  });

  it('error DB tidak melempar (job timer)', async () => {
    const db = new FakeDb(() => {
      throw new Error('db down');
    });
    const svc = new BehaviorJobsService(testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true' }), db);
    await expect(svc.runOnce()).resolves.toMatchObject({ skipped: true });
  });

  it('deterministicUuid: format UUID v8 stabil', () => {
    const a = deterministicUuid('x');
    expect(a).toBe(deterministicUuid('x'));
    expect(a).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(a).not.toBe(deterministicUuid('y'));
  });
});

describe('behavior-privacy (erasure/DSAR/penarikan consent)', () => {
  const MEMBER = randomUUID();
  const REQUEST = randomUUID();
  const PEPPER = 'p'.repeat(32);

  function db(ready: boolean, keys: string[] = [], devices: string[] = []) {
    return new FakeDb((t) => {
      if (t.includes('to_regclass')) return { rows: [{ ok: ready }], rowCount: 1 };
      if (t.includes('FROM behavior.subject_member WHERE member_id')) return { rows: keys.map((k) => ({ k })), rowCount: keys.length };
      if (t.includes('SELECT device_key FROM behavior.device_link WHERE member_id')) {
        return { rows: devices.map((device_key) => ({ device_key })), rowCount: devices.length };
      }
      return undefined;
    });
  }

  it('SEC-G50-07: erasure mencatat penarikan consent (granted=false) untuk SETIAP device tertaut, sebelum tautan dihapus', async () => {
    const d1 = '1'.repeat(64);
    const d2 = '2'.repeat(64);
    const d = db(true, [d1, d2], [d1, d2, d1]);
    await eraseBehaviorMember(d, MEMBER, PEPPER, REQUEST);
    const ins = d.find('INSERT INTO behavior.device_consent')[0]!;
    expect(ins.text).toContain('false');
    expect(ins.params).toEqual([[d1, d2], 'erasure', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855']);
    const texts = d.queries.map((q) => q.text);
    expect(texts.findIndex((t) => t.includes('INSERT INTO behavior.device_consent'))).toBeLessThan(
      texts.findIndex((t) => t.includes('DELETE FROM behavior.device_link WHERE member_id')),
    );
    const none = db(true, [], []);
    await eraseBehaviorMember(none, MEMBER, PEPPER, REQUEST);
    expect(none.find('INSERT INTO behavior.device_consent')).toHaveLength(0);
  });

  it('skema G50 belum ada → tidak ada query hapus (erasure akun tidak gagal)', async () => {
    const d = db(false);
    expect(await eraseBehaviorMember(d, MEMBER, PEPPER, REQUEST)).toBe(0);
    expect(d.queries.filter((q) => q.text.includes('DELETE') || q.text.includes('INSERT'))).toHaveLength(0);
  });

  it('erasure: SEMUA subject_key (HMAC member + device_key tertaut) ke subject_erasure dengan request_id SEBELUM staging dihapus', async () => {
    const dev = 'f'.repeat(64);
    const d = db(true, [dev]);
    const n = await eraseBehaviorMember(d, MEMBER, PEPPER, REQUEST);
    expect(n).toBe(2);
    const se = d.find('INSERT INTO behavior.subject_erasure')[0]!;
    expect(se.params[0]).toEqual(expect.arrayContaining([dev, memberSubjectKey(PEPPER, MEMBER)]));
    expect((se.params[0] as string[]).length).toBe(2);
    expect(se.params.slice(1)).toEqual([REQUEST, 'dsar_erasure']);
    const order = d.queries.map((q) => q.text);
    expect(order.findIndex((t) => t.includes('INSERT INTO behavior.subject_erasure'))).toBeLessThan(
      order.findIndex((t) => t.includes('DELETE FROM behavior.event_raw')),
    );
    const keys = d.find('DELETE FROM behavior.event_raw')[0]!.params[0] as string[];
    expect(keys).toEqual(expect.arrayContaining([dev, memberSubjectKey(PEPPER, MEMBER)]));
    expect(d.find('INSERT INTO behavior.erased_subject')).toHaveLength(1);
    expect(d.find('DELETE FROM behavior.device_link WHERE member_id')).toHaveLength(1);
    expect(d.find('DELETE FROM behavior.subject_member WHERE member_id')).toHaveLength(1);
  });

  it('penarikan tracking: hanya tautan & pemetaan dihapus; export tanpa device_key', async () => {
    const d = db(true);
    await withdrawBehaviorTracking(d, MEMBER);
    expect(d.find('DELETE FROM behavior.event_raw')).toHaveLength(0);
    expect(d.find('DELETE FROM behavior.device_link')).toHaveLength(1);
    const exp = await exportBehaviorMember(db(true), MEMBER, null);
    expect(exp).toEqual({ linked_devices: [], events: [], truncated: false });
  });

  it('V-01: erasure/export DSAR tidak menyentuh subject anonim (domain HMAC terpisah, tidak pernah di subject_member/device_link)', async () => {
    const devicePepper = 'd'.repeat(32);
    const deviceId = randomUUID();
    const linked = deviceKey(devicePepper, deviceId);
    const anon = anonSubjectKey(devicePepper, deviceId);
    expect(anon).not.toBe(linked);
    const d = db(true, [linked], [linked]);
    await eraseBehaviorMember(d, MEMBER, PEPPER, REQUEST);
    const touched = d.queries.flatMap((q) => q.params.flat() as unknown[]);
    expect(touched).toContain(linked);
    expect(touched).not.toContain(anon);
    const e = db(true, [linked], [linked]);
    await exportBehaviorMember(e, MEMBER, PEPPER);
    expect(e.queries.flatMap((q) => q.params.flat() as unknown[])).not.toContain(anon);
  });
});
