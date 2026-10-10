import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getBrand } from '@/lib/brand-server';
import { companyDisplayName, parsePositiveInt } from '@/lib/catalog';
import { loadChannels, loadProduct, pickChannel } from '@/lib/catalog-server';
import { formatRupiah } from '@/lib/format';
import { getSessionToken } from '@/lib/session';
import { AddToCart } from '@/components/online/AddToCart';
import { OnlineUnavailable } from '@/components/online/OnlineUnavailable';
import { ProductImage } from '@/components/online/ProductImage';
import styles from '@/components/online/online.module.css';

export const metadata: Metadata = { title: 'Detail Produk' };
export const dynamic = 'force-dynamic';

/**
 * Detail produk (docs/04 §10.3.2): gambar via proxy, harga indikatif termasuk pajak + label dari BFF, deskripsi teks
 * polos, ketersediaan per toko ambil (client, `/api/catalog/stock`), tambah ke keranjang.
 */
export default async function ProductPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const productId = parsePositiveInt(id);
  if (productId === null) notFound();

  const brand = await getBrand();
  const channels = await loadChannels(brand);
  if (!channels.ok) return <OnlineUnavailable reason={channels.unavailable ? 'off' : 'error'} />;
  const requested = Array.isArray(sp.channel) ? sp.channel[0] : sp.channel;
  const channel = pickChannel(channels.data, requested);
  if (!channel) notFound();

  const product = await loadProduct(productId, channel.channel_code);
  if (!product.ok) {
    if (product.notFound) notFound();
    return <OnlineUnavailable reason={product.unavailable ? 'off' : 'error'} />;
  }
  const p = product.data;
  const loggedIn = Boolean(await getSessionToken());
  const returnPath = `/belanja/${productId}?channel=${encodeURIComponent(channel.channel_code)}`;

  return (
    <div className="stack">
      <nav aria-label="Remah roti" className="small">
        <Link href="/belanja">Belanja</Link> ›{' '}
        {p.category_id !== null ? (
          <Link href={`/belanja?category=${p.category_id}`}>Kategori</Link>
        ) : (
          <span>Produk</span>
        )}
      </nav>
      <div className={styles.detail}>
        <ProductImage sha={p.image_sha} alt={p.name} size={1024} />
        <div className="stack">
          <h1 style={{ margin: 0, overflowWrap: 'anywhere' }}>{p.name}</h1>
          <p style={{ margin: 0 }}>
            <span className={styles.price} style={{ fontSize: '1.5rem' }}>
              {formatRupiah(p.price_incl)}
            </span>
            {p.uom && <span className="muted"> / {p.uom}</span>}
          </p>
          <p className="small muted" style={{ margin: 0 }}>
            {p.tax_labels.length > 0 ? `Harga termasuk ${p.tax_labels.join(', ')}. ` : 'Harga termasuk pajak. '}
            Dijual oleh {companyDisplayName(channel.company_code)}. Total akhir dihitung saat checkout.
          </p>
          {p.description && <p className={styles.description}>{p.description}</p>}
          <AddToCart
            product={{ product_id: p.product_id, name: p.name, max_qty: p.max_qty }}
            channelCode={channel.channel_code}
            outlets={channel.outlets}
            deliveryAvailable={channel.fulfilment.delivery}
            loggedIn={loggedIn}
            returnPath={returnPath}
          />
        </div>
      </div>
    </div>
  );
}
