'use client';

/**
 * R6: memuat status hapus akun dari `/api/dsar/requests/:id` (cookie HttpOnly `efn_dsar_receipt` dikirim otomatis
 * oleh peramban; JavaScript tidak pernah melihat token). Tombol batalkan hanya bila `sessionCanCancel` (dicek server)
 * DAN status terbaru `scheduled`.
 */
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  canCancelDeletion,
  deletionStatusDescription,
  deletionStatusLabel,
  type DeletionStatusView,
} from '@/lib/dsar-receipt';
import { formatDateId } from '@/lib/dsar';

type Load =
  | { kind: 'loading' }
  | { kind: 'ok'; view: DeletionStatusView }
  | { kind: 'error'; text: string };

type Msg = { kind: 'ok' | 'error'; text: string } | null;

async function errorMessage(res: Response, fallback: string): Promise<string> {
  const data = (await res.json().catch(() => null)) as { error?: { message?: string } } | null;
  return data?.error?.message ?? fallback;
}

export function DeletionStatus({
  id,
  sessionCanCancel,
  signedIn,
  deferredUntil = null,
}: {
  id: string;
  sessionCanCancel: boolean;
  signedIn: boolean;
  /** G18 W-18: batas paling lambat erasure karena ada pesanan online terbuka (dari respons 202; hanya tampilan). */
  deferredUntil?: string | null;
}) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [busy, setBusy] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  /** Ambil status terbaru; tidak menyentuh state (dipakai effect & handler). */
  const loadStatus = useCallback(async (): Promise<Load> => {
    try {
      const res = await fetch(`/api/dsar/requests/${encodeURIComponent(id)}`, { cache: 'no-store' });
      if (res.ok) return { kind: 'ok', view: (await res.json()) as DeletionStatusView };
      return { kind: 'error', text: await errorMessage(res, 'Status belum dapat dimuat. Silakan coba lagi.') };
    } catch {
      return { kind: 'error', text: 'Koneksi bermasalah. Periksa internet Anda lalu coba lagi.' };
    }
  }, [id]);

  /** Muat ulang dari handler (klik/setelah batal): tampilkan "memuat" lalu hasil. */
  const fetchStatus = useCallback(async () => {
    setLoad({ kind: 'loading' });
    setLoad(await loadStatus());
  }, [loadStatus]);

  // Muat awal: state awal sudah `loading`, jadi setState hanya di callback async (react-hooks/set-state-in-effect).
  // `active` mencegah update setelah unmount / pergantian `id`.
  useEffect(() => {
    let active = true;
    void loadStatus().then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
    };
  }, [loadStatus]);

  async function cancelDeletion() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch('/api/me/delete/cancel', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
      if (res.ok) {
        setCancelled(true);
        setMsg({ kind: 'ok', text: 'Permintaan hapus akun dibatalkan. Akun Anda tetap aktif.' });
        await fetchStatus();
      } else if (res.status === 401) {
        window.location.assign(`/masuk?next=${encodeURIComponent(`/akun/penghapusan/${id}`)}&expired=1`);
      } else {
        setMsg({ kind: 'error', text: await errorMessage(res, 'Pembatalan belum dapat diproses.') });
      }
    } catch {
      setMsg({ kind: 'error', text: 'Koneksi bermasalah. Periksa internet Anda lalu coba lagi.' });
    } finally {
      setBusy(false);
    }
  }

  const loginHref = `/masuk?next=${encodeURIComponent(`/akun/penghapusan/${id}`)}`;

  return (
    <section className="card stack" aria-live="polite" aria-busy={load.kind === 'loading'}>
      {load.kind === 'loading' && <p style={{ margin: 0 }}>Memuat status&hellip;</p>}

      {load.kind === 'error' && (
        <>
          <p className="alert alert-error" role="alert" style={{ margin: 0 }}>
            {load.text}
          </p>
          <p style={{ margin: 0 }}>
            <button type="button" className="btn btn-secondary" onClick={() => void fetchStatus()}>
              Coba lagi
            </button>
          </p>
          {signedIn && (
            <p style={{ margin: 0 }}>
              Anda sedang masuk: riwayat permintaan juga tersedia di <Link href="/akun/data-saya">Data Saya</Link>.
            </p>
          )}
        </>
      )}

      {load.kind === 'ok' && (
        <>
          <p style={{ margin: 0 }}>
            Status: <strong>{deletionStatusLabel(load.view.status)}</strong>
          </p>
          <p style={{ margin: 0 }}>{deletionStatusDescription(load.view.status)}</p>
          <dl style={{ margin: 0 }}>
            <dt className="muted small">Diajukan</dt>
            <dd style={{ marginLeft: 0 }}>{formatDateId(load.view.requested_at)}</dd>
            <dt className="muted small">Jadwal penghapusan</dt>
            <dd style={{ marginLeft: 0 }}>{formatDateId(load.view.scheduled_at)}</dd>
            {deferredUntil && !load.view.completed_at && (
              <>
                <dt className="muted small">Paling lambat dihapus (ada pesanan online belum selesai)</dt>
                <dd style={{ marginLeft: 0 }}>{formatDateId(deferredUntil)}</dd>
              </>
            )}
            {load.view.completed_at && (
              <>
                <dt className="muted small">Selesai</dt>
                <dd style={{ marginLeft: 0 }}>{formatDateId(load.view.completed_at)}</dd>
              </>
            )}
          </dl>

          {msg && (
            <p className={`alert ${msg.kind === 'ok' ? 'alert-success' : 'alert-error'}`} role="status" style={{ margin: 0 }}>
              {msg.text}
            </p>
          )}

          {!cancelled && canCancelDeletion(load.view, sessionCanCancel) && (
            <p style={{ margin: 0 }}>
              <button type="button" className="btn" onClick={() => void cancelDeletion()} disabled={busy}>
                {busy ? 'Membatalkan…' : 'Batalkan penghapusan'}
              </button>
            </p>
          )}
          {load.view.status === 'scheduled' && !sessionCanCancel && (
            <p style={{ margin: 0 }}>
              Berubah pikiran? <Link href={loginHref}>Masuk kembali</Link> dengan nomor HP akun ini sebelum tanggal
              penghapusan untuk membatalkan.
            </p>
          )}
          <p style={{ margin: 0 }}>
            <button type="button" className="btn btn-secondary btn-small" onClick={() => void fetchStatus()}>
              Perbarui status
            </button>
          </p>
        </>
      )}
    </section>
  );
}
