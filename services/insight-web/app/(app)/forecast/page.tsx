import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = { title: 'Forecast' };

/** Placeholder G44: statis, tidak memanggil data. */
export default function ForecastPage() {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Forecast</h1>
          <p className="page-q">Segera hadir (G44)</p>
        </div>
      </div>
      <p>Prakiraan penjualan dan kebutuhan produksi per cabang dan produk, dengan rentang ketidakpastian.</p>
      <div className="wireframe" aria-hidden="true">
        <div />
        <div />
        <div />
      </div>
      <Link className="btn btn-primary" href="/">
        Kembali ke Ringkasan
      </Link>
    </>
  );
}
