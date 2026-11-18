import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import './globals.css';
import type { Metadata, Viewport } from 'next';
import { headers } from 'next/headers';

export const metadata: Metadata = {
  title: { default: 'EFN Insight', template: '%s - EFN Insight' },
  description: 'Dashboard staf EFN (hanya baca).',
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  colorScheme: 'light dark',
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Nonce dari proxy.ts (CSP berbasis nonce + strict-dynamic: skrip luar juga butuh nonce).
  const nonce = (await headers()).get('x-nonce') ?? undefined;
  return (
    <html lang="id" suppressHydrationWarning>
      <head>
        {/* Sengaja sinkron: tema harus terpasang sebelum paint pertama (tanpa kedip terang/gelap); berkas kecil,
            same-origin, ber-nonce CSP. */}
        {/* eslint-disable-next-line @next/next/no-sync-scripts */}
        <script src="/theme-init.js" nonce={nonce} suppressHydrationWarning />
      </head>
      <body>{children}</body>
    </html>
  );
}
