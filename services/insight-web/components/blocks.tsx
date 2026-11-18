/**
 * Pembungkus blok per komponen: satu blok gagal = satu kartu galat (bukan halaman putih). Pesan galat generik +
 * referensi korelasi singkat (tanpa SQL/host/nama tabel).
 */
import type { ReactNode } from 'react';
import type { Result } from '@/lib/loaders';
import { Icon } from './ui';
import { RetryButton } from './RetryButton';

export function ErrorCard({ title, refId, reason }: { title?: string; refId: string; reason?: 'rate_limited' }): ReactNode {
  return (
    <div className="card error-card" role="alert">
      <Icon name="bad" size={22} />
      <div>
        {title ? <p className="card-title">{title}</p> : null}
        <p>{reason === 'rate_limited' ? 'Terlalu banyak permintaan. Coba lagi sebentar.' : 'Data tidak dapat dimuat. Coba lagi.'}</p>
        <p className="muted small">Kode referensi: {refId}</p>
        <RetryButton />
      </div>
    </div>
  );
}

/** Render `ok` bila data tersedia, kartu galat bila tidak. */
export function Block<T>({ result, title, children }: { result: Result<T>; title?: string; children: (data: T) => ReactNode }): ReactNode {
  if (!result.ok) return <ErrorCard title={title} refId={result.ref} reason={result.reason} />;
  return <>{children(result.data)}</>;
}
