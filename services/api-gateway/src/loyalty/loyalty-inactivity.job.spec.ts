import { Logger } from '@nestjs/common';
import { MetricsService } from '../common/metrics/metrics.service';
import { FakeClock, testConfig } from '../common/testing/fakes';
import { EXPIRY_LOCK, ExpiryStore, LoyaltyInactivityJob, NoticeCandidate, NoticeRow, calendarExpiry } from './loyalty-inactivity.job';
import { CompanyCode, LedgerBucket } from './loyalty.service';

/** G22 docs/04 §14.D (W-17; L-73/L-75/L-76/L-77) — store in-memory, tanpa DB/Redis/AMQP. */

const H1 = '1'.repeat(64);
const H2 = '2'.repeat(64);
const DAY = 86_400_000;

class MemStore implements ExpiryStore {
  activity = new Map<string, Date>();
  lotsBy = new Map<string, LedgerBucket[]>();
  notices = new Map<string, NoticeRow & { status: string }>();
  outbox: { queue: string; payload: Record<string, unknown> }[] = [];
  entries: { memberHash: string; issuer: CompanyCode; points: number; expiresAt: Date | null }[] = [];
  blocked = new Set<string>();
  retention: string[] = [];
  locks: number[] = [];
  months = 12;
  async tx<T>(fn: (s: ExpiryStore) => Promise<T>): Promise<T> {
    return fn(this);
  }
  async tryLock(key: number): Promise<boolean> {
    this.locks.push(key);
    return true;
  }
  async noticeCandidates(p: { epoch: Date; months: number; noticeDays: number; now: Date; limit: number }): Promise<NoticeCandidate[]> {
    const out: NoticeCandidate[] = [];
    for (const [h, last] of this.activity) {
      const anchor = new Date(Math.max(last.getTime(), p.epoch.getTime()));
      const expiry = calendarExpiry(anchor, p.months);
      if (expiry.getTime() - p.noticeDays * DAY > p.now.getTime()) continue;
      if ([...this.notices.values()].some((n) => n.member_hash === h && n.anchor_at.getTime() === anchor.getTime())) continue;
      if ((this.lotsBy.get(h) ?? []).reduce((s, b) => s + b.remaining, 0) <= 0) continue;
      out.push({ member_hash: h, anchor_at: anchor, expiry_at: expiry });
    }
    return out.slice(0, p.limit);
  }
  async insertNotice(n: NoticeRow & { notified_at: Date }): Promise<boolean> {
    if ([...this.notices.values()].some((x) => x.member_hash === n.member_hash && x.anchor_at.getTime() === n.anchor_at.getTime())) return false;
    this.notices.set(n.id, { ...n, status: 'queued' });
    return true;
  }
  async supersedeOlder(h: string, anchorAt: Date): Promise<number> {
    let k = 0;
    for (const n of this.notices.values()) {
      if (n.member_hash === h && n.status === 'queued' && n.anchor_at < anchorAt) {
        n.status = 'superseded';
        k += 1;
      }
    }
    return k;
  }
  async enqueue(queue: string, payload: Record<string, unknown>): Promise<void> {
    this.outbox.push({ queue, payload });
  }
  async countDue(now: Date): Promise<number> {
    return [...this.notices.values()].filter((n) => n.status === 'queued' && n.due_at <= now).length;
  }
  async dueNotices(now: Date, limit: number): Promise<NoticeRow[]> {
    return [...this.notices.values()].filter((n) => n.status === 'queued' && n.due_at <= now).slice(0, limit);
  }
  async findNotice(h: string, anchorAt: Date): Promise<NoticeRow | null> {
    return [...this.notices.values()].find((n) => n.member_hash === h && n.anchor_at.getTime() === anchorAt.getTime() && n.status === 'queued') ?? null;
  }
  async lockMember(): Promise<void> {}
  async currentAnchor(h: string, epoch: Date): Promise<Date | null> {
    const a = this.activity.get(h);
    return a ? new Date(Math.max(a.getTime(), epoch.getTime())) : null;
  }
  async hasBlocker(h: string): Promise<boolean> {
    return this.blocked.has(h);
  }
  async lots(h: string): Promise<LedgerBucket[]> {
    return this.lotsBy.get(h) ?? [];
  }
  async latestMemberRefEnc(): Promise<Buffer | null> {
    return Buffer.alloc(40, 1);
  }
  async insertExpireEntry(e: { memberHash: string; enc: Buffer; issuer: CompanyCode; points: number; expiresAt: Date | null }): Promise<void> {
    this.entries.push({ memberHash: e.memberHash, issuer: e.issuer, points: e.points, expiresAt: e.expiresAt });
    const lots = this.lotsBy.get(e.memberHash) ?? [];
    for (const b of lots) if (b.issuer === e.issuer && (b.expiresAt?.getTime() ?? null) === (e.expiresAt?.getTime() ?? null)) b.remaining -= e.points;
  }
  async setNoticeStatus(id: string, status: 'superseded' | 'expired' | 'cancelled'): Promise<void> {
    const n = this.notices.get(id);
    if (n) n.status = status;
  }
  async scheduleRetention(h: string): Promise<boolean> {
    if (this.retention.includes(h)) return false;
    this.retention.push(h);
    return true;
  }
  async purge(): Promise<number> {
    return 0;
  }
  async cancelRetentionOnActivity(): Promise<number> {
    return 0;
  }
  consented = new Set<string>();
  proofs: { noticeId: string; expireDate: string; channel: string }[] = [];
  async hasMarketingConsent(h: string): Promise<boolean> {
    return this.consented.has(h);
  }
  async recordInAppProof(noticeId: string, expireDate: string): Promise<void> {
    if (!this.proofs.some((p) => p.noticeId === noticeId)) this.proofs.push({ noticeId, expireDate, channel: 'in_app' });
  }
}

