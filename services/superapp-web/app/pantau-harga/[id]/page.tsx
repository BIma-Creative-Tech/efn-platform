import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { bffFetch, type ReportStatus, type ReportStatusResponse } from '@/lib/bff';
import { formatNumber } from '@/lib/format';
import { getSessionToken } from '@/lib/session';
import { AutoRefresh } from '@/components/AutoRefresh';

export const metadata: Metadata = { title: 'Status Laporan Harga' };
export const dynamic = 'force-dynamic';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const STATUS_TEXT: Record<ReportStatus, { label: string; detail: string }> = {
  queued: { label: 'Dalam antrean', detail: 'Laporan diterima dan menunggu diperiksa.' },
  processing: { label: 'Sedang divalidasi', detail: 'Foto sedang dibaca dan dicocokkan.' },
  valid: { label: 'Valid', detail: 'Terima kasih! Laporan Anda valid.' },
  rejected: { label: 'Ditolak', detail: 'Laporan tidak dapat diterima.' },
  flagged: { label: 'Perlu pemeriksaan manual', detail: 'Tim kami akan memeriksa laporan ini secara manual.' },
};

/** Next 15: params berupa Promise. Kontrak: GET /v1/market-scout/reports/:id (docs/04 §3.3). */
export default async function ReportStatusPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!UUID_RE.test(id)) notFound();

  const token = await getSessionToken();
  if (!token) redirect(`/masuk?next=${encodeURIComponent(`/pantau-harga/${id}`)}`);

  const result = await bffFetch<ReportStatusResponse>(`/v1/market-scout/reports/${encodeURIComponent(id)}`, { token });
  if (!result.ok) {
    if (result.status === 401) redirect(`/masuk?next=${encodeURIComponent(`/pantau-harga/${id}`)}&expired=1`);
    // 403 diperlakukan sama dengan 404 agar tidak membocorkan keberadaan laporan orang lain.
    if (result.status === 404 || result.status === 403) notFound();
    return (
      <div className="stack" style={{ maxWidth: '36rem', margin: '0 auto' }}>
        <h1>Status Laporan</h1>
        <p className="alert alert-error" role="alert">
          Status belum dapat dimuat. Silakan muat ulang beberapa saat lagi.
        </p>
      </div>
    );
  }

  const report = result.data;
  const text = STATUS_TEXT[report.status] ?? STATUS_TEXT.queued;
  const pending = report.status === 'queued' || report.status === 'processing';

  return (
    <div className="stack" style={{ maxWidth: '36rem', margin: '0 auto' }}>
      <h1>Status Laporan</h1>
      <section className="card stack" aria-live="polite">
        <p style={{ margin: 0 }}>
          <span className="badge">{text.label}</span>
        </p>
        <p style={{ margin: 0 }}>{text.detail}</p>
        {report.status === 'rejected' && report.reason && (
          <p style={{ margin: 0 }} className="muted">
            Alasan: {report.reason}
          </p>
        )}
        {report.status === 'valid' && (
          <p style={{ margin: 0 }}>
            Poin diberikan: <strong>{formatNumber(report.points_awarded ?? 0)}</strong>
          </p>
        )}
        {pending && (
          <p className="small muted" style={{ margin: 0 }}>
            Pemeriksaan berjalan di latar belakang dan bisa memakan beberapa menit. Anda akan menerima notifikasi; halaman
            ini juga diperbarui otomatis.
          </p>
        )}
        <p className="small muted" style={{ margin: 0 }}>
          ID laporan: <code>{report.report_id}</code>
        </p>
      </section>
      {pending && <AutoRefresh intervalMs={15_000} maxTimes={20} />}
      <p>
        <Link href="/pantau-harga">Kirim laporan lain</Link> &middot; <Link href="/akun">Lihat poin</Link>
      </p>
    </div>
  );
}
