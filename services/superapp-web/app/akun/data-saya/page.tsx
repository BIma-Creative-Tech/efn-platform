import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { bffFetch, type MeResponse } from '@/lib/bff';
import { parseDsarRequests, parseGraceDays } from '@/lib/dsar';
import { getSessionToken } from '@/lib/session';
import { DataSaya } from './DataSaya';

export const metadata: Metadata = { title: 'Data Saya' };
export const dynamic = 'force-dynamic';

/**
 * Hak subjek data (UU PDP) — docs/04 §8.4, docs/compliance/PDP_DSAR_DESIGN.md.
 * Server Component memuat saldo poin (GET /v1/me) dan riwayat permintaan (GET /v1/me/requests);
 * aksi (unduh, re-OTP, hapus, batal) lewat Route Handler `/api/me/*` dengan cookie HttpOnly.
 */
export default async function DataSayaPage() {
  const token = await getSessionToken();
  if (!token) redirect('/masuk?next=/akun/data-saya');

  const [me, reqs] = await Promise.all([
    bffFetch<MeResponse>('/v1/me', { token }),
    bffFetch<unknown>('/v1/me/requests', { token }),
  ]);
  if ((!me.ok && me.status === 401) || (!reqs.ok && reqs.status === 401)) {
    redirect('/masuk?next=/akun/data-saya&expired=1');
  }

  // Non-rahasia, perkiraan tampilan saja; tanggal pasti = scheduled_at dari BFF (DSAR_DELETE_GRACE_DAYS).
  const graceDays = parseGraceDays(process.env.DSAR_DELETE_GRACE_DAYS);

  return (
    <div className="stack" style={{ maxWidth: '40rem', margin: '0 auto' }}>
      <p>
        <Link href="/akun">&larr; Akun Saya</Link>
      </p>
      <h1>Data Saya</h1>
      <p className="muted">
        Anda berhak memperoleh salinan data pribadi Anda dan meminta penghapusan akun sesuai UU No. 27 Tahun 2022
        tentang Pelindungan Data Pribadi. Persetujuan lokasi dan promo diatur di{' '}
        <Link href="/akun/privasi">Pengaturan Privasi</Link>.
      </p>
      <DataSaya
        pointsBalance={me.ok ? (me.data.points_balance ?? 0) : null}
        requests={reqs.ok ? parseDsarRequests(reqs.data) : null}
        graceDays={graceDays}
      />
    </div>
  );
}
