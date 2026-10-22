import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { brandHasFnb } from '@/lib/booking';
import { getBrand } from '@/lib/brand-server';
import { getSessionToken } from '@/lib/session';
import { BookingFlow } from '@/components/booking/BookingFlow';

export const metadata: Metadata = { title: 'Reservasi meja' };
export const dynamic = 'force-dynamic';

/**
 * Reservasi meja (docs/04 §15.7, G23 W-35). Hanya brand dengan PT F&B (Paris Baguette, Bacha, holding `app.`);
 * GrandLucky (ritel) -> 404. Seluruh alur di client component; ketersediaan & meja dihitung Odoo lewat BFF.
 */
export default async function ReservasiPage() {
  const brand = await getBrand();
  if (!brandHasFnb(brand)) notFound();
  const loggedIn = Boolean(await getSessionToken());
  return (
    <div className="stack" style={{ maxWidth: '46rem', margin: '0 auto', width: '100%' }}>
      <header className="stack">
        <h1 style={{ margin: 0 }}>Reservasi meja</h1>
        <p className="muted" style={{ margin: 0 }}>
          Pilih gerai, tanggal, dan jam. Meja disiapkan oleh gerai sesuai jumlah tamu.{' '}
          {loggedIn && <Link href="/reservasi/saya">Lihat reservasi saya</Link>}
        </p>
      </header>
      <BookingFlow loggedIn={loggedIn} brandName={brand.name} />
    </div>
  );
}
