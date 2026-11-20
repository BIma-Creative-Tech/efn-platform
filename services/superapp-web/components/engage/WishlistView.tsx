'use client';

/**
 * Halaman "Wishlist" (G50; docs/04 §28.8): wishlist, pengingat stok, dan pengingat harga milik member. Bagian yang
 * fiturnya mati (flag) atau endpoint-nya 404/501 tidak ditampilkan. Nama/harga produk diambil dari katalog publik
 * (maks. 30 produk per muat); sisanya tampil sebagai "Produk #id".
 */
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { apiFetch, goToLogin } from '@/lib/cart-client';
import type { ProductDetail } from '@/lib/catalog';
import {
  isFeatureOff,
  NOTIFY_CHANNEL_LABEL,
  parsePriceAlerts,
  parseRestockAlerts,
  parseWishlist,
  type PriceAlert,
  type RestockAlert,
  type WishlistItem,
} from '@/lib/engage';
import { formatRupiah } from '@/lib/format';
import { useEngage } from './EngageProvider';
import styles from './engage.module.css';

export interface ChannelInfo {
  code: string;
  name: string;
  outlets: { code: string; name: string }[];
}

type Section<T> = { status: 'loading' | 'ready' | 'off' | 'error'; items: T[] };
const loading = <T,>(): Section<T> => ({ status: 'loading', items: [] });
const MAX_DETAILS = 30;

function productHref(channel: string, id: number): string {
  return `/belanja/${id}?${new URLSearchParams({ channel })}`;
}

