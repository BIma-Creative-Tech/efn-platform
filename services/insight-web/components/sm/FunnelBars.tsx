/**
 * Funnel (MK-2): bar horizontal berurutan dengan label jumlah + persentase, bukan corong/Sankey; tabel alternatif
 * tersedia lewat <details>. Tahap di bawah k-anonim tampil "< 10" tanpa bar dan tanpa persentase (§6.6).
 */
import type { ReactNode } from 'react';
import { fmtPctOrNa } from '@/lib/sm-format';
import type { FunnelStep } from '@/lib/sm-vm';

export function FunnelBars({ steps }: { steps: readonly FunnelStep[] }): ReactNode {
  const max = steps.reduce((m, s) => (s.count !== null && s.count > m ? s.count : m), 0);
  return (
    <>
      <ol className="funnel" aria-label="Tahapan funnel pengunjung">
        {steps.map((s) => (
          <li key={s.label}>
            <div className="funnel-meta">
              <span>{s.label}</span>
              <span className="funnel-count">{s.text}</span>
              {s.pctOfFirst !== null ? <span>{fmtPctOrNa(s.pctOfFirst)} dari tahap awal</span> : null}
              {s.pctOfPrev !== null ? <span>{fmtPctOrNa(s.pctOfPrev)} dari tahap sebelumnya</span> : null}
              {s.biggestDrop ? <strong>Penurunan terbesar</strong> : null}
            </div>
            {s.count !== null && max > 0 ? <div className="funnel-bar" style={{ width: `${Math.max(1, (s.count / max) * 100)}%` }} aria-hidden="true" /> : null}
          </li>
        ))}
      </ol>
      <details>
        <summary>Tampilkan sebagai tabel</summary>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel funnel pengunjung">
          <table className="table">
            <caption className="sr-only">Funnel pengunjung (jumlah perangkat-hari ber-consent)</caption>
            <thead>
              <tr>
                <th scope="col">Tahap</th>
                <th scope="col" className="num">
                  Jumlah
                </th>
                <th scope="col" className="num">
                  % dari awal
                </th>
                <th scope="col" className="num">
                  % dari sebelumnya
                </th>
              </tr>
            </thead>
            <tbody>
              {steps.map((s) => (
                <tr key={s.label}>
                  <th scope="row">{s.label}</th>
                  <td className="num">{s.text}</td>
                  <td className="num">{fmtPctOrNa(s.pctOfFirst)}</td>
                  <td className="num">{fmtPctOrNa(s.pctOfPrev)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
