import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { FnbMetrics } from './fnb-metrics';
import { FNB_REPO, FnbRepo } from './fnb.repository';
import { FnbService } from './fnb.service';
import { ODOO_QR, OdooQrApi, OdooQrError } from './odoo-qr';

export const FNB_LOCK = { MENU: 0x45464e51, SWEEP: 0x45464e53 } as const; // 'EFNQ', 'EFNS'
const PAGE = 200;
const MAX_PAGES = 50;

/**
 * Job QR meja (docs/04 §14.A.6–A.8, L-76), satu replika per siklus (advisory lock xact):
 * - menu (FNB_MENU_SYNC_SECONDS, 300 s): config dengan sesi terbuka / `efn_qr_enabled` → `efn_qr_menu(since, cursor)` →
 *   overlay `fnb_menu_item` (+ config: outlet, qr_enabled, confirm_mode). Gagal → `last_error_code`, menu lama tetap.
 * - sweeper (FNB_JOB_INTERVAL_MS, 60 s): sesi lewat `expires_at` → `expired`; kiriman `submitting` > 1 menit →
 *   rekonsiliasi `efn_qr_status`; gauge alert L-76; retensi (tautan member 30 hari, baris 180 hari).
 * Heartbeat `EFN_FNB_JOB {"status":…}` tanpa PII.
 */
@Injectable()
export class FnbJobsService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger('FnbJobs');
  private readonly timers: NodeJS.Timeout[] = [];
  private readonly running = new Set<string>();
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(FNB_REPO) private readonly repo: FnbRepo,
    @Inject(ODOO_QR) private readonly odoo: OdooQrApi,
    private readonly fnb: FnbService,
    private readonly metrics: FnbMetrics,
  ) {}

  onModuleInit(): void {
    if (!this.config.fnbQrEnabled) return;
    const every = (ms: number, name: string, fn: () => Promise<unknown>): void => {
      if (ms <= 0) return;
      const t = setInterval(() => void this.guarded(name, fn), ms);
      t.unref();
      this.timers.push(t);
    };
    every(this.config.fnbMenuSyncSeconds * 1000, 'menu', () => this.menuOnce());
    every(this.config.fnbJobIntervalMs, 'sweep', () => this.sweepOnce());
  }

  onModuleDestroy(): void {
    for (const t of this.timers) clearInterval(t);
    this.timers.length = 0;
  }

  private async guarded(name: string, fn: () => Promise<unknown>): Promise<void> {
    if (this.running.has(name)) return;
    this.running.add(name);
    try {
      await fn();
    } catch (err) {
      this.logger.error(`EFN_FNB_JOB ${JSON.stringify({ status: 'error', job: name, error: err instanceof Error ? err.name : 'error' })}`);
    } finally {
      this.running.delete(name);
    }
  }

  async menuOnce(): Promise<{ synced: number; failed: number }> {
    const configs = await this.repo.configsToSync(this.config.fnbQrCompanies);
    let synced = 0;
    let failed = 0;
    for (const c of configs) {
      try {
        let since = c.since;
        let cursor = c.cursor;
        let nextSince: string | null = null;
        for (let page = 0; page < MAX_PAGES; page += 1) {
          const p = await this.odoo.menu(c.company_code, c.config_id, since, cursor, PAGE);
          if (p.config.config_id !== c.config_id || p.config.company_code !== c.company_code) {
            throw new OdooQrError('invalid_response', null, 'config/PT menu tidak cocok');
          }
          nextSince ??= p.server_time;
          await this.repo.tx(async (r) => {
            for (const item of p.items) await r.upsertMenuItem(c.config_id, c.company_code, item);
            await r.deleteMenuItems(c.config_id, p.deleted);
            await r.updateMenuSync(c.config_id, {
              outlet_name: p.config.outlet_name,
              qr_enabled: p.config.qr_enabled,
              confirm_mode: p.config.confirm_mode,
              since,
              cursor: p.next_cursor,
              ...(p.next_cursor ? {} : { ok: true }),
            });
          });
          cursor = p.next_cursor;
          if (!cursor) break;
          since = c.since;
        }
        await this.repo.updateMenuSync(c.config_id, { since: nextSince ?? c.since, cursor: null, ok: true, error: null });
        synced += 1;
      } catch (err) {
        failed += 1;
        const code = err instanceof OdooQrError ? `odoo_${err.kind}` : 'db_error';
        await this.repo.updateMenuSync(c.config_id, { error: code }).catch(() => undefined);
        this.logger.warn(`Sinkron menu QR config ${c.config_id} gagal: ${code}`);
      }
    }
    this.logger.log(`EFN_FNB_JOB ${JSON.stringify({ status: 'ok', job: 'menu', synced, failed })}`);
    return { synced, failed };
  }

  async sweepOnce(): Promise<Record<string, number>> {
    const now = new Date(this.now());
    const expired = await this.repo.tx(async (r) => ((await r.tryJobLock(FNB_LOCK.SWEEP)) ? r.expireDue(now, 500) : -1));
    if (expired < 0) return { skipped: 1 };
    let reconciled = 0;
    for (const { submission, session } of await this.repo.stuckSubmitting(new Date(now.getTime() - 60_000), 50)) {
      if ((await this.fnb.reconcile(session, submission)) !== 'unknown') reconciled += 1;
    }
    const g = await this.repo.gauges(now);
    this.metrics.gauges(g);
    const ret = await this.repo.tx((r) =>
      r.retention(now, { retentionDays: this.config.fnbSessionRetentionDays, memberLinkDays: this.config.fnbSessionMemberLinkDays }),
    );
    const out = { expired, reconciled, ...g, ...ret };
    this.logger.log(`EFN_FNB_JOB ${JSON.stringify({ status: 'ok', job: 'sweep', ...out })}`);
    return out;
  }
}
