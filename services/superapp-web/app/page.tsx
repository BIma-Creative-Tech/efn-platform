import Link from 'next/link';
import { getBrand } from '@/lib/brand-server';
import { COMPANIES } from '@/lib/brands';
import { loadTaxConfigs, productsForCompanies } from '@/lib/mock-catalog';
import { CartDemo } from '@/components/CartDemo';

export default async function HomePage() {
  const brand = await getBrand();
  const products = productsForCompanies(brand.companies);
  // Pajak configurable: dibaca server-side dari sumber data (MOCK sekarang, BFF/Odoo kelak).
  const taxConfigs = await loadTaxConfigs(brand.companies);
  const companies = brand.companies.map((c) => {
    const cfg = taxConfigs[c];
    return `${COMPANIES[c].displayName}${cfg ? ` (${cfg.tax_label})` : ''}`;
  });

  return (
    <div className="stack">
      <section>
        <h1>{brand.name}</h1>
        <p className="muted">{brand.tagline}</p>
        <p className="small muted">
          Dilayani oleh: {companies.join(' dan ')}.
        </p>
      </section>

      <div className="notice" role="note">
        <strong>Demo konsep (Fase 0).</strong> Katalog di bawah adalah <strong>data contoh (MOCK)</strong>, bukan
        produk dan harga sebenarnya. Keranjang hanya memperlihatkan pengelompokan per PT dan pajaknya; checkout dan
        pembayaran belum tersedia.
      </div>

      <CartDemo products={products} taxConfigs={taxConfigs} />

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
