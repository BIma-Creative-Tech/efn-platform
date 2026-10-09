'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  countItems,
  issueLabel,
  parseQuote,
  quoteHasIssues,
  setOutlet,
  setQty,
  type Cart,
  type CartGroup,
  type GuestCart,
  type Quote,
  type QuoteGroup,
} from '@/lib/cart';
import {
  apiFetch,
  clearGuestCart,
  getServerCart,
  goToLogin,
  mergeGuestCartIntoServer,
  readGuestCart,
  updateServerCart,
  writeGuestCart,
} from '@/lib/cart-client';
import { companyDisplayName, type CatalogChannel } from '@/lib/catalog';
import { formatRupiah } from '@/lib/format';
import { onlineErrorMessage, REQUOTE_CODES } from '@/lib/orders';
import { OnlineUnavailable } from './OnlineUnavailable';
import styles from './online.module.css';

type View =
  | { kind: 'loading' }
  | { kind: 'unavailable' }
  | { kind: 'error'; text: string }
  | { kind: 'guest'; guest: GuestCart }
  | { kind: 'member'; cart: Cart; quote: Quote | null; quoteError: string | null; notice: string | null };

type Msg = { kind: 'success' | 'error'; text: string } | null;

async function fetchQuote(): Promise<{ quote: Quote | null; error: string | null; unavailable: boolean; status: number }> {
  const r = await apiFetch<unknown>('/api/cart/quote', { method: 'POST', body: '{}' });
  if (!r.ok) return { quote: null, error: r.message, unavailable: r.code === 'online_unavailable', status: r.status };
  const quote = parseQuote(r.data);
  return quote
    ? { quote, error: null, unavailable: false, status: r.status }
    : { quote: null, error: onlineErrorMessage(null), unavailable: false, status: 502 };
}

/** Muat awal (tanpa menyentuh state): gabung keranjang tamu -> ambil keranjang -> quote server. */
async function loadView(loggedIn: boolean): Promise<View | 'login'> {
  if (!loggedIn) return { kind: 'guest', guest: readGuestCart() };
  let notice: string | null = null;
  const merged = await mergeGuestCartIntoServer();
  if (merged && !merged.ok) {
    if (merged.status === 401) return 'login';
    if (merged.code === 'online_unavailable') return { kind: 'unavailable' };
    notice = 'Sebagian isi keranjang dari perangkat ini belum dapat digabungkan.';
  } else if (merged?.ok) {
    notice = merged.data.warnings.length > 0 ? 'Keranjang digabungkan; beberapa barang tidak tersedia dan dihapus.' : 'Keranjang dari perangkat ini sudah digabungkan.';
  }
  const cart = await getServerCart();
  if (!cart.ok) {
    if (cart.status === 401) return 'login';
    if (cart.code === 'online_unavailable') return { kind: 'unavailable' };
    return { kind: 'error', text: cart.message };
  }
  if (cart.data.groups.length === 0) return { kind: 'member', cart: cart.data, quote: null, quoteError: null, notice };
  const q = await fetchQuote();
  if (q.status === 401) return 'login';
  if (q.unavailable) return { kind: 'unavailable' };
  return { kind: 'member', cart: cart.data, quote: q.quote, quoteError: q.error, notice };
}

function channelInfo(channels: CatalogChannel[], code: string) {
  return channels.find((c) => c.channel_code === code) ?? null;
}

function GroupTitle({ channel, code, companyCode, index, total }: { channel: CatalogChannel | null; code: string; companyCode?: string; index: number; total: number }) {
  const company = companyDisplayName(channel?.company_code ?? companyCode ?? code);
  return (
    <div className={styles.groupHeader}>
      <h2 id={`grp-${code}`}>{company}</h2>
      <span className="small muted">
        {channel?.name ?? code}
        {total > 1 ? ` · pesanan ${index + 1} dari ${total}` : ''}
      </span>
    </div>
  );
}

