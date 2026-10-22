'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  countItems,
  groupLevelIssues,
  issueLabel,
  lineKey,
  MAX_PROMO_CODES,
  normalizePromoCode,
  pairQuoteLines,
  parseQuote,
  PROMO_INVALID_MESSAGE,
  PROMO_RATE_LIMIT_MESSAGE,
  quoteHasIssues,
  retainPromoCodes,
  setOutlet,
  setPickupSlot,
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
import { formatNumber, formatRupiah } from '@/lib/format';
import { onlineErrorMessage, REQUOTE_CODES } from '@/lib/orders';
import { COUNTER_UNAVAILABLE_REASON, paymentOptions, resolvePaymentMethod, type PaymentMethod } from '@/lib/payment';
import {
  canRedeemAny,
  INSUFFICIENT_BALANCE_MESSAGE,
  POINTS_REFRESH_CODES,
  pointsErrorMessage,
  resolveAllocation,
  suggestMaxPoints,
  type Allocation,
  type QuotePoints,
} from '@/lib/points';
import {
  buildInvoiceRequest,
  EMPTY_INVOICE_FORM,
  INVOICE_NOT_AVAILABLE_MESSAGE,
  invoiceRequestAvailable,
  validateInvoiceRequest,
  type InvoiceErrors,
  type InvoiceRequestInput,
} from '@/lib/invoice-request';
import { InvoiceRequestForm } from './InvoiceRequestForm';
import { PickupSlotPicker } from './PickupSlotPicker';
import { OnlineUnavailable } from './OnlineUnavailable';
import styles from './online.module.css';

type View =
  | { kind: 'loading' }
  | { kind: 'unavailable' }
  | { kind: 'error'; text: string }
  | { kind: 'guest'; guest: GuestCart }
  | { kind: 'member'; cart: Cart; quote: Quote | null; quoteError: string | null; notice: string | null };

type Msg = { kind: 'success' | 'error'; text: string } | null;

type QuoteFetch = { quote: Quote | null; error: string | null; unavailable: boolean; status: number; promoNotice: string | null };

/**
 * Ambil quote. `codes` = kode promo (dikirim lewat body POST saja — tidak pernah di URL/log/analytics, §11.2).
 * Bila quote dengan kode ditolak karena 429 (rate limit promo) atau 400, dan `fallback` aktif, hitung ulang TANPA kode
 * supaya checkout tidak terblokir; `promoNotice` memuat pesan generik untuk ditampilkan di area promo.
 */
