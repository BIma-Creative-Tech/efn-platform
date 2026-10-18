import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { bffFetch } from '@/lib/bff';
import { DEFER_DEADLINE_PARAM, isCancellableDeletion, isoDateOrNull, parseDsarRequests } from '@/lib/dsar';
import { isDsarRequestId } from '@/lib/dsar-receipt';
import { getSessionToken } from '@/lib/session';
import { DeletionStatus } from './DeletionStatus';

export const metadata: Metadata = { title: 'Status Penghapusan Akun', robots: { index: false, follow: false } };
export const dynamic = 'force-dynamic';

/**
 * R6 (docs/04 §8.13): status permintaan hapus akun yang tetap bisa dibuka TANPA sesi (setelah DELETE sesi dicabut,
 * setelah selesai akun hilang). Status diambil di browser lewat `/api/dsar/requests/:id` karena cookie bukti
 * `efn_dsar_receipt` hanya dikirim ke Path `/api/dsar` (tidak terbaca oleh halaman ini).
 *
 * Tombol batalkan hanya muncul bila ada sesi valid YANG MEMILIKI permintaan ini dalam masa tenggang (dicek server lewat
 * `GET /v1/me/requests`) — mencegah sesi akun lain di perangkat yang sama membatalkan permintaan miliknya sendiri
 * secara tidak sengaja dari halaman yang salah.
 */
export default async function DeletionStatusPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id: rawId } = await params;
  if (!isDsarRequestId(rawId)) notFound();
  const id = rawId.toLowerCase();
  // G18 W-18 / L-22: batas erasure kini dikirim BFF di respons status (dibaca DeletionStatus). Query `?batas=` dari
  // respons 202 hanya CADANGAN untuk BFF lama (lib/dsar-receipt.ts resolveErasureDeadline). Nilai tidak valid diabaikan.
  const sp = await searchParams;
  const rawDeadline = sp[DEFER_DEADLINE_PARAM];
  const queryDeadline = isoDateOrNull(Array.isArray(rawDeadline) ? rawDeadline[0] : rawDeadline);

  let sessionCanCancel = false;
  let signedIn = false;
  let sessionDeadline: string | null = null;
  const token = await getSessionToken();
  if (token) {
    const reqs = await bffFetch<unknown>('/v1/me/requests', { token });
    if (reqs.ok) {
      signedIn = true;
      const mine = parseDsarRequests(reqs.data).find((r) => r.id.toLowerCase() === id);
      sessionCanCancel = Boolean(mine && isCancellableDeletion(mine));
      sessionDeadline = mine?.erasure_deadline ?? null;
    }
  }

  return (
    <div className="stack" style={{ maxWidth: '36rem', margin: '0 auto' }}>
      <h1>Status Penghapusan Akun</h1>
      <DeletionStatus id={id} sessionCanCancel={sessionCanCancel} signedIn={signedIn}
        sessionDeadline={sessionDeadline}
        queryDeadline={queryDeadline}
      />
      <p className="muted small">
        Halaman ini tidak menampilkan data pribadi. Bukti status disimpan aman di peramban ini selama 30 hari. Hak Anda
        menurut UU No. 27 Tahun 2022: lihat <Link href="/privasi">Kebijakan Privasi</Link>.
      </p>
    </div>
  );
}
