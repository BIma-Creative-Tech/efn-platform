import Link from 'next/link';
import { getBrand } from '@/lib/brand-server';
import { COMPANIES } from '@/lib/brands';

/**
 * Beranda. G17: demo cart MOCK (dihapus) diganti tautan ke belanja online sungguhan
 * (`/belanja` -> katalog BFF, `/keranjang`, `/pesanan`). Pajak tidak lagi disebut di sini: label pajak datang dari BFF/Odoo.
 */
export default async function HomePage() {
  const brand = await getBrand();
  const companies = brand.companies.map((c) => COMPANIES[c].displayName);

  return (
    <div className="stack">
      <section>
        <h1>{brand.name}</h1>
        <p className="muted">{brand.tagline}</p>
        <p className="small muted">Dilayani oleh: {companies.join(' dan ')}.</p>
      </section>

      <section className="card stack" aria-labelledby="belanja-judul">
        <h2 id="belanja-judul" style={{ margin: 0 }}>
          Belanja online, ambil di toko
        </h2>
        <p style={{ margin: 0 }}>
          Pilih barang, bayar sekali dengan QRIS, lalu ambil di toko dengan kode ambil. Harga sudah termasuk pajak.
        </p>
        <p style={{ margin: 0, display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <Link href="/belanja" className="btn">
            Mulai belanja
          </Link>
          <Link href="/keranjang" className="btn btn-secondary">
            Keranjang
          </Link>
          <Link href="/pesanan" className="btn btn-secondary">
            Pesanan saya
          </Link>
        </p>
      </section>

      <section className="card">
        <h2>Pantau Harga, dapat poin</h2>
        <p>
          Lihat harga lebih murah di toko lain? Foto label harganya dan kirim lewat{' '}
          <Link href="/pantau-harga">Pantau Harga</Link>. Laporan yang valid mendapat poin.
        </p>
      </section>
    </div>
  );
}
