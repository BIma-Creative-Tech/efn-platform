'use client';

/**
 * Pengaturan pelacakan (G50): (1) pilihan PERANGKAT, bisa diubah kapan saja; (2) consent AKUN `tracking` untuk member login
 * lewat tombol eksplisit (consent perangkat TIDAK otomatis menjadi consent akun; ADR-047 §3.1 no. 4). Teks = DRAFT DPO.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';
import Link from 'next/link';
import { apiFetch, goToLogin } from '@/lib/cart-client';
import { parseChoice, TRACKING_TEXT, VISIT_LOG_TEXT } from '@/lib/tracking';
import {
  choiceSnapshot,
  declineDeviceConsent,
  grantDeviceConsent,
  isTrackingActive,
  setLinked,
  subscribeTracking,
} from '@/lib/tracking-client';
import { useEngage } from './EngageProvider';
import styles from './engage.module.css';

type Msg = { kind: 'ok' | 'error'; text: string } | null;
interface AccountState {
  granted: boolean;
  version: string | null;
  updated_at: string | null;
}

export function TrackingSettings() {
  const { features, ready, loggedIn, disableTracking } = useEngage();
  const raw = useSyncExternalStore(subscribeTracking, choiceSnapshot, () => 'ssr');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [account, setAccount] = useState<AccountState | 'loading' | 'error'>('loading');

  useEffect(() => {
    if (!loggedIn || !features.tracking) return;
    let alive = true;
    void apiFetch<AccountState>('/api/me/tracking-consent').then((r) => {
      if (alive) setAccount(r.ok ? r.data : 'error');
    });
    return () => {
      alive = false;
    };
  }, [loggedIn, features.tracking]);

  if (!ready || raw === 'ssr') return <p className="muted">Memuat…</p>;
  if (!features.tracking) {
    return (
      <p className="notice" role="note">
        Pelacakan aktivitas sedang tidak aktif. Tidak ada aktivitas Anda yang dicatat.
      </p>
    );
  }

  const choice = parseChoice(raw || null);
  const deviceOn = choice?.granted === true && choice.version === TRACKING_TEXT.version;

  async function setDevice(grant: boolean) {
    setBusy(true);
    setMsg(null);
    try {
      const r = grant ? await grantDeviceConsent() : await declineDeviceConsent();
      if (r.ok) {
        setMsg({ kind: 'ok', text: grant ? 'Pelacakan di perangkat ini diaktifkan.' : 'Pelacakan di perangkat ini dimatikan.' });
      } else if (r.disabled) {
        disableTracking();
      } else {
        setMsg({ kind: 'error', text: r.message });
      }
    } finally {
      setBusy(false);
    }
  }

  async function setAccountConsent(grant: boolean) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch<AccountState>('/api/me/tracking-consent', {
        method: 'PUT',
        body: JSON.stringify({ granted: grant, version: TRACKING_TEXT.version }),
      });
      if (r.ok) {
        setAccount({ granted: grant, version: TRACKING_TEXT.version, updated_at: new Date().toISOString() });
        if (isTrackingActive()) setLinked(grant);
        setMsg({
          kind: 'ok',
          text: grant ? 'Aktivitas Anda kini dapat dikaitkan dengan akun.' : 'Kaitan aktivitas dengan akun dimatikan.',
        });
      } else if (r.status === 401) {
        goToLogin('/privasi/pelacakan');
      } else {
        setMsg({ kind: 'error', text: r.message });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div role="status" aria-live="polite">
        {msg && <p className={`alert ${msg.kind === 'ok' ? 'alert-success' : 'alert-error'}`}>{msg.text}</p>}
      </div>

      {features.visitLog && (
        <section className="card stack" aria-labelledby="trk-anon-title">
          <h2 id="trk-anon-title" style={{ margin: 0, fontSize: '1.125rem' }}>
            Kunjungan anonim
          </h2>
          <p style={{ margin: 0 }}>{VISIT_LOG_TEXT.notice}</p>
          <p className="hint" style={{ margin: 0 }}>
            Rincian dan masa simpan ada di <Link href="/privasi">Kebijakan Privasi</Link>.
          </p>
        </section>
      )}

      <section className="card stack" aria-labelledby="trk-device-title">
        <h2 id="trk-device-title" style={{ margin: 0, fontSize: '1.125rem' }}>
          Perangkat ini
        </h2>
        <p style={{ margin: 0 }} id="trk-device-body">
          {TRACKING_TEXT.body}
        </p>
        <p className="hint" style={{ margin: 0 }} id="trk-device-status">
          Status: <strong>{deviceOn ? 'Aktif' : 'Tidak aktif'}</strong> · versi teks {TRACKING_TEXT.version}. {!deviceOn && (features.visitLog ? VISIT_LOG_TEXT.whenOff : TRACKING_TEXT.whenOff)}
        </p>
        <div className={styles.row}>
          <button type="button" className="btn" disabled={busy || deviceOn} aria-describedby="trk-device-status" onClick={() => void setDevice(true)}>
            Izinkan di perangkat ini
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy || !deviceOn}
            aria-describedby="trk-device-status"
            onClick={() => void setDevice(false)}
          >
            Matikan di perangkat ini
          </button>
        </div>
      </section>

      <section className="card stack" aria-labelledby="trk-account-title">
        <h2 id="trk-account-title" style={{ margin: 0, fontSize: '1.125rem' }}>
          Akun Anda
        </h2>
        {!loggedIn ? (
          <p style={{ margin: 0 }}>
            <Link href="/masuk?next=/privasi/pelacakan">Masuk</Link> bila Anda ingin mengaitkan aktivitas dengan akun. Tanpa itu, aktivitas hanya
            tercatat atas kode perangkat.
          </p>
        ) : (
          <>
            <p style={{ margin: 0 }} id="trk-account-body">
              Secara bawaan aktivitas <strong>tidak</strong> dikaitkan dengan akun Anda, meskipun pelacakan di perangkat aktif. Aktifkan hanya bila Anda
              setuju aktivitas dikaitkan dengan akun agar penawaran lebih relevan.
            </p>
            <p className="hint" style={{ margin: 0 }} id="trk-account-status">
              {account === 'loading'
                ? 'Memuat status…'
                : account === 'error'
                  ? 'Status akun belum dapat dimuat.'
                  : `Status: ${account.granted ? 'Aktif' : 'Tidak aktif'}`}
            </p>
            <div className={styles.row}>
              <button
                type="button"
                className="btn"
                disabled={busy || account === 'loading' || account === 'error' || account.granted}
                aria-describedby="trk-account-body trk-account-status"
                onClick={() => void setAccountConsent(true)}
              >
                Kaitkan aktivitas dengan akun saya
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy || account === 'loading' || account === 'error' || !account.granted}
                aria-describedby="trk-account-body trk-account-status"
                onClick={() => void setAccountConsent(false)}
              >
                Hentikan pengaitan
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
