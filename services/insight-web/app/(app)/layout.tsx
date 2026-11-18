import type { Metadata } from 'next';
import { FreshnessBadge } from '@/components/FreshnessBadge';
import { FilterBar } from '@/components/FilterBar';
import { FocusMain, NavLinks, NavToggle, ThemeSelect } from '@/components/Chrome';
import { Icon } from '@/components/ui';
import { requireSession } from '@/lib/auth';
import { odooOpenUrl } from '@/lib/config';
import { fmtAge } from '@/lib/format';
import { getConfig } from '@/lib/runtime';
import { loadShellData } from '@/lib/shell-data';
import { freshnessStatus } from '@/lib/thresholds';
import { codeName } from '@/lib/brands';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = { title: { default: 'EFN Insight', template: '%s - EFN Insight' } };

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();
  const shell = await loadShellData(session);
  const odoo = odooOpenUrl(getConfig());
  const status = freshnessStatus(shell.ageHours);
  const roleLabel = session.roles[0] === 'all_brands' ? 'Semua brand' : session.codes.map(codeName).join(', ');

  return (
    <>
      <a className="skip-link" href="#konten">
        Lompat ke konten
      </a>
      <div id="live-region" className="sr-only" aria-live="polite" role="status" />
      <div className="shell">
        <aside className="sidebar">
          <div className="brand-mark">
            <span className="brand-name">EFN</span>
            <span className="brand-sub">Insight</span>
          </div>
          <NavLinks />
          <div className="sidebar-foot">
            {odoo ? (
              <a href={odoo} className="nav-link">
                <Icon name="home" />
                <span>Kembali ke Odoo</span>
              </a>
            ) : null}
            <span className="version">Data: ESB Core (arsip G41)</span>
          </div>
        </aside>
        <div className="main-col">
          <header className="topbar">
            <div className="topbar-row">
              <NavToggle />
              <div className="topbar-spacer" />
              <FreshnessBadge ageHours={shell.ageHours} rows={shell.freshness} />
              <ThemeSelect />
              <div className="user" title={session.name}>
                <span className="user-name">{session.name}</span>
                <span className="user-role">{roleLabel}</span>
              </div>
              <form method="post" action="/logout">
                <button type="submit" className="btn">
                  Keluar
                </button>
              </form>
            </div>
            <FilterBar options={shell.options} sessionCodes={session.codes} defaults={shell.defaults} yesterday={shell.yesterday} />
          </header>
          {status === 'warn' || status === 'bad' || status === 'unknown' ? (
            <div className={`banner banner-${status}`} role="status">
              {shell.ageHours === null
                ? 'Kesegaran data tidak diketahui. Angka mungkin belum mencakup kemarin.'
                : `Data terakhir diperbarui ${fmtAge(shell.ageHours)}. Angka mungkin belum mencakup kemarin.`}
            </div>
          ) : null}
          <main id="konten" tabIndex={-1}>
            {children}
          </main>
          <FocusMain />
        </div>
      </div>
    </>
  );
}
