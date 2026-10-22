import type { CSSProperties, ReactNode } from 'react';
import type { Metadata, Viewport } from 'next';
import Link from 'next/link';
// Font self-host same-origin (CSP `font-src 'self'`), OFL-1.1. Hanya dipakai brand yang menyebutnya di fontBody/fontHeading
// (saat ini PB); browser hanya mengunduh file bila family dipakai.
import '@fontsource-variable/fraunces/full.css';
import '@fontsource-variable/fraunces/wght-italic.css';
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import { getBrand } from '@/lib/brand-server';
import { safeScheme, themeToCssVars } from '@/lib/brands';
import { SiteHeader } from '@/components/SiteHeader';
import './globals.css';

export async function generateMetadata(): Promise<Metadata> {
  const brand = await getBrand();
  return {
    title: { default: brand.name, template: `%s | ${brand.name}` },
    description: brand.tagline,
    robots: { index: false, follow: false }, // Fase 0/PoC: jangan diindeks.
  };
}

export async function generateViewport(): Promise<Viewport> {
  const brand = await getBrand();
  return { themeColor: brand.theme.chrome, width: 'device-width', initialScale: 1 };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const brand = await getBrand();
  const style = themeToCssVars(brand.theme) as CSSProperties;

  return (
    <html lang="id" data-brand={brand.id} data-scheme={safeScheme(brand.theme)} style={style}>
      <body>
        <a className="skip-link" href="#konten">
          Lewati ke konten
        </a>
        <SiteHeader brand={brand} />
        <main id="konten" tabIndex={-1}>
          <div className="container">{children}</div>
        </main>
        <footer className="site-footer">
          <div className="container">
            <p>
              <strong>{brand.name}</strong>
              {brand.id === 'efn' ? '' : ' · bagian dari Erajaya Food & Nourishment (EFN)'}
            </p>
            <p>
              <Link href="/privasi">Kebijakan Privasi</Link>
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