function setup(env: Record<string, string> = {}) {
  const clock = new FakeClock(Date.UTC(2027, 10, 1, 3, 0, 0)); // 1 Nov 2027 10:00 WIB
  const config = testConfig({ LOYALTY_EXPIRY_ENABLED: 'true', LOYALTY_EXPIRY_DRY_RUN: 'false', LOYALTY_ACTIVITY_EPOCH: '2026-11-01', ...env });
  const store = new MemStore();
  const metrics = new MetricsService();
  const job = new LoyaltyInactivityJob(config, store, metrics);
  job.now = clock.now;
  metrics.initLoyaltyExpirySeries();
  return { clock, config, store, metrics, job };
}

function quietLogs(): () => void {
  const a = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
  const b = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
  const c = jest.spyOn(Logger.prototype, 'error').mockImplementation(() => undefined);
  return () => [a, b, c].forEach((s) => s.mockRestore());
}

describe('LoyaltyInactivityJob (§14.D)', () => {
  it('jatuh tempo kalender: 00:00 WIB hari setelah jangkar + N bulan (akhir bulan dipotong)', () => {
    expect(calendarExpiry(new Date('2026-11-01T03:00:00Z'), 12).toISOString()).toBe('2027-11-01T17:00:00.000Z');
    expect(calendarExpiry(new Date('2026-01-31T03:00:00Z'), 1).toISOString()).toBe('2026-02-28T17:00:00.000Z');
  });

  it('notice: member bersaldo yang jatuh tempo ≤ 30 hari → notice + outbox notify.push v2 (params HANYA expire_date, L-76); idempoten per jangkar', async () => {
    const ctx = setup();
    ctx.store.activity.set(H1, new Date('2026-11-20T03:00:00Z')); // jatuh tempo 21 Nov 2027 → dalam 30 hari
    ctx.store.lotsBy.set(H1, [{ expiresAt: null, remaining: 120, issuer: 'GL' }]);
    ctx.store.activity.set(H2, new Date('2027-06-01T03:00:00Z')); // masih jauh
    ctx.store.lotsBy.set(H2, [{ expiresAt: null, remaining: 50, issuer: 'PB' }]);
    ctx.store.consented.add(H1); // SEC-171: consent marketing aktif
    const restore = quietLogs();
    try {
      expect(await ctx.job.noticeOnce()).toEqual({ queued: 1, dry_run: false, no_consent: 0 });
      expect(await ctx.job.noticeOnce()).toEqual({ queued: 0, dry_run: false, no_consent: 0 });
    } finally {
      restore();
    }
    const msg = ctx.store.outbox[0];
    expect(msg.queue).toBe('notify.push');
    expect(msg.payload).toMatchObject({ schema_version: 2, member_hash: H1, source: 'loyalty_expiry', template: 'loyalty_inactivity_notice', params: { expire_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/) } });
    expect(Object.keys(msg.payload.params as object)).toEqual(['expire_date']);
    const notice = [...ctx.store.notices.values()][0];
    // due ≥ notified + 30 hari (setiap member mendapat ≥ 30 hari pemberitahuan)
    expect(notice.due_at.getTime()).toBeGreaterThanOrEqual(ctx.clock.ms + 30 * DAY - DAY);
    expect(ctx.store.locks).toContain(EXPIRY_LOCK.NOTICE);
  });

  it('expire: entri per lot bersisa > 0 (lot utang tidak disentuh), notice expired, metrik per penerbit; jangkar bergeser → superseded; blocker → lewati', async () => {
    const ctx = setup();
    ctx.store.activity.set(H1, new Date('2026-11-20T03:00:00Z'));
    ctx.store.lotsBy.set(H1, [
      { expiresAt: null, remaining: 100, issuer: 'GL' },
      { expiresAt: null, remaining: 20, issuer: 'PB' },
      { expiresAt: null, remaining: -5, issuer: 'EFN' },
    ]);
    ctx.store.activity.set(H2, new Date('2026-11-20T03:00:00Z'));
    ctx.store.lotsBy.set(H2, [{ expiresAt: null, remaining: 10, issuer: 'GL' }]);
    const restore = quietLogs();
    try {
      await ctx.job.noticeOnce();
      ctx.clock.ms += 40 * DAY;
      ctx.store.activity.set(H2, new Date(ctx.clock.ms - DAY)); // H2 aktif lagi → superseded
      const res = await ctx.job.expireOnce();
      expect(res).toMatchObject({ expired: 1, superseded: 1, skipped: 0, points_by_issuer: { GL: 100, PB: 20 } });
    } finally {
      restore();
    }
    expect(ctx.store.entries.map((e) => [e.issuer, e.points])).toEqual([['GL', 100], ['PB', 20]]);
    expect(await ctx.metrics.render()).toMatch(/efn_loyalty_expiry_total\{phase="expire",outcome="expired"\} 1/);
    expect(await ctx.metrics.render()).toMatch(/efn_loyalty_expired_points_total\{issuer="GL"\} 100/);
    expect(ctx.store.retention).toEqual([]); // anonimisasi OFF default (L-73)
  });

  it('pengaman massal: kandidat > LOYALTY_EXPIRY_MAX_PER_RUN → aborted (tanpa entri) + heartbeat; dry run hanya menghitung', async () => {
    const ctx = setup({ LOYALTY_EXPIRY_MAX_PER_RUN: '1' });
    for (const h of [H1, H2]) {
      ctx.store.notices.set(h, { id: h, member_hash: h, anchor_at: new Date(0), due_at: new Date(ctx.clock.ms - DAY), status: 'queued' });
      ctx.store.lotsBy.set(h, [{ expiresAt: null, remaining: 10, issuer: 'GL' }]);
    }
    const err = jest.spyOn(Logger.prototype, 'error').mockImplementation(() => undefined);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await ctx.job.expireOnce()).toBeNull();
      expect(err.mock.calls.some((c) => String(c[0]).startsWith('EFN_LOYALTY_EXPIRY_RUN {"status":"aborted","phase":"expire"'))).toBe(true);
    } finally {
      err.mockRestore();
      warn.mockRestore();
    }
    expect(ctx.store.entries).toEqual([]);
    const dry = setup({ LOYALTY_EXPIRY_DRY_RUN: 'true' });
    dry.store.notices.set(H1, { id: H1, member_hash: H1, anchor_at: new Date(0), due_at: new Date(dry.clock.ms - DAY), status: 'queued' });
    dry.store.lotsBy.set(H1, [{ expiresAt: null, remaining: 7, issuer: 'BCF' }]);
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    try {
      expect(await dry.job.expireOnce()).toMatchObject({ expired: 0, points_by_issuer: { BCF: 7 } });
      expect(log.mock.calls.some((c) => String(c[0]).startsWith('EFN_LOYALTY_EXPIRY_RUN {"status":"dry_run","phase":"expire"'))).toBe(true);
    } finally {
      log.mockRestore();
    }
    expect(dry.store.entries).toEqual([]);
  });

  it('anonimisasi aktif → dsar retention dijadwalkan; epoch tidak ada → error heartbeat; job OFF default', async () => {
    const ctx = setup({ LOYALTY_INACTIVE_ANONYMIZE: 'true' });
    ctx.store.activity.set(H1, new Date('2026-01-01T00:00:00Z'));
    ctx.store.notices.set(H1, { id: H1, member_hash: H1, anchor_at: new Date('2026-10-31T17:00:00Z'), due_at: new Date(ctx.clock.ms - DAY), status: 'queued' });
    ctx.store.lotsBy.set(H1, [{ expiresAt: null, remaining: 5, issuer: 'GL' }]);
    const restore = quietLogs();
    try {
      expect(await ctx.job.expireOnce()).toMatchObject({ expired: 1, anonymize: 1 });
      expect(ctx.store.retention).toEqual([H1]);
      const noEpoch = setup({ LOYALTY_ACTIVITY_EPOCH: '' });
      expect(await noEpoch.job.noticeOnce()).toBeNull();
    } finally {
      restore();
    }
    expect(testConfig().loyaltyExpiryEnabled).toBe(false);
    expect(testConfig().loyaltyExpiryDryRun).toBe(true);
    expect(() => testConfig({ LOYALTY_EXPIRY_ENABLED: 'true' }).assertStartupSecrets()).toThrow(/LOYALTY_ACTIVITY_EPOCH/);
  });

  it('SEC-171: default REQUIRE_CONSENT=true → tanpa consent: notice tetap (due ≥ 30 hari), TANPA WA, bukti anonim in_app; expire tetap jalan', async () => {
    const ctx = setup();
    expect(ctx.config.loyaltyExpiryNoticeRequireConsent).toBe(true);
    ctx.store.activity.set(H1, new Date('2026-11-20T03:00:00Z'));
    ctx.store.lotsBy.set(H1, [{ expiresAt: null, remaining: 120, issuer: 'GL' }]);
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    try {
      expect(await ctx.job.noticeOnce()).toEqual({ queued: 0, dry_run: false, no_consent: 1 });
      expect(log.mock.calls.some((c) => String(c[0]).includes('"no_consent":1') && String(c[0]).includes('"require_consent":true'))).toBe(true);
    } finally {
      log.mockRestore();
    }
    expect(ctx.store.outbox).toEqual([]);
    const notice = [...ctx.store.notices.values()][0];
    expect(notice.status).toBe('queued');
    expect(notice.due_at.getTime()).toBeGreaterThanOrEqual(ctx.clock.ms + 30 * DAY - DAY);
    expect(ctx.store.proofs).toEqual([{ noticeId: notice.id, expireDate: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/), channel: 'in_app' }]);
    expect(JSON.stringify(ctx.store.proofs)).not.toContain(H1); // tanpa identitas
    expect(await ctx.metrics.render()).toMatch(/efn_loyalty_expiry_total\{phase="notice",outcome="no_consent"\} 1/);
  });

  it('SEC-171: REQUIRE_CONSENT=false → WA tanpa cek consent; nilai salah ketik → ConfigError (tidak diam-diam mati)', async () => {
    const ctx = setup({ LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT: 'false' });
    ctx.store.activity.set(H1, new Date('2026-11-20T03:00:00Z'));
    ctx.store.lotsBy.set(H1, [{ expiresAt: null, remaining: 1, issuer: 'GL' }]);
    const restore = quietLogs();
    try {
      expect(await ctx.job.noticeOnce()).toEqual({ queued: 1, dry_run: false, no_consent: 0 });
    } finally {
      restore();
    }
    expect(ctx.store.outbox).toHaveLength(1);
    expect(() => testConfig({ LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT: 'flase' }).loyaltyExpiryNoticeRequireConsent).toThrow();
    expect(() => testConfig({ LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT: 'flase' }).assertStartupSecrets()).toThrow(/LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT/);
  });

  it('GET /v1/me points_expiry: expires_on hanya ≤ 60 hari & saldo > 0', async () => {
    const ctx = setup();
    ctx.store.activity.set(H1, new Date('2026-11-20T03:00:00Z'));
    // hangus 00:00 WIB 21 Nov 2027 (hari setelah ulang 12 bulan)
    expect(await ctx.job.expiryInfo(H1, 100)).toMatchObject({ inactive_since: '2026-11-20', expires_on: '2027-11-21', notice_sent: false });
    expect((await ctx.job.expiryInfo(H1, 0)).expires_on).toBeNull();
    ctx.store.activity.set(H2, new Date('2027-10-01T03:00:00Z'));
    expect((await ctx.job.expiryInfo(H2, 100)).expires_on).toBeNull();
  });
});
