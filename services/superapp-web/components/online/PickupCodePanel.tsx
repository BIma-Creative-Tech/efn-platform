'use client';

import { useEffect, useState } from 'react';
import { apiFetch, goToLogin } from '@/lib/cart-client';
import { parsePickupCodes, type PickupCodeItem } from '@/lib/orders';
import styles from './online.module.css';

const AUTO_HIDE_MS = 120_000;

/**
 * Layar kode ambil (docs/04 §10.3.6 — kode = RAHASIA).
 * - Kode hanya diambil saat pengguna menekan tombol, disimpan di memori komponen (bukan URL, storage, atau log),
 *   dan disembunyikan otomatis setelah 2 menit atau saat tab disembunyikan.
 * - Respons Route Handler `no-store`; tidak ada analytics di aplikasi ini.
 */
export function PickupCodePanel({ orderId }: { orderId: string }) {
  const [codes, setCodes] = useState<PickupCodeItem[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!codes) return;
    const timer = setTimeout(() => setCodes(null), AUTO_HIDE_MS);
    const onHide = () => {
      if (document.visibilityState === 'hidden') setCodes(null);
    };
    document.addEventListener('visibilitychange', onHide);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onHide);
    };
  }, [codes]);

  async function reveal() {
    setBusy(true);
    setError(null);
    try {
      const r = await apiFetch<unknown>(`/api/orders/${orderId}/pickup-code`);
      if (!r.ok) {
        if (r.status === 401) return goToLogin(`/pesanan/${orderId}`);
        setError(r.message);
        return;
      }
      const items = parsePickupCodes(r.data);
      if (!items || items.length === 0) {
        setError('Kode ambil belum tersedia.');
        return;
      }
      setCodes(items);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack" aria-labelledby="kode-ambil">
      <h2 id="kode-ambil" style={{ margin: 0, fontSize: '1.0625rem' }}>
        Kode ambil
      </h2>
      <p className="small" style={{ margin: 0 }}>
        Tunjukkan kode ini ke kasir saat mengambil pesanan. <strong>Jangan bagikan kode ke siapa pun</strong> — petugas kami
        tidak pernah meminta kode lewat telepon atau chat.
      </p>
      {codes ? (
        <>
          {codes.map((c) => (
            <div key={`${c.channel_code}-${c.outlet_code}`} className="stack">
              <p className="small muted" style={{ margin: 0 }}>
                {c.outlet_name || c.outlet_code}
              </p>
              <p className={styles.pickupCode} aria-label={`Kode ambil ${c.pickup_code.split('').join(' ')}`}>
                {c.pickup_code}
              </p>
            </div>
          ))}
          <p style={{ margin: 0 }}>
            <button type="button" className="btn btn-secondary" onClick={() => setCodes(null)}>
              Sembunyikan kode
            </button>
          </p>
        </>
      ) : (
        <p style={{ margin: 0 }}>
          <button type="button" className="btn" onClick={() => void reveal()} disabled={busy}>
            {busy ? 'Memuat…' : 'Tampilkan kode ambil'}
          </button>
        </p>
      )}
      <div role="status" aria-live="polite">
        {error && <p className="alert alert-error">{error}</p>}
      </div>
    </section>
  );
}
