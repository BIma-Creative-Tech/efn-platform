import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { bffFetch } from '@/lib/bff';
import { brandHasFnb, parseReservationList } from '@/lib/booking';
import { getBrand } from '@/lib/brand-server';
import { getSessionToken } from '@/lib/session';
import { ReservationList } from '@/components/booking/ReservationList';

export const metadata: Metadata = { title: 'Reservasi saya', robots: { index: false, follow: false } };
export const dynamic = 'force-dynamic';

/** Daftar reservasi milik member (docs/04 §15.5/§15.7). Belum masuk -> /masuk; kill switch -> pesan belum tersedia. */
export default async function ReservasiSayaPage() {
  const brand = await getBrand();
  if (!brandHasFnb(brand)) notFound();
  const token = await getSessionToken();
  if (!token) redirect('/masuk?next=/reservasi/saya');
  const r = await bffFetch<unknown>('/v1/booking/reservations', { token });
  if (!r.ok && r.status === 401) redirect('/masuk?next=/reservasi/saya&expired=1');
  const off = !r.ok && r.status === 503;
  const list = r.ok ? parseReservationList(r.data) : null;
  // Satu akun lintas brand: semua reservasi member tampil di domain mana pun (data sudah milik member di BFF).
  const items = list ?? [];
  return (
    <div className="stack" style={{ maxWidth: '46rem', margin: '0 auto', width: '100%' }}>
      <h1 style={{ margin: 0 }}>Reservasi saya</h1>
      {off ? (
        <p className="notice">Reservasi meja online belum tersedia. Silakan coba lagi nanti.</p>
      ) : !list ? (
        <p className="alert alert-error" role="alert">
          Daftar reservasi belum dapat dimuat. Silakan muat ulang.
        </p>
      ) : items.length === 0 ? (
        <section className="card stack">
          <p style={{ margin: 0 }}>Belum ada reservasi.</p>
          <p style={{ margin: 0 }}>
            <Link href="/reservasi" className="btn">
              Buat reservasi
            </Link>
          </p>
        </section>
      ) : (
        <>
          <ReservationList initial={items} />
          <p style={{ margin: 0 }}>
            <Link href="/reservasi">Buat reservasi baru</Link>
          </p>
        </>
      )}
    </div>
  );
}
