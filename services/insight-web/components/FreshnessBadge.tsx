import Link from 'next/link';
import type { FreshnessRow } from '@/lib/shell-data';
import { brandName } from '@/lib/brands';
import { fmtAge } from '@/lib/format';
import { freshnessStatus, STATUS_LABEL } from '@/lib/thresholds';
import { StatusPill } from './ui';

const ENTITY: Record<string, string> = {
  sales: 'Penjualan',
  payment: 'Pembayaran',
  production: 'Produksi',
  delivery: 'Pengiriman',
  transfer: 'Transfer',
  fin_sold: 'Terjual (FIN)',
};

export function FreshnessBadge({ ageHours, rows }: { ageHours: number | null; rows: FreshnessRow[] }) {
  const status = freshnessStatus(ageHours);
  const text = ageHours === null ? 'Tidak diketahui' : fmtAge(ageHours);
  return (
    <details className="fresh">
      <summary>
        <span className={`dot dot-${status}`} aria-hidden="true" />
        <span>Data: {text}</span>
        <span className="sr-only"> ({STATUS_LABEL[status]})</span>
      </summary>
      <div className="fresh-panel">
        {rows.length === 0 ? (
          <p>Kesegaran data tidak diketahui.</p>
        ) : (
          <ul>
            {rows.map((r) => (
              <li key={`${r.brand}-${r.entity}`}>
                <span>
                  {brandName(r.brand)}: {ENTITY[r.entity] ?? r.entity}
                </span>
                <StatusPill status={freshnessStatus(r.ageHours)} label={fmtAge(r.ageHours)} />
              </li>
            ))}
          </ul>
        )}
        <Link href="/data-quality">Lihat Kualitas Data</Link>
      </div>
    </details>
  );
}
