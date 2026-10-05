import type { CSSProperties, ReactNode } from 'react';
import type { Metadata, Viewport } from 'next';
import Link from 'next/link';
import { getBrand } from '@/lib/brand-server';
import { themeToCssVars } from '@/lib/brands';
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
  return { themeColor: brand.theme.primary, width: 'device-width', initialScale: 1 };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const brand = await getBrand();
  const style = themeToCssVars(brand.theme) as CSSProperties;

  return (
    <html lang="id" data-brand={brand.id} style={style}>
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
              {brand.name} &middot; bagian dari grup EFN &middot; <Link href="/privasi">Kebijakan Privasi</Link>
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
