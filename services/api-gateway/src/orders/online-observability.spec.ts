import { Logger } from '@nestjs/common';
import { MetricsService } from '../common/metrics/metrics.service';
import { FakeClock, randomSecret, testConfig } from '../common/testing/fakes';
import { ConfigError } from '../config/app-config';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { OnlineJobsService } from './online-jobs.service';
import { OnlineSagaService } from './online-saga.service';
import { FakeOnlinePayments, MemoryOnlineRepo } from './testing/online-fakes';

/** Selector promtail (infra/observability/promtail.yml tahap 6) — harus cocok persis dengan format NestJS (NO_COLOR). */
const RECON_SELECTOR =
  /^\[Nest\] \d+ {2}- \d{1,2}\/\d{1,2}\/\d{4}, \d{1,2}:\d{2}:\d{2} (AM|PM) +(LOG|ERROR) \[Online[A-Za-z]{0,40}\] EFN_ONLINE_RECON \{"status":"(ok|error)"[^\n]*$/;
const ANOMALY_SELECTOR =
  /^\[Nest\] \d+ {2}- \d{1,2}\/\d{1,2}\/\d{4}, \d{1,2}:\d{2}:\d{2} (AM|PM) +WARN \[OnlineAnomaly\] EFN_ONLINE_ANOMALY \{"kind":"[a-z_]{1,40}","severity":"(warning|critical)"[^\n]*\}\s*$/;

function nestLine(level: string, ctx: string, msg: string): string {
  return `[Nest] 19  - 10/9/2026, 2:40:03 PM ${level.padStart(7)} [${ctx}] ${msg}`;
}

function jobs() {
  const clock = new FakeClock();
  const repo = new MemoryOnlineRepo();
  repo.now = clock.now;
  const metrics = new OnlineMetrics(new MetricsService());
  const payments = new FakeOnlinePayments(repo);
  const odoo = new MockOdooOnline();
  const saga = new OnlineSagaService(repo, odoo, payments, metrics);
  const svc = new OnlineJobsService(testConfig(), repo, odoo, payments, saga, metrics);
  svc.now = clock.now;
  return { svc, metrics, repo };
}

describe('Observability online (§10.11)', () => {
  it('heartbeat EFN_ONLINE_RECON: logger [Online…], kunci pertama status ok|error, cocok selector promtail', async () => {
    const ctx = jobs();
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    const err = jest.spyOn(Logger.prototype, 'error').mockImplementation(() => undefined);
    try {
      await ctx.svc.reconOnce();
      await ctx.svc.statusOnce();
      ctx.svc.heartbeat('error', { job: 'status', error: 'TypeError' });
      const lines = [
        ...log.mock.calls.filter((c) => String(c[0]).startsWith('EFN_ONLINE_RECON')).map((c) => nestLine('LOG', 'OnlineJobs', String(c[0]))),
        ...err.mock.calls.filter((c) => String(c[0]).startsWith('EFN_ONLINE_RECON')).map((c) => nestLine('ERROR', 'OnlineJobs', String(c[0]))),
      ];
      expect(lines).toHaveLength(3);
      for (const l of lines) expect(l).toMatch(RECON_SELECTOR);
      expect(JSON.parse(lines[0].split('EFN_ONLINE_RECON ')[1])).toMatchObject({ status: 'ok', job: 'saga' });
      expect(JSON.parse(lines[1].split('EFN_ONLINE_RECON ')[1])).toEqual({ status: 'ok', job: 'status', applied: 0 });
      // nama logger (konteks) = OnlineJobs → [Online…]
      expect((ctx.svc as unknown as { logger: { context: string } }).logger.context).toBe('OnlineJobs');
    } finally {
      log.mockRestore();
      err.mockRestore();
    }
  });

  it('anomali EFN_ONLINE_ANOMALY cocok selector promtail; field di luar daftar aman dibuang', () => {
    const m = new OnlineMetrics(new MetricsService());
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      m.anomaly('refund_exceeds_portion', 'critical', { part_id: 'p-1', expected: 10, member_ref: '6281234567890', qr: 'x' } as Record<string, string | number>);
      const line = nestLine('WARN', 'OnlineAnomaly', String(warn.mock.calls[0][0]));
      expect(line).toMatch(ANOMALY_SELECTOR);
      expect(line).not.toContain('6281234567890');
    } finally {
      warn.mockRestore();
    }
  });

  it('gauge efn_online_refunds_open{status} & efn_online_stuck_parts diperbarui tiap rekonsiliasi', async () => {
    const ctx = jobs();
    ctx.repo.state.refunds.set('r1', {
      id: 'r1', part_id: 'p', payment_intent_id: 'i', kind: 'full', reason: 'stock_short', lines: null, amount: 1, odoo_refund_ref: null,
      provider_refund_ref: null, status: 'manual_required', failure_reason: null, initiated_by: null, claim_token: null, requested_by: 'system', created_at: new Date(0), updated_at: new Date(0), settled_at: null,
    });
    await ctx.svc.reconOnce();
    const open = await ctx.metrics.refundsOpen.get();
    expect(open.values.find((v) => v.labels.status === 'manual_required')?.value).toBe(1);
    expect(open.values.find((v) => v.labels.status === 'requested')?.value).toBe(0);
    const stuck = await ctx.metrics.stuck.get();
    expect(stuck.values.find((v) => v.labels.state === 'refund_attention')?.value).toBe(1);
  });

  it('API key kanal placeholder `disabled` (gen-secrets) ditolak saat kanal aktif; HMAC event wajib & ≠ service key', () => {
    const reader = (v: string) => (p: string) => {
      if (p === '/k') return Buffer.from(v);
      throw new Error('ENOENT');
    };
    expect(() => testConfig({ ODOO_API_KEY_ONLINE_RTL_FILE: '/k' }, reader('disabled\n')).odooApiKeyOnline('RTL')).toThrow(/placeholder 'disabled'/);
    const startup = testConfig({ ONLINE_ENABLED: 'true', ODOO_API_KEY_ONLINE_RTL_FILE: '/k', ODOO_ONLINE_EVENT_HMAC_FILE: '/k' }, reader('disabled'));
    expect(() => startup.assertStartupSecrets()).toThrow(ConfigError);
    const same = randomSecret();
    expect(() => testConfig({ ODOO_BFF_API_KEY: same, ODOO_ONLINE_EVENT_HMAC_FILE: '/k' }, reader(same)).odooOnlineEventHmac).toThrow(/berbeda/);
    // kanal mati (default) → secret online tidak diperlukan untuk start
    expect(() => testConfig().assertStartupSecrets()).not.toThrow();
  });
});
