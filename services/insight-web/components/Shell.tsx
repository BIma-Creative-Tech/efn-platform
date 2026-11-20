/**
 * Cangkang bersama (sidebar + topbar) untuk grup rute F&B (`(app)`) dan supermarket (`(sm)`). Tidak ada komponen per
 * persona: perbedaan = grup navigasi, chip peran, dan slot header (R-T14).
 */
import type { ReactNode } from 'react';
import type { Session } from '@/lib/session';
import type { NavGroup } from '@/lib/sm-personas';
import { FocusMain, NavLinks, NavToggle, ThemeSelect } from './Chrome';
import { Icon } from './ui';

export function Shell({
  session,
  groups,
  odooUrl,
  roleLabel,
  headerSlot,
  belowHeader,
  footNote,
  children,
}: {
  session: Session;
  groups: readonly NavGroup[];
  odooUrl: string | null;
  roleLabel: ReactNode;
  headerSlot?: ReactNode;
  belowHeader?: ReactNode;
  footNote: string;
  children: ReactNode;
}): ReactNode {
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
          <NavLinks groups={groups} />
          <div className="sidebar-foot">
            {odooUrl ? (
              <a href={odooUrl} className="nav-link">
                <Icon name="home" />
                <span>Kembali ke Odoo</span>
              </a>
            ) : null}
            <span className="version">{footNote}</span>
          </div>
        </aside>
        <div className="main-col">
          <header className="topbar">
            <div className="topbar-row">
              <NavToggle />
              <div className="topbar-spacer" />
              {headerSlot}
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
            {belowHeader}
          </header>
          <main id="konten" tabIndex={-1}>
            {children}
          </main>
          <FocusMain />
        </div>
      </div>
    </>
  );
}
