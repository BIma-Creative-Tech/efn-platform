import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { bffFetch, type MeResponse } from '@/lib/bff';
import { formatNumber } from '@/lib/format';
import { currentExpiryNotice, parsePointsExpiry } from '@/lib/points';
import { getSessionToken } from '@/lib/session';

export const metadata: Metadata = { title: 'Akun' };
export const dynamic = 'force-dynamic';

/** Server Component: baca cookie efn_session, panggil BFF GET /v1/me (docs/04 §3.2). */
export default async function AkunPage() {
  const token = await getSessionToken();
  if (!token) redirect('/masuk?next=/akun');

  const me = await bffFetch<MeResponse>('/v1/me', { token });
  if (!me.ok && me.status === 401) redirect('/masuk?next=/akun&expired=1');

  // G22 (§14.D.6): info poin hangus hanya bila BFF mengisi `expires_on` (≤ 60 hari) dan saldo > 0. Teks DRAF (menunggu Legal).
  const balance = me.ok ? (me.data.points_balance ?? 0) : 0;
  const reserved = me.ok && typeof me.data.points_reserved === 'number' && me.data.points_reserved > 0 ? me.data.points_reserved : 0;
  const expiry = me.ok ? currentExpiryNotice(parsePointsExpiry(me.data.points_expiry), balance) : null;

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
            {reserved > 0 && (
              <p className="small" style={{ margin: 0 }}>
                {formatNumber(reserved)} poin sedang ditahan untuk pesanan online yang belum dibayar. Poin kembali bila pesanan
                kedaluwarsa atau dibatalkan.
              </p>
            )}
            {expiry && (
              <div className={expiry.urgent ? 'alert alert-error' : 'notice'} role="note" aria-labelledby="hangus-judul">
                <p id="hangus-judul" style={{ margin: 0, fontWeight: 700 }}>
                  Poin akan hangus
                </p>
                <p style={{ margin: '0.25rem 0 0' }}>{expiry.text}</p>
                <p className="small" style={{ margin: '0.25rem 0 0' }}>
                  Tanggal hangus: <strong>{expiry.dateLabel}</strong> · Jumlah: <strong>{formatNumber(expiry.points)} poin</strong>
                </p>
              </div>
            )}
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
