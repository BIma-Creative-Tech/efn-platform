import type { Metadata } from 'next';
import { getBrand } from '@/lib/brand-server';
import { loadChannels } from '@/lib/catalog-server';
import { getSessionToken } from '@/lib/session';
import { CartView } from '@/components/online/CartView';

export const metadata: Metadata = { title: 'Keranjang' };
export const dynamic = 'force-dynamic';

/**
 * Keranjang (docs/04 §10.3.3–§10.3.5): dikelompokkan per PT/kanal, harga & pajak dari quote server, satu tombol bayar.
 * Tamu: keranjang perangkat (tanpa harga) + ajakan masuk. Data kanal (nama, toko ambil) dari katalog publik.
 */
export default async function KeranjangPage() {
  const brand = await getBrand();
  const [channels, token] = await Promise.all([loadChannels(brand), getSessionToken()]);
  return (
    <div className="stack" style={{ maxWidth: '44rem', margin: '0 auto' }}>
      <h1 style={{ margin: 0 }}>Keranjang</h1>
      <CartView loggedIn={Boolean(token)} channels={channels.ok ? channels.data : []} />
    </div>
  );
}
