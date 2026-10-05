'use client';

/**
 * Demo keranjang lintas PT (ADR unified cart, Proposed).
 * Prinsip: TIDAK menyembunyikan bahwa keranjang campuran = beberapa pesanan terpisah
 * (satu per PT, pajak berbeda), dibayar dalam satu transaksi oleh holding sebagai merchant of record.
 *
 * Pajak CONFIGURABLE (keputusan user Gelombang 5): komponen ini tidak memuat tarif/label pajak apa pun;
 * semuanya datang dari prop `taxConfigs` (sekarang MOCK dari `lib/mock-catalog.ts`, kelak BFF/Odoo).
 * Semua data MOCK; tidak ada checkout.
 */
import { useMemo, useState } from 'react';
import { COMPANIES } from '@/lib/brands';
import { formatRupiah } from '@/lib/format';
import { groupCartByCompany, type CartLine, type MockProduct, type TaxConfigMap } from '@/lib/mock-catalog';
import { describeTax, priceTaxHint } from '@/lib/tax';

export function CartDemo({ products, taxConfigs }: { products: MockProduct[]; taxConfigs: TaxConfigMap }) {
  const [qty, setQty] = useState<Record<string, number>>({});

  const lines: CartLine[] = useMemo(
    () => products.filter((p) => (qty[p.id] ?? 0) > 0).map((p) => ({ product: p, qty: qty[p.id] ?? 0 })),
    [products, qty],
  );
  const groups = useMemo(() => groupCartByCompany(lines, taxConfigs), [lines, taxConfigs]);
  const total = groups.reduce((s, g) => s + g.grandTotal, 0);

  const change = (id: string, delta: number) =>
    setQty((prev) => {
      const next = Math.max(0, Math.min(99, (prev[id] ?? 0) + delta));
      return { ...prev, [id]: next };
    });

  return (
    <div className="two-col">
      <section aria-labelledby="katalog-judul">
        <h2 id="katalog-judul">
          Katalog <span className="badge badge-mock">MOCK</span>
        </h2>
        <ul className="grid" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
          {products.map((p) => {
            const company = COMPANIES[p.company];
            const cfg = taxConfigs[p.company];
            const inCart = qty[p.id] ?? 0;
            return (
              <li key={p.id} className="card stack">
                <div>
                  <span className="badge">{company.displayName}</span>
                </div>
                <h3 style={{ margin: 0, fontSize: '1rem' }}>{p.name}</h3>
                <p className="small muted" style={{ margin: 0 }}>
                  {p.unit} &middot; {cfg ? priceTaxHint(cfg) : 'pajak dihitung saat checkout'}
                </p>
                <p style={{ margin: 0, fontWeight: 700 }}>{formatRupiah(p.price)}</p>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-small"
                    onClick={() => change(p.id, -1)}
                    disabled={inCart === 0}
                    aria-label={`Kurangi ${p.name}`}
                  >
                    &minus;
                  </button>
                  <span aria-live="polite" aria-label={`Jumlah ${p.name} di keranjang`}>
                    {inCart}
                  </span>
                  <button
                    type="button"
                    className="btn btn-small"
                    onClick={() => change(p.id, 1)}
                    aria-label={`Tambah ${p.name}`}
                  >
                    +
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      <aside aria-labelledby="keranjang-judul" className="stack">
        <h2 id="keranjang-judul">Keranjang</h2>
        {groups.length === 0 ? (
          <p className="muted">Keranjang masih kosong.</p>
        ) : (
          <>
            {groups.length > 1 && (
              <p className="notice small" role="note">
                Keranjang ini akan menjadi <strong>{groups.length} pesanan terpisah</strong> (satu per PT, pajak dan
                pengiriman berbeda), dibayar dalam satu pembayaran.
              </p>
            )}
            {groups.map((g, i) => {
              const c = COMPANIES[g.company];
              const cfg = g.taxConfig;
              return (
                <section key={g.company} className="cart-group" aria-label={`Pesanan ${i + 1}: ${c.displayName}`}>
                  <h3>
                    Pesanan {i + 1}: {c.displayName}
                  </h3>
                  <table className="cart-table">
                    <caption className="visually-hidden">Rincian pesanan {c.displayName}</caption>
                    <thead>
                      <tr>
                        <th scope="col">Item</th>
                        <th scope="col" className="num">
                          Jml
                        </th>
                        <th scope="col" className="num">
                          Harga
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {g.lines.map((l) => (
                        <tr key={l.product.id}>
                          <td>{l.product.name}</td>
                          <td className="num">{l.qty}</td>
                          <td className="num">{formatRupiah(l.product.price * l.qty)}</td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      {cfg === null ? (
                        <tr>
                          <td colSpan={2}>Pajak</td>
                          <td className="num">dihitung saat checkout</td>
                        </tr>
                      ) : cfg.price_include ? (
                        <>
                          {/* Inklusif: pajak adalah BAGIAN dari harga, tidak ditambahkan ke total. */}
                          <tr>
                            <td colSpan={2}>Harga sebelum pajak</td>
                            <td className="num">{formatRupiah(g.base)}</td>
                          </tr>
                          <tr>
                            <td colSpan={2}>{describeTax(cfg)}</td>
                            <td className="num">{formatRupiah(g.tax)}</td>
                          </tr>
                        </>
                      ) : (
                        <>
                          {/* Eksklusif: pajak DITAMBAHKAN di atas subtotal. */}
                          <tr>
                            <td colSpan={2}>Subtotal</td>
                            <td className="num">{formatRupiah(g.itemsTotal)}</td>
                          </tr>
                          <tr>
                            <td colSpan={2}>{describeTax(cfg)}</td>
                            <td className="num">+{formatRupiah(g.tax)}</td>
                          </tr>
                        </>
                      )}
                      <tr>
                        <td colSpan={2}>
                          <strong>Total pesanan {i + 1}</strong>
                        </td>
                        <td className="num">
                          <strong>{formatRupiah(g.grandTotal)}</strong>
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                  {cfg && (
                    <p className="small muted" style={{ marginBottom: 0 }}>
                      {cfg.price_include
                        ? `Harga sudah termasuk ${cfg.tax_label}; total tidak bertambah.`
                        : `${cfg.tax_label} ditambahkan di atas harga.`}{' '}
                      Konfigurasi pajak contoh (MOCK), menunggu konfirmasi tim pajak.
                    </p>
                  )}
                </section>
              );
            })}
            <p>
              <strong>Total dibayar: {formatRupiah(total)}</strong>
            </p>
            <button type="button" className="btn" disabled aria-describedby="checkout-info">
              Checkout
            </button>
            <p id="checkout-info" className="small muted">
              Checkout belum tersedia pada demo ini. Tarif dan perhitungan pajak hanya ilustrasi dari konfigurasi
              contoh; angka final dihitung oleh sistem ERP per PT.
            </p>
          </>
        )}
      </aside>
    </div>
  );
}
