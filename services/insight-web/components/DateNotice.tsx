import { DATE_NA_REASON } from '@/lib/page-filters';

/**
 * Keterangan eksplisit di halaman yang tidak memakai rentang tanggal (review 21 Nov 2026): filter tanggal di URL tidak
 * diabaikan diam-diam. Alasan satu sumber di lib/page-filters.ts DATE_NA_REASON.
 */
export function DateNotice({ path }: { path: string }): React.ReactNode {
  const reason = DATE_NA_REASON[path];
  if (!reason) return null;
  return (
    <p className="notice" role="note">
      <strong>Rentang tanggal tidak berlaku:</strong> {reason}
    </p>
  );
}