export function CartView({ loggedIn, channels }: { loggedIn: boolean; channels: CatalogChannel[] }) {
  const router = useRouter();
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [busy, setBusy] = useState(false);
  const [paying, setPaying] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  useEffect(() => {
    let active = true;
    void loadView(loggedIn).then((v) => {
      if (!active) return;
      if (v === 'login') goToLogin('/keranjang');
      else setView(v);
    });
    return () => {
      active = false;
    };
  }, [loggedIn]);

  const requote = useCallback(async (cart: Cart, notice: string | null = null) => {
    if (cart.groups.length === 0) {
      setView({ kind: 'member', cart, quote: null, quoteError: null, notice });
      return;
    }
    const q = await fetchQuote();
    if (q.status === 401) return goToLogin('/keranjang');
    if (q.unavailable) return setView({ kind: 'unavailable' });
    setView({ kind: 'member', cart, quote: q.quote, quoteError: q.error, notice });
  }, []);

  /** Ubah keranjang member (PUT versi) lalu hitung ulang quote di server. */
  async function mutateMember(fn: (groups: CartGroup[]) => CartGroup[]) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await updateServerCart(fn);
      if (!r.ok) {
        if (r.status === 401) return goToLogin('/keranjang');
        if (r.code === 'online_unavailable') return setView({ kind: 'unavailable' });
        setMsg({ kind: 'error', text: r.message });
        return;
      }
      const notice = r.data.warnings.length > 0 ? 'Beberapa barang sudah tidak tersedia dan dihapus dari keranjang.' : null;
      await requote({ version: r.data.version, groups: r.data.groups }, notice);
    } finally {
      setBusy(false);
    }
  }

  function mutateGuest(fn: (groups: CartGroup[]) => CartGroup[]) {
    if (view.kind !== 'guest') return;
    const next = { ...view.guest, groups: fn(view.guest.groups) };
    if (!writeGuestCart(next)) setMsg({ kind: 'error', text: 'Keranjang tidak bisa disimpan di perangkat ini.' });
    setView({ kind: 'guest', guest: next });
  }

  async function checkout() {
    if (view.kind !== 'member' || !view.quote) return;
    setPaying(true);
    setMsg(null);
    try {
      const r = await apiFetch<{ order_id: string }>('/api/orders', {
        method: 'POST',
        body: JSON.stringify({ quote_id: view.quote.quote_id, accept_total: view.quote.amount_payable_total }),
      });
      if (r.ok) {
        clearGuestCart();
        router.push(`/pesanan/${r.data.order_id}`);
        return;
      }
      if (r.status === 401) return goToLogin('/keranjang');
      if (r.code === 'online_unavailable') return setView({ kind: 'unavailable' });
      setMsg({ kind: 'error', text: r.message });
      if (REQUOTE_CODES.includes(r.code) || r.code === 'cart_has_issues') {
        const cart = await getServerCart();
        if (cart.ok) await requote(cart.data);
      }
    } finally {
      setPaying(false);
    }
  }

  if (view.kind === 'loading') {
    return (
      <p role="status" aria-live="polite">
        Memuat keranjang…
      </p>
    );
  }
  if (view.kind === 'unavailable') return <OnlineUnavailable />;
  if (view.kind === 'error') {
    return (
      <p className="alert alert-error" role="alert">
        {view.text}
      </p>
    );
  }

  const groups = view.kind === 'guest' ? view.guest.groups : view.cart.groups;
  if (groups.length === 0) {
    return (
      <section className="card stack">
        {view.kind === 'member' && view.notice && <p className="notice">{view.notice}</p>}
        <p style={{ margin: 0 }}>Keranjang Anda masih kosong.</p>
        <p style={{ margin: 0 }}>
          <Link href="/belanja" className="btn">
            Mulai belanja
          </Link>
        </p>
      </section>
    );
  }

  const quote = view.kind === 'member' ? view.quote : null;
  const blocked = !quote || quoteHasIssues(quote);
  const quoteGroup = (code: string): QuoteGroup | null => quote?.groups.find((g) => g.channel_code === code) ?? null;
  const mutate = view.kind === 'guest' ? mutateGuest : (fn: (g: CartGroup[]) => CartGroup[]) => void mutateMember(fn);

  return (
    <div className="stack">
      <div aria-live="polite" role="status">
        {view.kind === 'member' && view.notice && <p className="notice">{view.notice}</p>}
        {msg && <p className={`alert ${msg.kind === 'success' ? 'alert-success' : 'alert-error'}`}>{msg.text}</p>}
        {busy && <p className="small muted">Memperbarui keranjang…</p>}
      </div>

      {groups.length > 1 && (
        <p className="notice" role="note">
          Keranjang Anda berisi barang dari {groups.length} PT/kanal berbeda. Anda membayar sekali, tetapi akan menerima{' '}
          {groups.length} pesanan terpisah — masing-masing dengan nota dan pajaknya sendiri.
        </p>
      )}

      {groups.map((g, gi) => {
        const ch = channelInfo(channels, g.channel_code);
        const qg = quoteGroup(g.channel_code);
        const outlets = (ch?.outlets ?? []).filter((o) => o.pickup_enabled);
        return (
          <section key={g.channel_code} className={styles.group} aria-labelledby={`grp-${g.channel_code}`}>
            <GroupTitle channel={ch} code={g.channel_code} companyCode={qg?.company_code} index={gi} total={groups.length} />
            {outlets.length > 1 ? (
              <div className="field" style={{ marginTop: '0.5rem' }}>
                <label htmlFor={`outlet-${g.channel_code}`}>Ambil di toko</label>
                <select
                  id={`outlet-${g.channel_code}`}
                  value={g.outlet_code}
                  disabled={busy || paying}
                  onChange={(e) => mutate((gs) => setOutlet(gs, g.channel_code, e.target.value))}
                  style={{ font: 'inherit', padding: '0.5rem', borderRadius: 'var(--radius)' }}
                >
                  {outlets.map((o) => (
                    <option key={o.outlet_code} value={o.outlet_code}>
                      {o.public_name}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <p className="small" style={{ margin: '0.5rem 0 0' }}>
                Ambil di toko: <strong>{outlets[0]?.public_name ?? ch?.outlets.find((o) => o.outlet_code === g.outlet_code)?.public_name ?? g.outlet_code}</strong>
              </p>
            )}

            <ul className={styles.lines}>
              {g.lines.map((l) => {
                const ql = qg?.lines.find((x) => x.product_id === l.product_id);
                const issue = qg?.issues.find((i) => i.product_id === l.product_id);
                const name = ql?.name ?? (view.kind === 'guest' ? view.guest.names[String(l.product_id)] : undefined) ?? `Produk #${l.product_id}`;
                return (
                  <li key={l.product_id} className={styles.line}>
                    <span>
                      <span className={styles.lineName}>{name}</span>
                      {ql && (
                        <span className="small muted">
                          <br />
                          {formatRupiah(ql.price_unit_incl)} / satuan{ql.tax_labels.length > 0 ? ` · termasuk ${ql.tax_labels.join(', ')}` : ''}
                        </span>
                      )}
                      {(issue || (ql && !ql.available)) && (
                        <span className={styles.lineIssue}>
                          <br />
                          {issueLabel(issue?.code ?? 'item_unavailable')}
                        </span>
                      )}
                    </span>
                    <span className={styles.price}>{ql ? formatRupiah(ql.subtotal_incl) : ''}</span>
                    <span className={styles.lineControls}>
                      <span className={styles.qty} role="group" aria-label={`Jumlah ${name}`}>
                        <button
                          type="button"
                          disabled={busy || paying}
                          aria-label={l.qty <= 1 ? `Hapus ${name}` : `Kurangi ${name}`}
                          onClick={() => mutate((gs) => setQty(gs, g.channel_code, l.product_id, l.qty - 1))}
                        >
                          −
                        </button>
                        <span aria-live="polite">{l.qty}</span>
                        <button
                          type="button"
                          disabled={busy || paying || l.qty >= 99}
                          aria-label={`Tambah ${name}`}
                          onClick={() => mutate((gs) => setQty(gs, g.channel_code, l.product_id, l.qty + 1))}
                        >
                          +
                        </button>
                      </span>
                      <button
                        type="button"
                        className="btn btn-secondary btn-small"
                        disabled={busy || paying}
                        onClick={() => mutate((gs) => setQty(gs, g.channel_code, l.product_id, 0))}
                      >
                        Hapus
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>

            {qg && (
              <dl className={styles.totals}>
                <dt>Total {companyDisplayName(qg.company_code)} (termasuk pajak)</dt>
                <dd className={styles.price}>{formatRupiah(qg.amount_payable)}</dd>
                {qg.tax_groups.map((t) => (
                  <PairRow key={t.label} label={`Termasuk ${t.label}`} value={formatRupiah(t.amount)} />
                ))}
                {qg.amount_total !== qg.amount_payable && (
                  <PairRow label="Pembulatan" value={formatRupiah(qg.amount_payable - qg.amount_total)} />
                )}
              </dl>
            )}
          </section>
        );
      })}

      {view.kind === 'guest' ? (
        <div className={styles.checkoutBar}>
          <span className="small">{countItems(groups)} barang · total dihitung setelah masuk</span>
          <Link href="/masuk?next=%2Fkeranjang" className="btn">
            Masuk untuk lanjut
          </Link>
        </div>
      ) : (
        <>
          {view.quoteError && (
            <div className="alert alert-error" role="alert">
              <p style={{ margin: 0 }}>{view.quoteError}</p>
              <button type="button" className="btn btn-secondary btn-small" style={{ marginTop: '0.5rem' }} onClick={() => void requote(view.cart)}>
                Hitung ulang
              </button>
            </div>
          )}
          {quote && (
            <section className="card" aria-labelledby="ringkasan">
              <h2 id="ringkasan" style={{ marginTop: 0, fontSize: '1.0625rem' }}>
                Ringkasan pembayaran
              </h2>
              <dl className={styles.totals} style={{ borderTop: 0, marginTop: 0 }}>
                {quote.groups.map((qg) => (
                  <PairRow key={qg.channel_code} label={companyDisplayName(qg.company_code)} value={formatRupiah(qg.amount_payable)} />
                ))}
                <dt className={styles.grand}>Total bayar</dt>
                <dd className={styles.grand}>{formatRupiah(quote.amount_payable_total)}</dd>
              </dl>
              <p className="hint" style={{ marginBottom: 0 }}>
                Harga sudah termasuk pajak dan dihitung oleh sistem toko. Pembayaran lewat QRIS, batas waktu 15 menit setelah
                pesanan dibuat. Stok dicek lagi setelah pembayaran; bila habis, bagian itu dibatalkan dan dana dikembalikan.
              </p>
            </section>
          )}
          <div className={styles.checkoutBar}>
            <span className="small">
              {countItems(groups)} barang
              {quote && quoteHasIssues(quote) ? ' · perbaiki barang yang ditandai' : ''}
            </span>
            <button type="button" className="btn" disabled={blocked || busy || paying} onClick={() => void checkout()}>
              {paying ? 'Membuat pesanan…' : quote ? `Bayar ${formatRupiah(quote.amount_payable_total)}` : 'Bayar'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function PairRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}