async function fetchQuote(codes: string[] = [], fallback = true): Promise<QuoteFetch> {
  const r = await apiFetch<unknown>('/api/cart/quote', {
    method: 'POST',
    body: JSON.stringify(codes.length > 0 ? { promo_codes: codes } : {}),
  });
  if (!r.ok) {
    if (codes.length > 0 && (r.status === 429 || r.status === 400)) {
      const notice = r.status === 429 ? PROMO_RATE_LIMIT_MESSAGE : PROMO_INVALID_MESSAGE;
      if (fallback) return { ...(await fetchQuote([], false)), promoNotice: notice };
      return { quote: null, error: null, unavailable: false, status: r.status, promoNotice: notice };
    }
    return { quote: null, error: r.message, unavailable: r.code === 'online_unavailable', status: r.status, promoNotice: null };
  }
  const quote = parseQuote(r.data);
  return quote
    ? { quote, error: null, unavailable: false, status: r.status, promoNotice: null }
    : { quote: null, error: onlineErrorMessage(null), unavailable: false, status: 502, promoNotice: null };
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

/** Baris quote untuk tiap baris keranjang grup ini (modifier berbeda = baris berbeda). */
function pairedLines(cartLines: CartGroup['lines'], qg: QuoteGroup | null) {
  return pairQuoteLines(cartLines, qg?.lines ?? []);
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
  // Kode promo hanya di memori komponen (bukan URL, localStorage, atau analytics). Hilang saat halaman dimuat ulang.
  const promoRef = useRef<string[]>([]);
  // Salinan untuk render (ref tidak boleh dibaca saat render); diperbarui bersama ref lewat setCodes.
  const [heldCodes, setHeldCodes] = useState<string[]>([]);
  const setCodes = useCallback((next: string[]) => {
    promoRef.current = next;
    setHeldCodes(next);
  }, []);
  const [promoInput, setPromoInput] = useState('');
  const [promoMsg, setPromoMsg] = useState<Msg>(null);
  // Metode bayar (§12.2). `counterDenied`: BFF menjawab 422 payment_method_unavailable -> opsi dikunci + pesan.
  const [chosenMethod, setChosenMethod] = useState<PaymentMethod | null>(null);
  const [counterDenied, setCounterDenied] = useState(false);
  // Faktur atas nama (§13.5, L-63): data pribadi HANYA di memori komponen — bukan URL/storage/log/analytics.
  const [invoiceOn, setInvoiceOn] = useState(false);
  const [invoiceForm, setInvoiceForm] = useState<InvoiceRequestInput>(EMPTY_INVOICE_FORM);
  const [invoiceErrors, setInvoiceErrors] = useState<InvoiceErrors>({});
  // Poin sebagai alat bayar per PT (§14.C.2): isian jumlah poin per kanal, hanya di memori komponen.
  const [pointsInputs, setPointsInputs] = useState<Record<string, string>>({});

  function resetInvoice() {
    setInvoiceOn(false);
    setInvoiceForm(EMPTY_INVOICE_FORM);
    setInvoiceErrors({});
  }

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

  /** Sinkronkan kode promo terpakai + pesan generik dari hasil quote. Kode tidak berlaku tidak dibedakan penyebabnya. */
  const settlePromo = useCallback((q: QuoteFetch, sent: string[], explicit: boolean) => {
    if (q.promoNotice) {
      // Fallback tanpa kode (429/400): kode tetap disimpan di memori untuk percobaan berikutnya.
      setCodes(retainPromoCodes(promoRef.current, null, true));
      setPromoMsg({
        kind: 'error',
        text: promoRef.current.length > 0 ? `${q.promoNotice} Kode Anda tetap tersimpan; ringkasan sementara dihitung tanpa kode.` : q.promoNotice,
      });
      return;
    }
    if (!q.quote) return;
    setCodes(retainPromoCodes(promoRef.current, q.quote.promo.applied, false));
    if (q.quote.promo.invalid.length > 0) setPromoMsg({ kind: 'error', text: PROMO_INVALID_MESSAGE });
    else if (explicit && sent.length > 0) setPromoMsg({ kind: 'success', text: 'Kode promo diterapkan.' });
    else if (sent.length === 0) setPromoMsg(null);
  }, []);

  const requote = useCallback(
    async (cart: Cart, notice: string | null = null) => {
      if (cart.groups.length === 0) {
        setCodes([]);
        setPromoMsg(null);
        setView({ kind: 'member', cart, quote: null, quoteError: null, notice });
        return;
      }
      const sent = promoRef.current;
      const q = await fetchQuote(sent);
      if (q.status === 401) return goToLogin('/keranjang');
      if (q.unavailable) return setView({ kind: 'unavailable' });
      settlePromo(q, sent, false);
      setView({ kind: 'member', cart, quote: q.quote, quoteError: q.error, notice });
    },
    [settlePromo],
  );

  async function applyPromo() {
    if (view.kind !== 'member' || busy || paying) return;
    const code = normalizePromoCode(promoInput);
    if (code === null) return setPromoMsg({ kind: 'error', text: PROMO_INVALID_MESSAGE });
    if (promoRef.current.includes(code)) return setPromoMsg({ kind: 'error', text: 'Kode promo ini sudah dimasukkan.' });
    if (promoRef.current.length >= MAX_PROMO_CODES) {
      return setPromoMsg({ kind: 'error', text: `Maksimal ${MAX_PROMO_CODES} kode promo.` });
    }
    const sent = [...promoRef.current, code];
    setBusy(true);
    setPromoMsg(null);
    try {
      const q = await fetchQuote(sent, false);
      if (q.status === 401) return goToLogin('/keranjang');
      if (q.unavailable) return setView({ kind: 'unavailable' });
      if (!q.quote) {
        // 429/400/galat lain: pertahankan ringkasan lama; checkout tidak terblokir.
        setPromoMsg({ kind: 'error', text: q.promoNotice ?? q.error ?? PROMO_INVALID_MESSAGE });
        return;
      }
      settlePromo(q, sent, true);
      setPromoInput('');
      setView({ kind: 'member', cart: view.cart, quote: q.quote, quoteError: null, notice: view.notice });
    } finally {
      setBusy(false);
    }
  }

  async function removePromo(code: string) {
    if (view.kind !== 'member' || busy || paying) return;
    setBusy(true);
    setPromoMsg(null);
    try {
      setCodes(promoRef.current.filter((c) => c !== code));
      await requote(view.cart, view.notice);
    } finally {
      setBusy(false);
    }
  }

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

  async function checkout(method: PaymentMethod) {
    if (view.kind !== 'member' || !view.quote) return;
    // Hanya dikirim bila opsi aktif DAN keranjang masih seluruhnya kanal PT PPN (validasi klien = UX saja).
    const invoiceEligible = invoiceRequestAvailable(view.quote.groups.map((g) => g.company_code));
    let invoice: InvoiceRequestInput | null = null;
    if (invoiceOn && invoiceEligible) {
      const errs = validateInvoiceRequest(invoiceForm);
      if (Object.keys(errs).length > 0) {
        setInvoiceErrors(errs);
        setMsg({ kind: 'error', text: 'Lengkapi data faktur pajak yang ditandai, atau matikan opsi faktur.' });
        setTimeout(() => document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 0);
        return;
      }
      setInvoiceErrors({});
      invoice = buildInvoiceRequest(invoiceForm);
    }
    // Poin (§14.C.2): validasi UX; server tetap memvalidasi (saldo, minimum, batas %, porsi tunai ≥ Rp1).
    const alloc = resolveAllocation(view.quote.points, view.quote.groups, pointsInputs);
    if (!alloc.valid) {
      setMsg({ kind: 'error', text: 'Periksa jumlah poin yang ditandai sebelum membayar, atau kosongkan untuk membayar tanpa poin.' });
      setTimeout(() => document.querySelector<HTMLElement>('[data-points-input][aria-invalid="true"]')?.focus(), 0);
      return;
    }
    setPaying(true);
    setMsg(null);
    try {
      const r = await apiFetch<{ order_id: string }>('/api/orders', {
        method: 'POST',
        body: JSON.stringify({
          quote_id: view.quote.quote_id,
          accept_total: view.quote.amount_payable_total,
          payment_method: method,
          ...(invoice ? { invoice_request: invoice } : {}),
          ...(alloc.request.length > 0 ? { points_redeem: alloc.request } : {}),
        }),
      });
      invoice = null;
      if (r.ok) {
        resetInvoice();
        clearGuestCart();
        router.push(`/pesanan/${r.data.order_id}`);
        return;
      }
      if (r.status === 401) {
        resetInvoice();
        return goToLogin('/keranjang');
      }
      if (r.code === 'online_unavailable') return setView({ kind: 'unavailable' });
      if (r.code === 'invoice_not_available') {
        // 422: data dibuang dari state; pelanggan bisa membayar tanpa faktur atas nama.
        resetInvoice();
        setMsg({ kind: 'error', text: INVOICE_NOT_AVAILABLE_MESSAGE });
        return;
      }
      setMsg({ kind: 'error', text: r.message });
      if (r.code === 'payment_method_unavailable' && method === 'counter') {
        setCounterDenied(true);
        setChosenMethod(null);
      }
      if (r.code === 'points_unavailable') setPointsInputs({});
      if (REQUOTE_CODES.includes(r.code) || r.code === 'cart_has_issues' || r.code === 'slot_unavailable' || POINTS_REFRESH_CODES.includes(r.code)) {
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
  // Saat fallback tanpa kode, quote.promo.applied kosong; tetap tampilkan kode yang disimpan agar bisa dihapus.
  const shownCodes = quote ? (quote.promo.applied.length > 0 ? quote.promo.applied : heldCodes) : [];
  const baseOptions = paymentOptions(groups.map((g) => channelInfo(channels, g.channel_code)?.payment_modes ?? null));
  const options = counterDenied
    ? baseOptions.map((o) => (o.method === 'counter' ? { ...o, enabled: false, reason: COUNTER_UNAVAILABLE_REASON } : o))
    : baseOptions;
  const method = resolvePaymentMethod(chosenMethod, options);
  const mutate = view.kind === 'guest' ? mutateGuest : (fn: (g: CartGroup[]) => CartGroup[]) => void mutateMember(fn);
  // Poin (§14.C.2): alokasi diturunkan dari isian + quote saat render (tanpa effect); kuota otomatis ikut quote terbaru.
  const redeemable = canRedeemAny(quote?.points ?? null);
  const alloc = resolveAllocation(redeemable ? (quote?.points ?? null) : null, quote?.groups ?? [], pointsInputs);

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
            {/* G23 §15.4: jadwal ambil pre-order hanya untuk outlet ber-slot (BFF mengirim slot_enabled bila aktif). */}
            {g.fulfilment === 'pickup' && ch?.outlets.find((o) => o.outlet_code === g.outlet_code)?.slot_enabled === true && (
              <PickupSlotPicker
                key={`${g.channel_code}-${g.outlet_code}`}
                channel={g.channel_code}
                outlet={g.outlet_code}
                value={g.pickup_slot ?? null}
                disabled={busy || paying}
                onChange={(slot) => mutate((gs) => setPickupSlot(gs, g.channel_code, slot))}
              />
            )}

            {qg &&
              groupLevelIssues(qg).map((gi2) => (
                <p key={gi2.code} className="alert alert-error" role="alert" style={{ marginTop: '0.5rem' }}>
                  {issueLabel(gi2.code)} Pembayaran belum bisa dilanjutkan untuk keranjang ini.
                </p>
              ))}
            <ul className={styles.lines}>
              {g.lines.map((l, li) => {
                // G22: baris produk sama dengan modifier berbeda dipasangkan menurut urutan (pairQuoteLines).
                const ql = pairedLines(g.lines, qg)[li];
                const issue = qg?.issues.find((i) => i.product_id === l.product_id);
                const name = ql?.name ?? (view.kind === 'guest' ? view.guest.names[String(l.product_id)] : undefined) ?? `Produk #${l.product_id}`;
                const optionsText =
                  ql?.attributes_text || (view.kind === 'guest' ? (view.guest.options?.[lineKey(l.product_id, l.attribute_value_ids)] ?? '') : '');
                return (
                  <li key={lineKey(l.product_id, l.attribute_value_ids)} className={styles.line}>
                    <span>
                      <span className={styles.lineName}>{name}</span>
                      {optionsText && (
                        <span className="small muted">
                          <br />
                          {optionsText}
                        </span>
                      )}
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
                          onClick={() => mutate((gs) => setQty(gs, g.channel_code, l.product_id, l.qty - 1, undefined, l.attribute_value_ids))}
                        >
                          −
                        </button>
                        <span aria-live="polite">{l.qty}</span>
                        <button
                          type="button"
                          disabled={busy || paying || l.qty >= 99}
                          aria-label={`Tambah ${name}`}
                          onClick={() => mutate((gs) => setQty(gs, g.channel_code, l.product_id, l.qty + 1, undefined, l.attribute_value_ids))}
                        >
                          +
                        </button>
                      </span>
                      <button
                        type="button"
                        className="btn btn-secondary btn-small"
                        disabled={busy || paying}
                        onClick={() => mutate((gs) => setQty(gs, g.channel_code, l.product_id, 0, undefined, l.attribute_value_ids))}
                      >
                        Hapus
                      </button>
                    </span>
                  </li>
                );
              })}
              {(qg?.lines ?? [])
                .filter((rl) => rl.reward)
                .map((rl, ri) => (
                  <li key={`reward-${ri}`} className={`${styles.line} ${styles.rewardLine}`}>
                    <span>
                      <span className={styles.lineName}>{rl.name}</span>
                      <span className="small muted">
                        <br />
                        Diskon promo
                      </span>
                    </span>
                    <span className={styles.price}>{formatRupiah(rl.subtotal_incl)}</span>
                  </li>
                ))}
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
              <div className={styles.promo}>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void applyPromo();
                  }}
                  noValidate
                >
                  <label htmlFor="promo-code">Kode promo (opsional)</label>
                  <div className={styles.promoRow}>
                    <input
                      id="promo-code"
                      name="promo"
                      type="text"
                      value={promoInput}
                      maxLength={40}
                      autoComplete="off"
                      autoCapitalize="characters"
                      spellCheck={false}
                      enterKeyHint="done"
                      aria-describedby="promo-status promo-hint"
                      disabled={busy || paying || shownCodes.length >= MAX_PROMO_CODES}
                      onChange={(e) => setPromoInput(e.target.value)}
                    />
                    <button type="submit" className="btn btn-secondary" disabled={busy || paying || promoInput.trim() === '' || shownCodes.length >= MAX_PROMO_CODES}>
                      Terapkan
                    </button>
                  </div>
                </form>
                <p id="promo-hint" className="hint" style={{ margin: '0.25rem 0 0' }}>
                  Maksimal {MAX_PROMO_CODES} kode. Kode yang tidak berlaku tidak menghalangi pembayaran.
                </p>
                <div id="promo-status" aria-live="polite" role="status">
                  {promoMsg && <p className={`alert ${promoMsg.kind === 'success' ? 'alert-success' : 'alert-error'}`}>{promoMsg.text}</p>}
                </div>
                {shownCodes.length > 0 && (
                  <ul className={styles.promoApplied} aria-label="Kode promo terpakai">
                    {shownCodes.map((c) => (
                      <li key={c}>
                        <span className={styles.promoCode}>{c}</span>
                        <button type="button" className="btn btn-secondary btn-small" disabled={busy || paying} aria-label={`Hapus kode promo ${c}`} onClick={() => void removePromo(c)}>
                          Hapus
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <dl className={styles.totals} style={{ borderTop: 0, marginTop: 0 }}>
                {quote.groups.map((qg) => (
                  <PairRow key={qg.channel_code} label={companyDisplayName(qg.company_code)} value={formatRupiah(qg.amount_payable)} />
                ))}
                <dt className={alloc.total_value_idr > 0 ? undefined : styles.grand}>{alloc.total_value_idr > 0 ? 'Total tagihan' : 'Total bayar'}</dt>
                <dd className={alloc.total_value_idr > 0 ? undefined : styles.grand}>{formatRupiah(quote.amount_payable_total)}</dd>
                {alloc.total_value_idr > 0 && (
                  <>
                    <PairRow label={`Dibayar dengan poin (${formatNumber(alloc.total_points)} poin)`} value={`−${formatRupiah(alloc.total_value_idr)}`} />
                    <dt className={styles.grand}>Bayar tunai</dt>
                    <dd className={styles.grand}>{formatRupiah(alloc.total_cash_idr)}</dd>
                  </>
                )}
              </dl>
              {redeemable && quote.points && (
                <PointsSection
                  points={quote.points}
                  groups={quote.groups}
                  inputs={pointsInputs}
                  alloc={alloc}
                  disabled={busy || paying}
                  onChange={(channel, value) => setPointsInputs((prev) => ({ ...prev, [channel]: value }))}
                />
              )}
              <fieldset className={styles.methodSet} disabled={busy || paying}>
                <legend>Metode pembayaran</legend>
                {options.map((o) => {
                  const id = `pay-${o.method}`;
                  const label = o.method === 'qris' ? 'QRIS (bayar di aplikasi)' : 'Bayar di kasir';
                  return (
                    <div key={o.method} className={styles.methodOption}>
                      <input
                        type="radio"
                        id={id}
                        name="payment_method"
                        value={o.method}
                        checked={method === o.method}
                        disabled={!o.enabled}
                        aria-describedby={o.reason ? `${id}-note` : undefined}
                        onChange={() => setChosenMethod(o.method)}
                      />
                      <label htmlFor={id}>{label}</label>
                      {o.reason && (
                        <p id={`${id}-note`} className="small muted" style={{ margin: '0.125rem 0 0 1.5rem' }}>
                          {o.reason}
                        </p>
                      )}
                    </div>
                  );
                })}
              </fieldset>
              {invoiceRequestAvailable(quote.groups.map((g) => g.company_code)) && (
                <InvoiceRequestForm
                  enabled={invoiceOn}
                  onToggle={(on) => {
                    if (on) setInvoiceOn(true);
                    else resetInvoice();
                  }}
                  value={invoiceForm}
                  errors={invoiceErrors}
                  onChange={(next) => {
                    setInvoiceForm(next);
                    if (Object.keys(invoiceErrors).length > 0) setInvoiceErrors({});
                  }}
                  disabled={busy || paying}
                />
              )}
              {method === null && (
                <p className="alert alert-error" role="alert">
                  Belum ada metode pembayaran yang dapat dipakai untuk keranjang ini. Pisahkan pesanan per PT, atau coba lagi nanti.
                </p>
              )}
              {method === 'counter' && (
                <p className="notice" role="note">
                  Setelah pesanan dibuat, Anda mendapat <strong>kode bayar</strong>. Tunjukkan kode itu ke kasir dan bayar di sana
                  (tunai, kartu, atau QRIS di kasir) sebelum batas waktu habis; stok ditahan selama itu. Kode ambil baru muncul setelah pembayaran diterima dan toko mengonfirmasi pesanan.
                </p>
              )}
              <p className="hint" style={{ marginBottom: 0 }}>
                Harga sudah termasuk pajak dan dihitung oleh sistem toko.{' '}
                {method === 'counter'
                  ? 'Batas waktu bayar (hitung mundur) ditampilkan di halaman pesanan setelah pesanan dibuat. Pesanan yang tidak dibayar sampai batas waktu dibatalkan otomatis.'
                  : 'Pembayaran lewat QRIS; batas waktu bayar (hitung mundur) ditampilkan di halaman pesanan setelah pesanan dibuat. Stok dicek lagi setelah pembayaran; bila habis, bagian itu dibatalkan dan dana dikembalikan.'}
              </p>
            </section>
          )}
          <div className={styles.checkoutBar}>
            <span className="small">
              {countItems(groups)} barang
              {quote && quoteHasIssues(quote) ? ' · pembayaran diblokir: perbaiki barang atau batas berat yang ditandai' : ''}
            </span>
            <button type="button" className="btn" disabled={blocked || busy || paying || method === null} onClick={() => method && void checkout(method)}>
              {paying
                ? 'Membuat pesanan…'
                : method === 'counter'
                  ? 'Buat pesanan & bayar di kasir'
                  : quote
                    ? `Bayar ${formatRupiah(alloc.total_value_idr > 0 ? alloc.total_cash_idr : quote.amount_payable_total)}`
                    : 'Bayar'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

interface PointsSectionProps {
  points: QuotePoints;
  groups: QuoteGroup[];
  inputs: Record<string, string>;
  alloc: Allocation;
  disabled: boolean;
  onChange: (channel: string, value: string) => void;
}

/**
 * Poin sebagai alat bayar per PT (docs/04 §14.C.1–§14.C.2): saldo tersedia + nilai, isian per kanal dengan batas dari quote,
 * dan porsi tunai per kanal. Poin BUKAN diskon (pajak tetap atas harga penuh). Validasi di sini hanya UX.
 */
function PointsSection({ points, groups, inputs, alloc, disabled, onChange }: PointsSectionProps) {
  return (
    <fieldset className={styles.methodSet} disabled={disabled}>
      <legend>Bayar dengan poin (opsional)</legend>
      <p className="small" style={{ margin: '0 0 0.25rem' }}>
        Saldo poin tersedia: <strong>{formatNumber(points.balance_available)} poin</strong> (1 poin = {formatRupiah(points.point_value_idr)}; setara{' '}
        {formatRupiah(points.balance_available * points.point_value_idr)}).
      </p>
      <p className="hint" style={{ marginTop: 0 }}>
        Poin dipakai sebagai alat bayar, bukan diskon: pajak tetap dihitung dari harga penuh. Setiap PT wajib menyisakan minimal Rp1 untuk dibayar
        tunai. Poin ditahan sampai pesanan dibayar atau kedaluwarsa.
      </p>
      {groups.map((g) => {
        const info = points.groups.find((x) => x.channel_code === g.channel_code);
        const company = companyDisplayName(g.company_code);
        if (!info || !info.redeem_allowed) {
          return (
            <p key={g.channel_code} className="small muted">
              {company}: poin tidak dapat dipakai untuk pesanan ini.
            </p>
          );
        }
        const line = alloc.lines.find((l) => l.channel_code === g.channel_code);
        const err = line?.error ? pointsErrorMessage(line.error, info) : null;
        const id = `poin-${g.channel_code}`;
        return (
          <div key={g.channel_code} className="field" style={{ marginTop: '0.5rem' }}>
            <label htmlFor={id}>Poin untuk {company}</label>
            <div className={styles.promoRow}>
              <input
                id={id}
                data-points-input=""
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                autoComplete="off"
                value={inputs[g.channel_code] ?? ''}
                aria-invalid={Boolean(err)}
                aria-describedby={err ? `${id}-hint ${id}-err` : `${id}-hint`}
                onChange={(e) => onChange(g.channel_code, e.target.value.replace(/\D/g, '').slice(0, 9))}
              />
              <button
                type="button"
                className="btn btn-secondary"
                aria-label={`Pakai poin maksimum untuk ${company}`}
                onClick={() => onChange(g.channel_code, String(suggestMaxPoints(points, groups, inputs, g.channel_code) || ''))}
              >
                Maks
              </button>
            </div>
            <span id={`${id}-hint`} className="hint">
              Minimal {formatNumber(info.min_points)}, maksimal {formatNumber(info.max_points)} poin ({formatRupiah(info.max_value_idr)}).
              {line && !err ? ` Dibayar poin ${formatRupiah(line.value_idr)}; sisa tunai ${formatRupiah(line.cash_idr)}.` : ''}
            </span>
            {err && (
              <p id={`${id}-err`} role="alert" className="alert alert-error" style={{ margin: 0 }}>
                {err}
              </p>
            )}
          </div>
        );
      })}
      {alloc.insufficient_balance && (
        <p role="alert" className="alert alert-error">
          {INSUFFICIENT_BALANCE_MESSAGE}
        </p>
      )}
    </fieldset>
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
