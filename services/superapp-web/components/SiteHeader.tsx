import Link from 'next/link';
import { brandHasTableService } from '@/lib/booking';
import { safeLogoSrc, type Brand } from '@/lib/brands';
import { getSessionToken } from '@/lib/session';
import { NavLink } from './NavLink';

/**
 * Header bersama semua brand; perbedaan visual hanya dari CSS custom properties (R-T14).
 * Logo: file resmi same-origin bila `brand.logoSrc` diisi (public/brands/<id>/, svg/png), selain itu logo teks bergaya.
 * `logoKind: 'mark'` (lencana/monogram, mis. PB) tampil bersama nama brand; `wordmark` tampil sendiri.
 */
export async function SiteHeader({ brand }: { brand: Brand }) {
  const loggedIn = Boolean(await getSessionToken());
  const logoSrc = safeLogoSrc(brand);
  return (
    <header className="site-header">
      <div className="container">
        <Link href="/" className="brand-logo" aria-label={`${brand.name} — beranda`}>
          {logoSrc ? (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element -- logo statis same-origin, tanpa optimizer */}
              <img src={logoSrc} alt="" width={brand.logoKind === 'mark' ? 40 : 160} height={brand.logoKind === 'mark' ? 40 : 32} data-kind={brand.logoKind} />
              {brand.logoKind === 'mark' && <span className="brand-logo-text">{brand.logoText}</span>}
            </>
          ) : (
            brand.logoText
          )}
        </Link>
        <nav className="site-nav" aria-label="Navigasi utama">
          <ul>
            <li>
              <NavLink href="/">Beranda</NavLink>
            </li>
            <li>
              <NavLink href="/belanja">Belanja</NavLink>
            </li>
            {brandHasTableService(brand) && (
              <li>
                <NavLink href="/reservasi">Reservasi</NavLink>
              </li>
            )}
            <li>
              <NavLink href="/keranjang">Keranjang</NavLink>
            </li>
            {loggedIn && (
              <li>
                <NavLink href="/pesanan">Pesanan</NavLink>
              </li>
            )}
            <li>
              <NavLink href="/pantau-harga">Pantau Harga</NavLink>
            </li>
            <li>{loggedIn ? <NavLink href="/akun">Akun</NavLink> : <NavLink href="/masuk">Masuk</NavLink>}</li>
          </ul>
        </nav>
      </div>
    </header>
  );
}
