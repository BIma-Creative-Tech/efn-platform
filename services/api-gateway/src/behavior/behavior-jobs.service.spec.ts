import { randomUUID } from 'node:crypto';
import { FakeDb, testConfig } from '../common/testing/fakes';
import { BehaviorJobsService } from './behavior-jobs.service';
import { eraseBehaviorMember, exportBehaviorMember, withdrawBehaviorTracking } from './behavior-privacy';
import { deterministicUuid, memberSubjectKey } from './subject-key';

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
    expect(r).toMatchObject({ skipped: false, partitionsCreated: 2, partitionsDropped: 1, purged: 18 });
    expect(ctx.db.find('drop_old_event_partitions')[0]!.params).toEqual([7]);
    expect(ctx.db.find('DELETE FROM behavior.device_link')[0]!.params).toEqual([180]);
    expect(ctx.db.find('DELETE FROM behavior.segment_member')[0]!.params).toEqual([30]);
    expect(ctx.db.find('DELETE FROM behavior.erased_subject')[0]!.params).toEqual([100]);
  });

  it('cart_abandon diturunkan server dengan event_id deterministik; sudah ada → tidak ditulis ulang', async () => {
    const cand = { subject_key: 'e'.repeat(64), subject_kind: 'device', event_id: randomUUID(), channel_code: 'SMD', outlet_code: 'SMD01' };
    const ctx = setup({ candidates: [cand] });
    expect((await ctx.svc.runOnce()).cartAbandon).toBe(1);
    const expected = deterministicUuid(`efn:cart_abandon:${cand.subject_key}:${cand.event_id}`);
    expect(ctx.db.find('INSERT INTO behavior.event_dedup')[0]!.params).toEqual([cand.subject_key, expected]);
    expect(ctx.db.find("'cart_abandon', now()")[0]!.params[3]).toBe(expected);
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
  const PEPPER = 'p'.repeat(32);

  function db(ready: boolean, keys: string[] = []) {
    return new FakeDb((t) => {
      if (t.includes('to_regclass')) return { rows: [{ ok: ready }], rowCount: 1 };
      if (t.includes('FROM behavior.subject_member WHERE member_id')) return { rows: keys.map((k) => ({ k })), rowCount: keys.length };
      return undefined;
    });
  }

  it('skema G50 belum ada → tidak ada query hapus (erasure akun tidak gagal)', async () => {
    const d = db(false);
    expect(await eraseBehaviorMember(d, MEMBER, PEPPER)).toBe(0);
    expect(d.queries.filter((q) => q.text.includes('DELETE'))).toHaveLength(0);
  });

  it('erasure: hapus staging/dedup/segmen untuk semua pseudonim + catat erased_subject + hapus tautan', async () => {
    const dev = 'f'.repeat(64);
    const d = db(true, [dev]);
    const n = await eraseBehaviorMember(d, MEMBER, PEPPER);
    expect(n).toBe(2);
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
});
