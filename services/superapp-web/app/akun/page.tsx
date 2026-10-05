import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { bffFetch, type MeResponse } from '@/lib/bff';
import { formatNumber } from '@/lib/format';
import { getSessionToken } from '@/lib/session';

export const metadata: Metadata = { title: 'Akun' };
export const dynamic = 'force-dynamic';

/** Server Component: baca cookie efn_session, panggil BFF GET /v1/me (docs/04 §3.2). */
export default async function AkunPage() {
  const token = await getSessionToken();
  if (!token) redirect('/masuk?next=/akun');

  const me = await bffFetch<MeResponse>('/v1/me', { token });
  if (!me.ok && me.status === 401) redirect('/masuk?next=/akun&expired=1');

  return (
    <div className="stack" style={{ maxWidth: '36rem', margin: '0 auto' }}>
      <h1>Akun Saya</h1>
      {me.ok ? (
        <>
          <section className="card stack" aria-labelledby="poin-judul">
            <h2 id="poin-judul" style={{ margin: 0 }}>
              Poin Anda
            </h2>
            <p className="points" style={{ margin: 0 }}>
              {formatNumber(me.data.points_balance ?? 0)}
            </p>
            <p className="small muted" style={{ margin: 0 }}>
              Poin berlaku di semua brand EFN. Dapatkan poin tambahan lewat{' '}
              <Link href="/pantau-harga">Pantau Harga</Link>.
            </p>
          </section>
          <section className="card stack">
            <h2 style={{ margin: 0 }}>Profil</h2>
            <p style={{ margin: 0 }}>
              Nomor HP: <span>{me.data.member_ref_masked}</span>
            </p>
          </section>
        </>
      ) : (
        <p className="alert alert-error" role="alert">
          Data akun belum dapat dimuat. Silakan muat ulang halaman beberapa saat lagi.
        </p>
      )}
      <section className="card stack" aria-labelledby="privasi-judul">
        <h2 id="privasi-judul" style={{ margin: 0 }}>
          Privasi &amp; data
        </h2>
        <ul style={{ margin: 0 }}>
          <li>
            <Link href="/akun/privasi">Pengaturan Privasi</Link> — persetujuan lokasi dan info promo.
          </li>
          <li>
            <Link href="/akun/data-saya">Data Saya</Link> — unduh data, hapus akun, riwayat permintaan.
          </li>
        </ul>
      </section>
      <p>
        <Link href="/keluar" className="btn btn-secondary">
          Keluar
        </Link>
      </p>
    </div>
  );
}
