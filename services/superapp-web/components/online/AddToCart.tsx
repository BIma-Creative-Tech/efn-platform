'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { describeSelection, validateSelection, type ProductAttribute, type Selection } from '@/lib/attributes';
import { addItem, lineKey } from '@/lib/cart';
import { addToServerCart, apiFetch, goToLogin, readGuestCart, writeGuestCart } from '@/lib/cart-client';
import { STOCK_LABEL, type CatalogOutlet, type StockStatus } from '@/lib/catalog';
import { AttributePicker } from '@/components/fnb/AttributePicker';
import styles from './online.module.css';

interface Props {
  /** `attributes` = modifier `no_variant` F&B (docs/04 §14.B B-2); `[]`/kosong untuk ritel. */
  product: { product_id: number; name: string; max_qty: number; attributes?: ProductAttribute[] };
  channelCode: string;
  outlets: CatalogOutlet[];
  deliveryAvailable: boolean;
  loggedIn: boolean;
  returnPath: string;
}

type Msg = { kind: 'success' | 'error'; text: string } | null;

/**
 * Pilih toko ambil + jumlah, lalu tambah ke keranjang. Ketersediaan per toko dari `/api/catalog/stock` (status saja,
 * tanpa qty). Tamu -> `localStorage` (tanpa harga); member -> `/api/cart` (PUT dengan versi).
 */
