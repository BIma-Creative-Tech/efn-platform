import type { Metadata } from 'next';
import Link from 'next/link';
import { getBrand } from '@/lib/brand-server';
import { companyDisplayName, CURSOR_RE, parsePositiveInt, sanitizeQuery, type ProductSummary } from '@/lib/catalog';
import { loadCategories, loadChannels, loadProducts, pickChannel } from '@/lib/catalog-server';
import { formatRupiah } from '@/lib/format';
import { OnlineUnavailable } from '@/components/online/OnlineUnavailable';
import { ProductImage } from '@/components/online/ProductImage';
import styles from '@/components/online/online.module.css';

export const metadata: Metadata = { title: 'Belanja' };
export const dynamic = 'force-dynamic';

type SP = Promise<Record<string, string | string[] | undefined>>;

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

function href(params: Record<string, string | number | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') p.set(k, String(v));
  const s = p.toString();
  return s ? `/belanja?${s}` : '/belanja';
}

/**
 * Katalog online (docs/04 §10.3.2): daftar, kategori (`?category=`), cari (`?q=`), halaman (`?cursor=`).
 * Data dari BFF (salinan katalog, bukan Odoo). Harga = indikatif termasuk pajak; tagihan final dari quote.
 */
export default async function BelanjaPage({ searchParams }: { searchParams: SP }) {
  const sp = await searchParams;
  const brand = await getBrand();
  const channels = await loadChannels(brand);
  if (!channels.ok) return <OnlineUnavailable reason={channels.unavailable ? 'off' : 'error'} />;
  const channel = pickChannel(channels.data, one(sp.channel));
  if (!channel) {
    return <OnlineUnavailable title="Belum ada toko online" />;
  }

  const category = parsePositiveInt(one(sp.category));
  const q = sanitizeQuery(one(sp.q));
  const cursorRaw = one(sp.cursor);
  const cursor = cursorRaw && CURSOR_RE.test(cursorRaw) ? cursorRaw : null;

  const [categories, products] = await Promise.all([
    loadCategories(channel.channel_code),
    loadProducts({ channel: channel.channel_code, category, q, cursor, limit: 24 }),
  ]);
  if (!products.ok && products.unavailable) return <OnlineUnavailable />;

  const base = { channel: channels.data.length > 1 ? channel.channel_code : null };
  const cats = categories.ok ? categories.data.filter((c) => c.parent_id === null || !categories.data.some((x) => x.category_id === c.parent_id)) : [];

  return (
    <div className="stack">
      <section className={styles.toolbar}>
        <h1 style={{ margin: 0 }}>Belanja</h1>
        <p className="small muted" style={{ margin: 0 }}>
          {companyDisplayName(channel.company_code)} · ambil di toko. Harga sudah termasuk pajak; total akhir dihitung saat
          checkout.
        </p>

        {channels.data.length > 1 && (
          <nav aria-label="Pilih toko/PT">
            <ul className={styles.chips}>
              {channels.data.map((c) => (
                <li key={c.channel_code}>
                  <Link className={styles.chip} href={href({ channel: c.channel_code })} aria-current={c.channel_code === channel.channel_code ? 'page' : undefined}>
                    {c.name}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        )}

        <form role="search" action="/belanja" method="get" className={styles.search}>
          {base.channel && <input type="hidden" name="channel" value={base.channel} />}
          {category !== null && <input type="hidden" name="category" value={category} />}
          <label htmlFor="cari" className="visually-hidden">
            Cari produk
          </label>
          <input id="cari" type="search" name="q" defaultValue={q ?? ''} maxLength={64} placeholder="Cari produk…" autoComplete="off" />
          <button type="submit" className="btn">
            Cari
          </button>
        </form>

        {cats.length > 0 && (
          <nav aria-label="Kategori">
            <ul className={styles.chips}>
              <li>
                <Link className={styles.chip} href={href({ ...base, q })} aria-current={category === null ? 'page' : undefined}>
                  Semua
                </Link>
              </li>
              {cats.map((c) => (
                <li key={c.category_id}>
                  <Link
                    className={styles.chip}
                    href={href({ ...base, category: c.category_id, q })}
                    aria-current={category === c.category_id ? 'page' : undefined}
                  >
                    {c.name}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </section>

      {!products.ok ? (
        <p className="alert alert-error" role="alert">
          Produk belum dapat dimuat. Silakan muat ulang beberapa saat lagi.
        </p>
      ) : products.data.items.length === 0 ? (
        <p className="card">{q ? `Tidak ada produk yang cocok dengan "${q}".` : 'Belum ada produk di kategori ini.'}</p>
      ) : (
        <>
          <p className="visually-hidden" role="status">
            {products.data.items.length} produk ditampilkan
          </p>
          <ul className={styles.productGrid}>
            {products.data.items.map((p) => (
              <li key={p.product_id}>
                <ProductCard product={p} channel={channel.channel_code} />
              </li>
            ))}
          </ul>
          <nav aria-label="Halaman produk" className={styles.actions}>
            {cursor && (
              <Link href={href({ ...base, category, q })} className="btn btn-secondary">
                Ke awal
              </Link>
            )}
            {products.data.next_cursor && (
              <Link href={href({ ...base, category, q, cursor: products.data.next_cursor })} className="btn">
                Produk berikutnya
              </Link>
            )}
          </nav>
        </>
      )}
    </div>
  );
}

function ProductCard({ product, channel }: { product: ProductSummary; channel: string }) {
  return (
    <Link href={`/belanja/${product.product_id}?channel=${encodeURIComponent(channel)}`} className={styles.productCard}>
      <ProductImage sha={product.image_sha} alt="" />
      <span className={styles.productBody}>
        <span className={styles.productName}>{product.name}</span>
        <span className={styles.price}>{formatRupiah(product.price_incl)}</span>
        <span className={styles.taxNote}>
          {product.uom ? `per ${product.uom}` : ''}
          {product.tax_labels.length > 0 ? `${product.uom ? ' · ' : ''}termasuk ${product.tax_labels.join(', ')}` : ''}
        </span>
      </span>
    </Link>
  );
}
