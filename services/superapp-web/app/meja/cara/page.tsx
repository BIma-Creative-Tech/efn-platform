import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { brandHasFnb } from '@/lib/booking';
import { getBrand } from '@/lib/brand-server';

export const metadata: Metadata = { title: 'Makan di tempat' };

/**
 * Panduan "Makan di tempat" (mode pesan, docs/04 §15.7). Pesan dari meja HANYA lewat QR di meja (`/meja#t=<token>`,
 * ADR-032): halaman ini menjelaskan caranya dan tidak membuka sesi meja apa pun.
 */
export default async function CaraMakanDiTempatPage() {
  const brand = await getBrand();
  if (!brandHasFnb(brand)) notFound();
  return (
    <div className="stack" style={{ maxWidth: '40rem', margin: '0 auto', width: '100%' }}>
      <h1 style={{ margin: 0 }}>Makan di tempat</h1>
      <section className="card stack">
        <ol style={{ margin: 0, paddingLeft: '1.25rem', display: 'grid', gap: '0.5rem' }}>
          <li>Duduk di meja, lalu pindai kode QR di meja atau pada slip dari pelayan dengan kamera ponsel.</li>
          <li>Pilih menu dan kirim pesanan langsung ke dapur, tanpa antre di kasir.</li>
          <li>Bayar di kasir saat selesai. Masuk dengan nomor HP bila ingin mengumpulkan poin.</li>
        </ol>
        <p className="small muted" style={{ margin: 0 }}>
          Demi keamanan, pesanan dari meja hanya bisa dibuka lewat kode QR di gerai.
        </p>
      </section>
      <p style={{ margin: 0, display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <Link href="/reservasi" className="btn">
          Reservasi meja dulu
        </Link>
        <Link href="/belanja" className="btn btn-secondary">
          Pesan untuk diambil
        </Link>
      </p>
    </div>
  );
}
