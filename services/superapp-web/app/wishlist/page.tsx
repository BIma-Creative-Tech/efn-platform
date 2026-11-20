import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { getBrand } from '@/lib/brand-server';
import { loadChannels } from '@/lib/catalog-server';
import { getSessionToken } from '@/lib/session';
import { WishlistView } from '@/components/engage/WishlistView';

export const metadata: Metadata = { title: 'Wishlist' };
export const dynamic = 'force-dynamic';

/**
 * Wishlist + pengingat stok + pengingat harga (G50; docs/04 §28.8). Butuh login; isi bagian mengikuti flag fitur dari BFF
 * (komponen klien menyembunyikan bagian yang mati).
 */
export default async function WishlistPage() {
  const token = await getSessionToken();
  if (!token) redirect('/masuk?next=/wishlist');

  const brand = await getBrand();
  const channels = await loadChannels(brand);
  const info = channels.ok
    ? channels.data.map((c) => ({
        code: c.channel_code,
        name: c.name,
        outlets: c.outlets.map((o) => ({ code: o.outlet_code, name: o.public_name })),
      }))
    : [];

  return (
    <div className="stack" style={{ maxWidth: '44rem', margin: '0 auto' }}>
      <p>
        <Link href="/akun">&larr; Akun Saya</Link>
      </p>
      <h1>Wishlist &amp; pengingat</h1>
      <p className="muted">
        Simpan produk favorit dan minta dikabari saat stok kembali atau harga turun. Ini adalah permintaan Anda sendiri dan tidak
        memerlukan persetujuan pelacakan.
      </p>
      <WishlistView channels={info} />
    </div>
  );
}
