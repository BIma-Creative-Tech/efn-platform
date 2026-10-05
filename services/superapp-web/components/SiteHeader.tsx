import Link from 'next/link';
import type { Brand } from '@/lib/brands';
import { getSessionToken } from '@/lib/session';

/** Header bersama semua brand; perbedaan visual hanya dari CSS custom properties. */
export async function SiteHeader({ brand }: { brand: Brand }) {
  const loggedIn = Boolean(await getSessionToken());
  return (
    <header className="site-header">
      <div className="container">
        <Link href="/" className="brand-logo" aria-label={`${brand.name} — beranda`}>
          {brand.logoText}
        </Link>
        <nav className="site-nav" aria-label="Navigasi utama">
          <ul>
            <li>
              <Link href="/">Beranda</Link>
            </li>
            <li>
              <Link href="/pantau-harga">Pantau Harga</Link>
            </li>
            <li>{loggedIn ? <Link href="/akun">Akun</Link> : <Link href="/masuk">Masuk</Link>}</li>
          </ul>
        </nav>
      </div>
    </header>
  );
}
