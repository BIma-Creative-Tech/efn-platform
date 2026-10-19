'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { LoginForm } from '@/app/masuk/LoginForm';
import { fnbErrorMessage, type FnbSession } from '@/lib/fnb';
import { fnbApi } from '@/lib/fnb-client';
import styles from './fnb.module.css';

interface Props {
  session: FnbSession;
  loggedIn: boolean;
  /** Dipanggil setelah klaim/batal berhasil (induk memuat ulang sesi). */
  onChanged: () => void;
}

/**
 * Klaim poin untuk meja ini (opsional; docs/04 §14.A.3). Tamu tanpa login tetap bisa memesan — panel ini hanya untuk
 * member (login OTP). Poin dikreditkan BFF setelah kasir menutup tagihan; tidak ada nominal di layar ini.
 *
 * Login dilakukan DI HALAMAN (LoginForm tertanam, `nextPath=/meja`) supaya token meja di memori tidak hilang: berpindah ke
 * `/masuk` memuat ulang dokumen dan menghapus token (token tidak disimpan di storage).
 */
export function PointsClaim({ session, loggedIn, onChanged }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const { claimable, claimed, claimed_by_me: mine } = session.points;

  if (!claimable && !claimed) return null;

  async function claim() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fnbApi<unknown>('/api/fnb/points-claim', { method: 'POST', body: '{}' });
      if (r.ok) {
        setMsg({ kind: 'success', text: 'Poin meja ini akan masuk ke akun Anda setelah tagihan dibayar di kasir.' });
        onChanged();
        return;
      }
      setMsg({ kind: 'error', text: fnbErrorMessage(r.code) });
      if (r.code === 'unauthenticated') router.refresh();
    } finally {
      setBusy(false);
    }
  }

  async function release() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fnbApi<unknown>('/api/fnb/points-claim', { method: 'DELETE' });
      if (r.ok) {
        setMsg({ kind: 'success', text: 'Klaim poin dibatalkan.' });
        onChanged();
        return;
      }
      setMsg({ kind: 'error', text: fnbErrorMessage(r.code) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={`card ${styles.pointsBox}`} aria-labelledby="poin-meja">
      <h2 id="poin-meja" style={{ margin: 0, fontSize: '1.0625rem' }}>
        Poin EFN (opsional)
      </h2>
      {claimed && !mine ? (
        <p style={{ margin: 0 }}>Poin untuk meja ini sudah diklaim oleh akun lain.</p>
      ) : mine ? (
        <>
          <p style={{ margin: 0 }}>Anda mengklaim poin meja ini. Poin masuk ke akun Anda setelah tagihan dibayar di kasir.</p>
          <p style={{ margin: 0 }}>
            <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => void release()}>
              Batalkan klaim
            </button>
          </p>
        </>
      ) : loggedIn ? (
        <>
          <p style={{ margin: 0 }}>
            Klaim poin agar belanja di meja ini tercatat di akun Anda. Satu meja hanya bisa diklaim satu akun, dan poin dihitung dari
            tagihan yang dibayar di kasir.
          </p>
          <p style={{ margin: 0 }}>
            <button type="button" className="btn" disabled={busy} onClick={() => void claim()}>
              {busy ? 'Memproses…' : 'Klaim poin meja ini'}
            </button>
          </p>
        </>
      ) : (
        <>
          <p style={{ margin: 0 }}>Ingin mengumpulkan poin? Masuk dengan nomor HP. Memesan tanpa masuk juga bisa.</p>
          <details className={styles.loginBox}>
            <summary>Masuk untuk klaim poin</summary>
            <div style={{ marginTop: '0.75rem' }}>
              <LoginForm nextPath="/meja" />
              <p className="small muted">Pesanan di keranjang tetap tersimpan selama halaman ini tidak dimuat ulang.</p>
            </div>
          </details>
        </>
      )}
      <div role="status" aria-live="polite">
        {msg && <p className={`alert ${msg.kind === 'success' ? 'alert-success' : 'alert-error'}`}>{msg.text}</p>}
      </div>
    </section>
  );
}
