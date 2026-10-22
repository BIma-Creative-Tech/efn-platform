'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';

/**
 * Tautan navigasi utama dengan `aria-current="page"` untuk halaman aktif (UX-02; WCAG 2.4.8 / 1.3.1).
 * Satu komponen untuk semua brand — gaya aktif dari globals.css (R-T14).
 */
export function NavLink({ href, children }: { href: string; children: ReactNode }) {
  const pathname = usePathname() ?? '/';
  const active = href === '/' ? pathname === '/' : pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link href={href} aria-current={active ? 'page' : undefined}>
      {children}
    </Link>
  );
}
