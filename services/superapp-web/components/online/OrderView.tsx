'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { apiFetch, goToLogin } from '@/lib/cart-client';
import { companyDisplayName } from '@/lib/catalog';
import { formatRupiah } from '@/lib/format';
import {
  canCancel,
  clockSkewMs,
  formatCountdown,
  hasPickupCode,
  orderStatusLabel,
  parseOrderDetail,
  partStatusLabel,
  pollIntervalMs,
  remainingSeconds,
  type OrderDetail,
  type OrderStatus,
} from '@/lib/orders';
import { OnlineUnavailable } from './OnlineUnavailable';
import { PickupCodePanel } from './PickupCodePanel';
import { QrCode } from './QrCode';
import styles from './online.module.css';

const MAX_POLLS = 400; // ± 20 menit pada 3 dtk; setelah itu pengguna memuat ulang manual.

const CANCEL_REASON: Record<string, string> = {
  payment_expired: 'Batas waktu pembayaran habis.',
  customer: 'Dibatalkan oleh Anda.',
  place_failed: 'Pesanan tidak dapat dibuat.',
  stock_short: 'Stok tidak mencukupi setelah pembayaran; dana bagian ini dikembalikan.',
  ops: 'Dibatalkan oleh toko; dana dikembalikan.',
  review: 'Dibatalkan setelah pemeriksaan; dana dikembalikan.',
};

function formatTime(iso: string | null): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  return new Intl.DateTimeFormat('id-ID', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Jakarta' }).format(t);
}

/**
 * Status pesanan + halaman bayar (QR lokal, hitung mundur 15 menit, batal sebelum bayar) + kode ambil.
 * Polling `/api/orders/:id`: 3 dtk saat menunggu bayar, 30 dtk sesudahnya, berhenti di status final (docs/04 §10.3.6).
 */
