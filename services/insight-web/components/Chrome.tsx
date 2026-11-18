'use client';
/**
 * Bagian interaktif cangkang aplikasi: navigasi (aria-current, filter dibawa antar halaman), pemilih tema,
 * tombol menu mobile (drawer), dan pemindah fokus ke <main> setelah navigasi halaman (spec §3.1).
 */
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { NAV_ITEMS } from '@/lib/page-filters';
import { Icon } from './ui';

const PAGE_ONLY_PARAMS = ['sort', 'rank'];

export function NavLinks(): React.ReactNode {
  const pathname = usePathname();
  const sp = useSearchParams();
  const carry = new URLSearchParams(sp.toString());
  for (const k of PAGE_ONLY_PARAMS) carry.delete(k);
  const qs = carry.toString();
  return (
    <nav id="nav" aria-label="Navigasi utama" className="nav">
      <ul>
        {NAV_ITEMS.map((it) => {
          const active = pathname === it.href;
          return (
            <li key={it.href}>
              <Link href={`${it.href}${qs ? `?${qs}` : ''}`} aria-current={active ? 'page' : undefined} className="nav-link">
                <Icon name={it.icon} />
                <span>{it.label}</span>
                {it.soon ? <span className="soon">segera</span> : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

type Theme = 'system' | 'light' | 'dark';

export function ThemeSelect(): React.ReactNode {
  const [theme, setTheme] = useState<Theme>('system');
  useEffect(() => {
    // setState lewat callback (bukan sinkron di badan efek): nilai tersimpan hanya diketahui di klien.
    const id = window.setTimeout(() => {
      try {
        const t = localStorage.getItem('insight-theme');
        if (t === 'light' || t === 'dark') setTheme(t);
      } catch {
        /* abaikan */
      }
    }, 0);
    return () => window.clearTimeout(id);
  }, []);
  const change = (t: Theme): void => {
    setTheme(t);
    try {
      if (t === 'system') {
        localStorage.removeItem('insight-theme');
        document.documentElement.removeAttribute('data-theme');
      } else {
        localStorage.setItem('insight-theme', t);
        document.documentElement.setAttribute('data-theme', t);
      }
    } catch {
      /* abaikan */
    }
  };
  return (
    <div className="theme">
      <label htmlFor="theme-select">Tema</label>
      <select id="theme-select" className="input" value={theme} onChange={(e) => change(e.target.value as Theme)}>
        <option value="system">Ikuti sistem</option>
        <option value="light">Terang</option>
        <option value="dark">Gelap</option>
      </select>
    </div>
  );
}

export function NavToggle(): React.ReactNode {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    // Tutup drawer setelah navigasi.
    const id = window.setTimeout(() => setOpen(false), 0);
    return () => window.clearTimeout(id);
  }, [pathname]);

  useEffect(() => {
    const shell = document.querySelector('.shell');
    if (shell) shell.toggleAttribute('data-nav-open', open);
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <button type="button" className="btn nav-toggle" aria-expanded={open} aria-controls="nav" onClick={() => setOpen((o) => !o)}>
      <Icon name={open ? 'close' : 'menu'} />
      <span>{open ? 'Tutup menu' : 'Menu'}</span>
    </button>
  );
}

/** Pindahkan fokus ke <main id="konten"> setelah pindah halaman (bukan saat render pertama). */
export function FocusMain(): React.ReactNode {
  const pathname = usePathname();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    document.getElementById('konten')?.focus();
  }, [pathname]);
  return null;
}
