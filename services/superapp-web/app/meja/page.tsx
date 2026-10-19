import type { Metadata } from 'next';
import { TableOrder } from '@/components/fnb/TableOrder';
import { getSessionToken } from '@/lib/session';

export const metadata: Metadata = {
  title: 'Pesan dari meja',
  // Halaman sesi pribadi: jangan diindeks, jangan kirim Referer ke tujuan lain (token ada di fragment, bukan di Referer,
  // tetapi tidak ada alasan membocorkan asal halaman).
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
};
export const dynamic = 'force-dynamic';

/**
 * Pesan dari meja (docs/04 §14.A; ADR-032). QR berisi `/meja#t=<token>`; fragment tidak pernah sampai ke server, jadi halaman
 * ini dirender tanpa data sesi. Seluruh logika (token → memori, menu, kirim, status, klaim poin) ada di client component.
 * Tamu tanpa login boleh memesan; `loggedIn` hanya menentukan tampilan klaim poin.
 */
export default async function MejaPage() {
  const loggedIn = Boolean(await getSessionToken());
  return <TableOrder loggedIn={loggedIn} />;
}
