'use client';

/**
 * Kotak masuk & voucher (G50): pesan kampanye + kode voucher (tombol salin, tampilan besar untuk kasir).
 * Tersembunyi bila flag `campaign_inbox` mati atau BFF 404. Kode voucher tidak pernah di-log.
 */
import { useEffect, useState } from 'react';
import { apiFetch, goToLogin } from '@/lib/cart-client';
import { isFeatureOff } from '@/lib/engage';
import { INBOX_SEEN_KEY, isExpired, parseInbox, type InboxItem } from '@/lib/inbox';
import { useEngage } from './EngageProvider';
import styles from './engage.module.css';

type State = { status: 'loading' | 'off' | 'error'; items: InboxItem[]; now: number } | { status: 'ready'; items: InboxItem[]; now: number };

const dateFmt = new Intl.DateTimeFormat('id-ID', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Jakarta' });
function fmt(iso: string): string {
  return dateFmt.format(new Date(iso));
}

export function InboxView() {
  const { features, ready } = useEngage();
  const [state, setState] = useState<State>({ status: 'loading', items: [], now: 0 });
  const [copyMsg, setCopyMsg] = useState('');

  useEffect(() => {
    if (!ready || !features.campaignInbox) return;
    let active = true;
    void apiFetch<{ items: unknown }>('/api/me/inbox').then((r) => {
      if (!active) return;
      if (!r.ok) {
        if (r.status === 401) {
          goToLogin('/inbox');
          return;
        }
        setState({ status: isFeatureOff(r.status, r.code) ? 'off' : 'error', items: [], now: 0 });
        return;
      }
      const now = Date.now();
      const items = parseInbox(r.data).filter((i) => !isExpired(i, now));
      setState({ status: 'ready', items, now });
      try {
        window.localStorage.setItem(INBOX_SEEN_KEY, new Date(now).toISOString());
      } catch {
        // diabaikan
      }
    });
    return () => {
      active = false;
    };
  }, [ready, features.campaignInbox]);

  async function copy(item: InboxItem) {
    if (!item.voucher) return;
    try {
      await navigator.clipboard.writeText(item.voucher.code);
      setCopyMsg(`Kode voucher untuk "${item.title}" disalin.`);
    } catch {
      setCopyMsg('Kode tidak dapat disalin otomatis. Tunjukkan kode di layar ke kasir.');
    }
  }

  if (!ready) return <p className="muted">Memuat…</p>;
  if (!features.campaignInbox || state.status === 'off') {
    return <p className="notice">Kotak masuk dan voucher sedang tidak tersedia.</p>;
  }

  return (
    <div className="stack">
      <div role="status" aria-live="polite" className="visually-hidden">
        {copyMsg}
      </div>
      {copyMsg && (
        <p className="alert alert-success" aria-hidden="true">
          {copyMsg}
        </p>
      )}
      {state.status === 'loading' ? (
        <p className="muted">Memuat…</p>
      ) : state.status === 'error' ? (
        <p className="alert alert-error" role="alert">
          Kotak masuk belum dapat dimuat. Muat ulang halaman beberapa saat lagi.
        </p>
      ) : state.items.length === 0 ? (
        <p className="card">Belum ada pesan atau voucher untuk Anda saat ini.</p>
      ) : (
        <ul className={styles.list}>
          {state.items.map((i) => (
            <li key={i.id} className="card stack">
              <h2 style={{ margin: 0, fontSize: '1.125rem' }}>{i.title}</h2>
              <p style={{ margin: 0, whiteSpace: 'pre-line' }}>{i.body}</p>
              {i.voucher && (
                <div className={styles.voucher}>
                  <div style={{ flex: '1 1 12rem' }}>
                    <p className="small muted" style={{ margin: 0 }}>
                      Kode voucher (tunjukkan ke kasir)
                    </p>
                    <p className={styles.voucherCode} style={{ margin: 0 }} aria-label={`Kode voucher: ${i.voucher.code.split('').join(' ')}`}>
                      {i.voucher.code}
                    </p>
                  </div>
                  <button type="button" className="btn" onClick={() => void copy(i)}>
                    Salin kode<span className="visually-hidden">: {i.title}</span>
                  </button>
                </div>
              )}
              <p className="small muted" style={{ margin: 0 }}>
                Berlaku sampai {fmt(i.valid_to)} · diterima {fmt(i.created_at)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
