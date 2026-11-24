/**
 * Cangkang bersama (sidebar + topbar) untuk grup rute F&B (`(app)`) dan supermarket (`(sm)`). Tidak ada komponen per
 * persona: perbedaan = grup navigasi, chip peran, dan slot header (R-T14).
 */
import Link from 'next/link';
import type { ReactNode } from 'react';
import type { Session } from '@/lib/session';
import type { NavGroup } from '@/lib/sm-personas';
import { FocusMain, NavLinks, NavToggle, ThemeSelect } from './Chrome';
import { LoadingOverlay } from './LoadingOverlay';
import { Icon } from './ui';

export function Shell({
  session,
  groups,
  odooUrl,
  roleLabel,
  headerSlot,
  belowHeader,
  footNote,
  logoutAction = '/logout',
  standalone = false,
  children,
}: {
  /** Hanya `name` yang dipakai (modul Produksi memakai nama pengguna ESB, bukan sesi Odoo). */
  session: Pick<Session, 'name'>;
  groups: readonly NavGroup[];
  odooUrl: string | null;
  roleLabel: ReactNode;
  headerSlot?: ReactNode;
  belowHeader?: ReactNode;
  footNote: string;
  /** Tujuan form "Keluar" (modul Produksi: `/api/prodplan/logout`). */
  logoutAction?: string;
  /** true = tanpa tautan ke halaman Insight lain (Glosarium) dan ke Odoo: modul Produksi punya sesi sendiri (§30.17.2). */
  standalone?: boolean;
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
            {standalone ? null : (
              <Link href="/glosarium" className="nav-link" title="Glosarium istilah">
                <Icon name="book" />
                <span>Glosarium</span>
              </Link>
            )}
            {odooUrl && !standalone ? (
              <a href={odooUrl} className="nav-link" title="Kembali ke Odoo">
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
              <form method="post" action={logoutAction}>
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
      <LoadingOverlay />
    </>
  );
}