export function WishlistView({ channels }: { channels: ChannelInfo[] }) {
  const { features, ready, announce } = useEngage();
  const [wish, setWish] = useState<Section<WishlistItem>>(loading);
  const [restock, setRestock] = useState<Section<RestockAlert>>(loading);
  const [price, setPrice] = useState<Section<PriceAlert>>(loading);
  const [details, setDetails] = useState<Record<string, ProductDetail | null>>({});

  const load = useCallback(async () => {
    const results = await Promise.all([
      features.wishlist
        ? Promise.all(channels.map((c) => apiFetch<{ items: unknown }>(`/api/me/wishlist?${new URLSearchParams({ channel: c.code })}`)))
        : Promise.resolve(null),
      features.restock ? apiFetch<{ items: unknown }>('/api/me/restock-alerts') : Promise.resolve(null),
      features.priceAlert ? apiFetch<{ items: unknown }>('/api/me/price-alerts') : Promise.resolve(null),
    ]);
    const [w, rs, pa] = results;
    const unauth = [...(w ?? []), rs, pa].some((r) => r !== null && !r.ok && r.status === 401);
    if (unauth) {
      goToLogin('/wishlist');
      return;
    }
    let wishItems: WishlistItem[] = [];
    if (w) {
      const bad = w.find((r) => !r.ok);
      if (bad && !bad.ok && isFeatureOff(bad.status, bad.code)) setWish({ status: 'off', items: [] });
      else if (bad) setWish({ status: 'error', items: [] });
      else {
        wishItems = w.flatMap((r) => (r.ok ? parseWishlist(r.data) : []));
        setWish({ status: 'ready', items: wishItems });
      }
    }
    let restockItems: RestockAlert[] = [];
    if (rs) {
      if (rs.ok) {
        restockItems = parseRestockAlerts(rs.data);
        setRestock({ status: 'ready', items: restockItems });
      } else setRestock({ status: isFeatureOff(rs.status, rs.code) ? 'off' : 'error', items: [] });
    }
    let priceItems: PriceAlert[] = [];
    if (pa) {
      if (pa.ok) {
        priceItems = parsePriceAlerts(pa.data);
        setPrice({ status: 'ready', items: priceItems });
      } else setPrice({ status: isFeatureOff(pa.status, pa.code) ? 'off' : 'error', items: [] });
    }

    const refs = new Map<string, { channel: string; id: number }>();
    for (const i of [...wishItems, ...restockItems, ...priceItems]) refs.set(`${i.channel_code}:${i.product_id}`, { channel: i.channel_code, id: i.product_id });
    const entries = await Promise.all(
      [...refs.entries()].slice(0, MAX_DETAILS).map(async ([key, ref]) => {
        const r = await apiFetch<ProductDetail>(`/api/catalog/products/${ref.id}?${new URLSearchParams({ channel: ref.channel })}`);
        return [key, r.ok ? r.data : null] as const;
      }),
    );
    setDetails(Object.fromEntries(entries));
  }, [channels, features.wishlist, features.restock, features.priceAlert]);

  // Muat awal: state awal sudah `loading`; load() dijalankan dari callback async agar setState tidak terjadi sinkron
  // di badan effect (react-hooks/set-state-in-effect, pola DeletionStatus). `active` mencegah muat setelah unmount.
  useEffect(() => {
    if (!ready) return;
    let active = true;
    void Promise.resolve().then(() => (active ? load() : undefined));
    return () => {
      active = false;
    };
  }, [ready, load]);

  if (!ready) return <p className="muted">Memuat…</p>;
  if (!features.wishlist && !features.restock && !features.priceAlert) {
    return <p className="notice">Fitur wishlist dan pengingat sedang tidak aktif.</p>;
  }

  const nameOf = (channel: string, id: number) => details[`${channel}:${id}`]?.name ?? `Produk #${id}`;
  const outletName = (channel: string, outlet: string) =>
    channels.find((c) => c.code === channel)?.outlets.find((o) => o.code === outlet)?.name ?? outlet;

  async function removeWish(i: WishlistItem) {
    const r = await apiFetch<null>(`/api/me/wishlist/items/${encodeURIComponent(i.channel_code)}/${i.product_id}`, { method: 'DELETE' });
    if (r.ok) {
      setWish((s) => ({ ...s, items: s.items.filter((x) => !(x.channel_code === i.channel_code && x.product_id === i.product_id)) }));
      announce(`${nameOf(i.channel_code, i.product_id)} dihapus dari wishlist.`);
    } else announce(r.message);
  }
  async function removeRestock(a: RestockAlert) {
    const r = await apiFetch<null>(`/api/me/restock-alerts/${encodeURIComponent(a.id)}`, { method: 'DELETE' });
    if (r.ok) {
      setRestock((s) => ({ ...s, items: s.items.filter((x) => x.id !== a.id) }));
      announce('Pengingat stok dibatalkan.');
    } else announce(r.message);
  }
  async function removePrice(a: PriceAlert) {
    const r = await apiFetch<null>('/api/me/price-alerts', {
      method: 'DELETE',
      body: JSON.stringify({ channel_code: a.channel_code, product_id: a.product_id }),
    });
    if (r.ok) {
      setPrice((s) => ({ ...s, items: s.items.filter((x) => !(x.channel_code === a.channel_code && x.product_id === a.product_id)) }));
      announce('Pengingat harga dihapus.');
    } else announce(r.message);
  }

  return (
    <div className="stack">
      {features.wishlist && wish.status !== 'off' && (
        <section className="stack" aria-labelledby="wl-title">
          <h2 id="wl-title" style={{ margin: 0 }}>
            Wishlist
          </h2>
          {wish.status === 'loading' ? (
            <p className="muted">Memuat…</p>
          ) : wish.status === 'error' ? (
            <p className="alert alert-error" role="alert">
              Wishlist belum dapat dimuat. Muat ulang halaman beberapa saat lagi.
            </p>
          ) : wish.items.length === 0 ? (
            <p className="card">
              Belum ada produk. Ketuk ikon hati di <Link href="/belanja">halaman produk</Link> untuk menyimpannya.
            </p>
          ) : (
            <ul className={styles.list}>
              {wish.items.map((i) => {
                const d = details[`${i.channel_code}:${i.product_id}`];
                return (
                  <li key={`${i.channel_code}:${i.product_id}`} className={styles.listItem}>
                    <Link href={productHref(i.channel_code, i.product_id)}>{nameOf(i.channel_code, i.product_id)}</Link>
                    {d && <span className="muted">{formatRupiah(d.price_incl)}</span>}
                    <button type="button" className="btn btn-secondary btn-small" onClick={() => void removeWish(i)}>
                      Hapus<span className="visually-hidden"> {nameOf(i.channel_code, i.product_id)} dari wishlist</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}

      {features.restock && restock.status !== 'off' && (
        <section className="stack" aria-labelledby="rs-title">
          <h2 id="rs-title" style={{ margin: 0 }}>
            Pengingat stok
          </h2>
          {restock.status === 'loading' ? (
            <p className="muted">Memuat…</p>
          ) : restock.status === 'error' ? (
            <p className="alert alert-error" role="alert">
              Pengingat stok belum dapat dimuat.
            </p>
          ) : restock.items.length === 0 ? (
            <p className="card">Tidak ada pengingat stok aktif. Pada produk yang habis, pilih &quot;Kabari saya bila tersedia&quot;.</p>
          ) : (
            <ul className={styles.list}>
              {restock.items.map((a) => (
                <li key={a.id} className={styles.listItem}>
                  <Link href={productHref(a.channel_code, a.product_id)}>{nameOf(a.channel_code, a.product_id)}</Link>
                  <span className="muted">
                    {outletName(a.channel_code, a.outlet_code)}
                    {a.channels.length > 0 ? ` · ${a.channels.map((c) => NOTIFY_CHANNEL_LABEL[c]).join(', ')}` : ''}
                  </span>
                  <button type="button" className="btn btn-secondary btn-small" onClick={() => void removeRestock(a)}>
                    Batalkan<span className="visually-hidden"> pengingat stok {nameOf(a.channel_code, a.product_id)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {features.priceAlert && price.status !== 'off' && (
        <section className="stack" aria-labelledby="pa-list-title">
          <h2 id="pa-list-title" style={{ margin: 0 }}>
            Pengingat harga
          </h2>
          {price.status === 'loading' ? (
            <p className="muted">Memuat…</p>
          ) : price.status === 'error' ? (
            <p className="alert alert-error" role="alert">
              Pengingat harga belum dapat dimuat.
            </p>
          ) : price.items.length === 0 ? (
            <p className="card">Tidak ada pengingat harga aktif. Atur dari halaman produk.</p>
          ) : (
            <ul className={styles.list}>
              {price.items.map((a) => (
                <li key={`${a.channel_code}:${a.product_id}`} className={styles.listItem}>
                  <Link href={productHref(a.channel_code, a.product_id)}>{nameOf(a.channel_code, a.product_id)}</Link>
                  <span className="muted">Target {formatRupiah(a.target_price_idr)}</span>
                  <button type="button" className="btn btn-secondary btn-small" onClick={() => void removePrice(a)}>
                    Hapus<span className="visually-hidden"> pengingat harga {nameOf(a.channel_code, a.product_id)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
