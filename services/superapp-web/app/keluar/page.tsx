import type { Metadata } from 'next';
import Link from 'next/link';
import { getSessionToken } from '@/lib/session';

export const metadata: Metadata = { title: 'Keluar' };

/**
 * Konfirmasi keluar. Penghapusan cookie lewat POST /api/auth/logout (bukan GET,
 * supaya link/prefetch tidak bisa mengeluarkan pengguna).
 */
export default async function KeluarPage() {
  const loggedIn = Boolean(await getSessionToken());
  return (
    <div className="card stack" style={{ maxWidth: '28rem', margin: '0 auto' }}>
      <h1 style={{ marginTop: 0 }}>Keluar</h1>
      {loggedIn ? (
        <>
          <p>Keluar dari akun di perangkat ini?</p>
          <form method="post" action="/api/auth/logout">
            <button type="submit" className="btn">
              Ya, keluar
            </button>{' '}
            <Link href="/akun" className="btn btn-secondary">
              Batal
            </Link>
          </form>
        </>
      ) : (
        <p>
          Anda tidak sedang masuk. <Link href="/masuk">Masuk</Link>
        </p>
      )}
    </div>
  );
}