export function OrderView({ initial, simulator }: { initial: OrderDetail; simulator: boolean }) {
  const [order, setOrder] = useState<OrderDetail>(initial);
  const [now, setNow] = useState<number>(() => Date.now());
  const [skew, setSkew] = useState(0);
  const [gone, setGone] = useState<'unavailable' | 'not_found' | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [pollTick, setPollTick] = useState(0);
  const polls = useRef(0);
  const status = order.status as OrderStatus;

  // Jam untuk hitung mundur (1 dtk) — hanya saat menunggu bayar.
  useEffect(() => {
    if (status !== 'awaiting_payment') return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [status]);

  // Polling status.
  useEffect(() => {
    const interval = pollIntervalMs(status);
    if (interval === null || gone || polls.current >= MAX_POLLS) return;
    let active = true;
    const timer = setTimeout(async () => {
      if (document.visibilityState !== 'visible') {
        // Tab tersembunyi: lewati siklus ini, jadwalkan ulang.
        if (active) setPollTick((t) => t + 1);
        return;
      }
      polls.current += 1;
      const r = await apiFetch<unknown>(`/api/orders/${order.order_id}`);
      if (!active) return;
      if (!r.ok) {
        if (r.status === 401) return goToLogin(`/pesanan/${order.order_id}`);
        if (r.status === 404) return setGone('not_found');
        if (r.code === 'online_unavailable') return setGone('unavailable');
        setPollTick((t) => t + 1); // coba lagi pada siklus berikutnya
        return;
      }
      const next = parseOrderDetail(r.data);
      setSkew(clockSkewMs(r.date, Date.now()));
      if (next) setOrder(next);
      else setPollTick((t) => t + 1);
    }, interval);
    return () => {
      active = false;
      clearTimeout(timer);
    };
    // `pollTick` memicu siklus berikutnya bila satu siklus tidak mengubah `order`.
  }, [order, status, gone, pollTick]);

  async function refresh() {
    const r = await apiFetch<unknown>(`/api/orders/${order.order_id}`);
    if (r.ok) {
      const next = parseOrderDetail(r.data);
      if (next) setOrder(next);
    }
  }

  async function cancel() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch<unknown>(`/api/orders/${order.order_id}/cancel`, { method: 'POST', body: '{}' });
      if (!r.ok) {
        if (r.status === 401) return goToLogin(`/pesanan/${order.order_id}`);
        setMsg(r.message);
      }
      setConfirmCancel(false);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function simulate(s: 'paid' | 'expired' | 'failed') {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch<unknown>(`/api/orders/${order.order_id}/simulate-payment`, {
        method: 'POST',
        body: JSON.stringify({ status: s }),
      });
      if (!r.ok) setMsg(r.message);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  if (gone === 'unavailable') return <OnlineUnavailable />;
  if (gone === 'not_found') {
    return (
      <p className="alert alert-error" role="alert">
        Pesanan tidak ditemukan. <Link href="/pesanan">Lihat daftar pesanan</Link>
      </p>
    );
  }

  const awaiting = status === 'awaiting_payment';
  const expiresAt = order.payment?.expires_at ?? order.expires_at;
  const left = remainingSeconds(expiresAt, now, skew);
  const qr = awaiting ? order.payment?.qr_string : null;

  return (
    <div className="stack">
      <section className="card stack" aria-labelledby="status-judul">
        <div className={styles.groupHeader}>
          <h1 id="status-judul" style={{ margin: 0, fontSize: '1.375rem' }}>
            Pesanan
          </h1>
          <span className="badge" aria-live="polite">
            {orderStatusLabel(order.status)}
          </span>
        </div>
        <p className="small muted" style={{ margin: 0 }}>
          Dibuat {formatTime(order.created_at)}
          {order.paid_at ? ` · dibayar ${formatTime(order.paid_at)}` : ''}
        </p>
        <p style={{ margin: 0 }}>
          Total: <strong>{formatRupiah(order.amount_payable)}</strong>
        </p>
        {status === 'expired' && <p className="notice">Batas waktu pembayaran habis dan pesanan dibatalkan. Bila Anda sudah membayar, dana dikembalikan otomatis.</p>}
        {status === 'failed' && <p className="notice">Pesanan tidak dapat dibuat. Tidak ada tagihan. Silakan coba lagi dari keranjang.</p>}
        {status === 'review' && <p className="notice">Pembayaran Anda sedang diperiksa tim kami. Anda tidak perlu membayar ulang.</p>}
      </section>

      {awaiting && (
        <section className={`card ${styles.payBox}`} aria-labelledby="bayar-judul">
          <h2 id="bayar-judul" style={{ margin: 0, fontSize: '1.0625rem' }}>
            Bayar dengan QRIS
          </h2>
          {left > 0 ? (
            <>
              <p style={{ margin: 0 }}>Selesaikan pembayaran dalam</p>
              <p
                className={`${styles.countdown} ${left <= 60 ? styles.countdownUrgent : ''}`}
                style={{ margin: 0 }}
                role="timer"
                aria-live={left <= 60 ? 'polite' : 'off'}
                aria-label={`Sisa waktu ${Math.floor(left / 60)} menit ${left % 60} detik`}
              >
                {formatCountdown(left)}
              </p>
              {qr ? (
                <QrCode value={qr} className={styles.qr} label={`Kode QRIS pembayaran ${formatRupiah(order.amount_payable)}`} />
              ) : (
                <p className="muted">Menyiapkan kode QR…</p>
              )}
              <p className="small muted" style={{ margin: 0 }}>
                Pindai dengan aplikasi bank atau dompet digital. Status diperbarui otomatis setelah pembayaran diterima.
              </p>
            </>
          ) : (
            <p style={{ margin: 0 }}>Waktu pembayaran habis. Menunggu konfirmasi status…</p>
          )}

          {simulator && (
            <div className="notice stack" style={{ width: '100%', textAlign: 'left' }}>
              <p style={{ margin: 0 }}>
                <span className="badge badge-mock">MODE UJI</span> Provider pembayaran <code>mock</code> — simulasikan hasil:
              </p>
              <div className={styles.actions}>
                <button type="button" className="btn btn-small" disabled={busy} onClick={() => void simulate('paid')}>
                  Bayar (sukses)
                </button>
                <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => void simulate('expired')}>
                  Kedaluwarsa
                </button>
                <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => void simulate('failed')}>
                  Gagal
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      {canCancel(order) && (
        <section className="card stack">
          {confirmCancel ? (
            <>
              <p style={{ margin: 0 }}>Batalkan pesanan ini? Kode QR tidak bisa dipakai lagi setelah dibatalkan.</p>
              <div className={styles.actions}>
                <button type="button" className="btn btn-danger" disabled={busy} onClick={() => void cancel()}>
                  {busy ? 'Membatalkan…' : 'Ya, batalkan'}
                </button>
                <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setConfirmCancel(false)}>
                  Tidak
                </button>
              </div>
            </>
          ) : (
            <p style={{ margin: 0 }}>
              <button type="button" className="btn btn-secondary" onClick={() => setConfirmCancel(true)}>
                Batalkan pesanan
              </button>
            </p>
          )}
        </section>
      )}
      <div role="status" aria-live="polite">
        {msg && <p className="alert alert-error">{msg}</p>}
      </div>

      {hasPickupCode(order) && <PickupCodePanel orderId={order.order_id} />}

      {order.parts.map((p, i) => (
        <section key={`${p.channel_code}-${i}`} className={styles.group} aria-labelledby={`part-${i}`}>
          <div className={styles.groupHeader}>
            <h2 id={`part-${i}`}>{companyDisplayName(p.company_code)}</h2>
            <span className="badge">{partStatusLabel(p.status)}</span>
          </div>
          <p className="small muted" style={{ margin: '0.25rem 0 0' }}>
            Ambil di: {p.outlet_name || p.outlet_code}
            {p.sale_order_name ? ` · No. pesanan ${p.sale_order_name}` : ''}
            {p.ready_at ? ` · siap ${formatTime(p.ready_at)}` : ''}
            {p.handed_over_at ? ` · diambil ${formatTime(p.handed_over_at)}` : ''}
          </p>
          {p.cancel_reason && <p className="small" style={{ margin: '0.25rem 0 0' }}>{CANCEL_REASON[p.cancel_reason] ?? 'Dibatalkan.'}</p>}
          <ul className={styles.lines}>
            {p.lines.map((l) => (
              <li key={l.product_id} className={styles.line}>
                <span>
                  <span className={styles.lineName}>{l.name}</span>
                  <span className="small muted">
                    <br />
                    {l.qty} × {formatRupiah(l.price_unit_incl)}
                  </span>
                </span>
                <span className={styles.price}>{formatRupiah(l.subtotal_incl)}</span>
              </li>
            ))}
          </ul>
          <dl className={styles.totals}>
            <dt>Total (termasuk pajak)</dt>
            <dd className={styles.price}>{formatRupiah(p.amount_payable)}</dd>
            {p.tax_groups.map((t) => (
              <TaxRow key={t.label} label={t.label} amount={t.amount} />
            ))}
            {p.refunded_amount > 0 && (
              <>
                <dt>Dana dikembalikan</dt>
                <dd>{formatRupiah(p.refunded_amount)}</dd>
              </>
            )}
          </dl>
        </section>
      ))}

      <p>
        <Link href="/pesanan">Semua pesanan</Link> &middot; <Link href="/belanja">Belanja lagi</Link>
      </p>
    </div>
  );
}

function TaxRow({ label, amount }: { label: string; amount: number }) {
  return (
    <>
      <dt>Termasuk {label}</dt>
      <dd>{formatRupiah(amount)}</dd>
    </>
  );
}
