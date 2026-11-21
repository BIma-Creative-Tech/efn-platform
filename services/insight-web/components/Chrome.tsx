'use client';
/**
 * Bagian interaktif cangkang aplikasi: navigasi (aria-current, filter dibawa antar halaman), pemilih tema,
 * tombol menu mobile (drawer), dan pemindah fokus ke <main> setelah navigasi halaman (spec §3.1).
 */
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { DEFAULT_NAV_PREFS, NAV_PREFS_KEY, parseNavPrefs, serializeNavPrefs, toggleClosed, type NavPrefs } from '@/lib/nav-state';
import type { NavGroup } from '@/lib/sm-personas';
import { Icon } from './ui';

const PAGE_ONLY_PARAMS = ['sort', 'rank'];
/** Param filter ESB yang tidak berlaku di halaman supermarket (dan sebaliknya) tidak dibawa antar kelompok. Tanggal (from/to) dibawa ke SEMUA halaman. */
const ESB_ONLY_PARAMS = ['pt', 'branch', 'product', 'cat'];
const SM_ONLY_PARAMS = ['wh', 'cat'];

/**
 * Sidebar tiga grup yang bisa dilipat + bisa diciutkan jadi rail ikon. Preferensi di localStorage (try/catch). Menu
 * yang diciutkan tetap punya nama aksesibel (teks disembunyikan secara visual) dan tooltip (title).
 */
export function NavLinks({ groups }: { groups: readonly NavGroup[] }): React.ReactNode {
  const pathname = usePathname();
  const sp = useSearchParams();
  const [prefs, setPrefs] = useState<NavPrefs>(DEFAULT_NAV_PREFS);

  useEffect(() => {
    // Nilai tersimpan hanya diketahui di klien; setState lewat callback (bukan sinkron di badan efek).
    const id = window.setTimeout(() => {
      try {
        setPrefs(parseNavPrefs(localStorage.getItem(NAV_PREFS_KEY)));
      } catch {
        /* abaikan */
      }
    }, 0);
    return () => window.clearTimeout(id);
  }, []);

  useEffect(() => {
    document.querySelector('.shell')?.toggleAttribute('data-collapsed', prefs.collapsed);
  }, [prefs.collapsed]);

  const save = (next: NavPrefs): void => {
    setPrefs(next);
    try {
      localStorage.setItem(NAV_PREFS_KEY, serializeNavPrefs(next));
    } catch {
      /* abaikan */
    }
  };

  const carry = new URLSearchParams(sp.toString());
  for (const k of PAGE_ONLY_PARAMS) carry.delete(k);
  const smCarry = new URLSearchParams(carry.toString());
  for (const k of ESB_ONLY_PARAMS) smCarry.delete(k);
  const esbCarry = new URLSearchParams(carry.toString());
  for (const k of SM_ONLY_PARAMS) esbCarry.delete(k);
  const smPaths = ['/store', '/dc', '/category', '/marketing', '/exec'];
  return (
    <>
      <div className="nav-tools">
        <button
          type="button"
          className="nav-collapse"
          aria-expanded={!prefs.collapsed}
          aria-controls="nav"
          title={prefs.collapsed ? 'Lebarkan menu' : 'Ciutkan menu'}
          onClick={() => save({ ...prefs, collapsed: !prefs.collapsed })}
        >
          <Icon name={prefs.collapsed ? 'chevron-right' : 'chevron-left'} />
          <span>{prefs.collapsed ? 'Lebarkan menu' : 'Ciutkan menu'}</span>
        </button>
      </div>
      <nav id="nav" aria-label="Navigasi utama" className="nav">
        {groups.map((g) => {
          const open = !prefs.closed.includes(g.id);
          return (
            <div key={g.id} className="nav-group">
              <button
                type="button"
                className="nav-group-btn"
                aria-expanded={open}
                aria-controls={`nav-g-${g.id}`}
                onClick={() => save({ ...prefs, closed: toggleClosed(prefs.closed, g.id) })}
              >
                <span>{g.title}</span>
                <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} />
              </button>
              <ul id={`nav-g-${g.id}`} hidden={!open}>
                {g.items.map((it) => {
                  const active = pathname === it.href;
                  const qs = (smPaths.includes(it.href) ? smCarry : esbCarry).toString();
                  return (
                    <li key={it.href}>
                      <Link href={`${it.href}${qs ? `?${qs}` : ''}`} aria-current={active ? 'page' : undefined} className="nav-link" title={it.label}>
                        <Icon name={it.icon} />
                        <span>{it.label}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </nav>
    </>
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
