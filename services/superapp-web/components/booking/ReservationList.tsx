'use client';

/**
 * "Reservasi saya" (docs/04 §15.7): daftar ≤ 20 reservasi milik member dari BFF + batal (hanya requested/confirmed yang
 * belum mulai; server tetap memutuskan). Data awal dimuat di server (page), di sini hanya aksi batal + muat ulang.
 */
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { BOOKING_STATUS_LABEL, bookingErrorMessage, canCancelReservation, formatDateTimeIn, parseReservationList, type Reservation } from '@/lib/booking';
import styles from './booking.module.css';

export function ReservationList({ initial }: { initial: Reservation[] }) {
  // Waktu acuan tombol batal diambil sekali saat mount (server tetap memutuskan).
  const router = useRouter();
  const [nowMs] = useState(() => Date.now());
  const [items, setItems] = useState(initial);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function reload() {
    const res = await fetch('/api/booking/reservations', { credentials: 'same-origin', cache: 'no-store' }).catch(() => null);
    const body: unknown = res ? await res.json().catch(() => null) : null;
    const list = res?.ok ? parseReservationList(body) : null;
    if (list) setItems(list);
  }

  async function cancel(r: Reservation) {
    if (busy) return;
    if (!window.confirm('Batalkan reservasi ini?')) return;
    setBusy(r.booking_id);
    setMsg(null);
    try {
      const res = await fetch(`/api/booking/reservations/${r.booking_id}/cancel`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: '{}',
      });
      if (res.status === 401) {
        router.push(`/masuk?next=${encodeURIComponent('/reservasi/saya')}&expired=1`);
        return;
      }
      if (!res.ok) {
        const body: unknown = await res.json().catch(() => null);
        setMsg({ ok: false, text: bookingErrorMessage((body as { error?: { code?: string } } | null)?.error?.code) });
      } else {
        setMsg({ ok: true, text: 'Reservasi dibatalkan.' });
      }
      await reload();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="stack">
      <div aria-live="polite" role="status">
        {msg && <p className={`alert ${msg.ok ? 'alert-success' : 'alert-error'}`}>{msg.text}</p>}
      </div>
      <ul className={styles.list}>
        {items.map((r) => (
          <li key={r.booking_id} className="card" data-reservation={r.status}>
            <div className={styles.resHead}>
              <h2>{r.outlet_name || r.outlet_code}</h2>
              <span className="badge">{BOOKING_STATUS_LABEL[r.status]}</span>
            </div>
            <p style={{ margin: '0.375rem 0 0' }}>
              {formatDateTimeIn(r.start_at, 'Asia/Jakarta')} · {r.party_size} orang{r.table_label ? ` · ${r.table_label}` : ''}
            </p>
            {canCancelReservation(r, nowMs) && (
              <p style={{ margin: '0.75rem 0 0' }}>
                <button type="button" className="btn btn-secondary btn-small" disabled={busy !== null} onClick={() => void cancel(r)}>
                  {busy === r.booking_id ? 'Membatalkan…' : 'Batalkan'}
                </button>
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