export function AddToCart({ product, channelCode, outlets, deliveryAvailable, loggedIn, returnPath }: Props) {
  const pickupOutlets = outlets.filter((o) => o.pickup_enabled);
  const [outlet, setOutlet] = useState<string>(pickupOutlets[0]?.outlet_code ?? '');
  const [qty, setQty] = useState(1);
  const [stock, setStock] = useState<Record<string, StockStatus | 'unknown'>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const attributes = product.attributes ?? [];
  const [selection, setSelection] = useState<Selection>({});
  const [missing, setMissing] = useState<number[]>([]);

  useEffect(() => {
    let active = true;
    const targets = pickupOutlets.slice(0, 10);
    void Promise.all(
      targets.map(async (o) => {
        const q = new URLSearchParams({ channel: channelCode, outlet: o.outlet_code, ids: String(product.product_id) });
        const r = await apiFetch<{ items: { product_id: number; status: StockStatus }[] }>(`/api/catalog/stock?${q}`);
        const status = r.ok ? r.data.items.find((i) => i.product_id === product.product_id)?.status : undefined;
        return [o.outlet_code, status ?? 'unknown'] as const;
      }),
    ).then((entries) => {
      if (!active) return;
      setStock(Object.fromEntries(entries));
      // Tamu: pakai toko yang sudah dipilih untuk kanal ini di keranjang perangkat (dibaca setelah hydration).
      if (!loggedIn) {
        const g = readGuestCart().groups.find((x) => x.channel_code === channelCode);
        if (g && targets.some((o) => o.outlet_code === g.outlet_code)) setOutlet(g.outlet_code);
      }
    });
    return () => {
      active = false;
    };
    // pickupOutlets diturunkan dari props `outlets`; cukup bergantung pada identitas produk/kanal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channelCode, product.product_id]);

  if (pickupOutlets.length === 0) {
    return <p className="notice">Produk ini belum bisa dipesan untuk diambil di toko.</p>;
  }

  const selectedStock = stock[outlet];
  const soldOut = selectedStock === 'out';
  const max = Math.max(1, product.max_qty);

  async function onAdd() {
    // Atribut F&B: validasi UX (atribut wajib terisi); BFF/Odoo memvalidasi kepemilikan nilai (PTAV) di server.
    const check = validateSelection(attributes, selection);
    if (!check.ok) {
      if (check.reason === 'missing_required') {
        setMissing(check.missing);
        setMsg({ kind: 'error', text: 'Lengkapi pilihan yang wajib (ditandai merah).' });
        setTimeout(() => document.querySelector<HTMLElement>('select[aria-invalid="true"], fieldset[aria-describedby] input')?.focus(), 0);
      } else {
        setMsg({ kind: 'error', text: 'Terlalu banyak pilihan tambahan.' });
      }
      return;
    }
    setMissing([]);
    setBusy(true);
    setMsg(null);
    const input = {
      channel_code: channelCode,
      outlet_code: outlet,
      product_id: product.product_id,
      qty,
      max_qty: max,
      ...(check.ids.length > 0 ? { attribute_value_ids: check.ids } : {}),
    };
    const optionsText = describeSelection(attributes, check.ids);
    const outletName = pickupOutlets.find((o) => o.outlet_code === outlet)?.public_name ?? outlet;
    try {
      if (!loggedIn) {
        const guest = readGuestCart();
        const r = addItem(guest.groups, input);
        if (!r.ok) {
          setMsg({ kind: 'error', text: r.reason === 'too_many_lines' ? 'Keranjang sudah penuh (maks. 50 jenis barang).' : 'Barang tidak dapat ditambahkan.' });
          return;
        }
        const options = optionsText ? { ...(guest.options ?? {}), [lineKey(product.product_id, check.ids)]: optionsText } : guest.options;
        const saved = writeGuestCart({
          groups: r.groups,
          names: { ...guest.names, [String(product.product_id)]: product.name },
          ...(options ? { options } : {}),
        });
        if (!saved) {
          setMsg({ kind: 'error', text: 'Keranjang tidak bisa disimpan di perangkat ini. Silakan masuk untuk berbelanja.' });
          return;
        }
        setMsg({
          kind: 'success',
          text: `Ditambahkan ke keranjang.${r.outletChanged ? ` Semua barang dari kanal ini akan diambil di ${outletName}.` : ''}`,
        });
        return;
      }
      const r = await addToServerCart(input);
      if (!r.ok) {
        if (r.status === 401) {
          goToLogin(returnPath);
          return;
        }
        setMsg({ kind: 'error', text: r.message });
        return;
      }
      const dropped = r.data.warnings?.some((w) => w.product_id === product.product_id);
      setMsg(
        dropped
          ? { kind: 'error', text: 'Produk ini sedang tidak tersedia dan tidak ditambahkan.' }
          : {
              kind: 'success',
              text: `Ditambahkan ke keranjang.${r.outletChanged ? ` Semua barang dari kanal ini akan diambil di ${outletName}.` : ''}`,
            },
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="card stack"
      onSubmit={(e) => {
        e.preventDefault();
        void onAdd();
      }}
    >
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend style={{ fontWeight: 700, marginBottom: '0.5rem' }}>Ambil di toko</legend>
        <ul className={styles.outletList}>
          {pickupOutlets.map((o) => {
            const st = stock[o.outlet_code];
            return (
              <li key={o.outlet_code}>
                <label className={styles.outletOption}>
                  <input
                    type="radio"
                    name="outlet"
                    value={o.outlet_code}
                    checked={outlet === o.outlet_code}
                    onChange={() => setOutlet(o.outlet_code)}
                  />
                  <span>
                    <span style={{ fontWeight: 600 }}>{o.public_name}</span>
                    <br />
                    <span className={`${styles.stock} ${st && st !== 'unknown' ? styles[`stock_${st}`] : ''}`}>
                      {st === undefined ? 'Memeriksa stok…' : st === 'unknown' ? 'Stok belum dapat dicek' : STOCK_LABEL[st]}
                    </span>
                    {o.prep_minutes > 0 && <span className="small muted"> · siap ± {o.prep_minutes} menit setelah bayar</span>}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
        {!deliveryAvailable && <p className="hint">Pengantaran belum tersedia; pesanan diambil di toko.</p>}
      </fieldset>

      {attributes.length > 0 && (
        <AttributePicker
          attributes={attributes}
          selection={selection}
          onChange={(next) => {
            setSelection(next);
            if (missing.length > 0) setMissing([]);
          }}
          idPrefix={`p${product.product_id}`}
          disabled={busy}
          missing={missing}
        />
      )}

      <div className="field">
        <span id="qty-label" style={{ fontWeight: 600 }}>
          Jumlah
        </span>
        <div className={styles.qty} role="group" aria-labelledby="qty-label">
          <button type="button" onClick={() => setQty((q) => Math.max(1, q - 1))} disabled={qty <= 1} aria-label="Kurangi jumlah">
            −
          </button>
          <output aria-live="polite">{qty}</output>
          <button type="button" onClick={() => setQty((q) => Math.min(max, q + 1))} disabled={qty >= max} aria-label="Tambah jumlah">
            +
          </button>
        </div>
        <span className="hint">Maksimum {max} per pesanan.</span>
      </div>

      <div className={styles.actions}>
        <button type="submit" className="btn" disabled={busy || !outlet || soldOut} aria-disabled={busy || !outlet || soldOut}>
          {soldOut ? 'Habis di toko ini' : busy ? 'Menambahkan…' : 'Tambah ke keranjang'}
        </button>
        <Link href="/keranjang">Lihat keranjang</Link>
      </div>
      <div aria-live="polite" role="status">
        {msg && <p className={`alert ${msg.kind === 'success' ? 'alert-success' : 'alert-error'}`}>{msg.text}</p>}
      </div>
      {!loggedIn && (
        <p className="hint" style={{ margin: 0 }}>
          Keranjang tamu disimpan di perangkat ini. Masuk untuk melihat total dan membayar.
        </p>
      )}
    </form>
  );
}
