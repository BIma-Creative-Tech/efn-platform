'use client';

/**
 * "Kabari saya bila tersedia" (restock alert; G50, docs/04 §28.8). Ditampilkan di produk yang habis pada outlet terpilih.
 * - Kanal yang bisa dipilih hanya yang tersedia di `features.channels`; email/WA butuh persetujuan info promo (BFF yang memutuskan).
 * - 409 `already_in_stock` -> beri tahu dan segarkan status stok (onAlreadyInStock). Batas 50 aktif ditangani lewat pesan.
 * - Bukan <form> (komponen ini berada di bawah form keranjang); tombol-tombol biasa.
 */
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { apiFetch, goToLogin } from '@/lib/cart-client';
import { isFeatureOff, NOTIFY_CHANNEL_LABEL, NOTIFY_CHANNELS, parseRestockAlerts, type NotifyChannel, type RestockAlert } from '@/lib/engage';
import { trackEvent } from '@/lib/tracking-client';
import { useEngage } from './EngageProvider';
import styles from './engage.module.css';

interface Props {
  channelCode: string;
  outletCode: string;
  outletName: string;
  productId: number;
  productName: string;
  onAlreadyInStock?: () => void;
  returnPath: string;
}

type Msg = { kind: 'ok' | 'error'; text: string } | null;

export function NotifyMe({ channelCode, outletCode, outletName, productId, productName, onAlreadyInStock, returnPath }: Props) {
  const { features, ready, loggedIn } = useEngage();
  const enabled = ready && features.restock;
  const [existing, setExisting] = useState<RestockAlert | { id: string | null } | null | 'loading' | 'off'>('loading');
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<NotifyChannel[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  useEffect(() => {
    if (!enabled || !loggedIn) return;
    let alive = true;
    void apiFetch<{ items: unknown }>('/api/me/restock-alerts').then((r) => {
      if (!alive) return;
      if (!r.ok) {
        setExisting(isFeatureOff(r.status, r.code) ? 'off' : null);
        return;
      }
      const hit = parseRestockAlerts(r.data).find((a) => a.channel_code === channelCode && a.outlet_code === outletCode && a.product_id === productId);
      setExisting(hit ?? null);
    });
    return () => {
      alive = false;
    };
  }, [enabled, loggedIn, channelCode, outletCode, productId]);

  if (!enabled || existing === 'off') return null;
  const available = NOTIFY_CHANNELS.filter((c) => features.channels[c]);
  if (available.length === 0) return null;

  if (!loggedIn) {
    return (
      <p className="hint" style={{ margin: 0 }}>
        <Link href={`/masuk?next=${encodeURIComponent(returnPath)}`}>Masuk</Link> untuk dikabari saat produk ini tersedia lagi di {outletName}.
      </p>
    );
  }

  const active = existing !== null && existing !== 'loading';
  const existingId = existing !== null && existing !== 'loading' ? existing.id : null;

  function toggle(c: NotifyChannel) {
    setSelected((s) => (s.includes(c) ? s.filter((x) => x !== c) : [...s, c]));
  }

  async function create() {
    if (selected.length === 0) {
      setMsg({ kind: 'error', text: 'Pilih minimal satu cara dikabari.' });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch<{ id: string | null }>('/api/me/restock-alerts', {
        method: 'POST',
        body: JSON.stringify({ channel_code: channelCode, outlet_code: outletCode, product_id: productId, channels: selected }),
      });
      if (r.ok) {
        setExisting({ id: r.data?.id ?? null });
        setOpen(false);
        trackEvent({ type: 'notify_me_request', channelCode, outletCode, productId });
        setMsg({ kind: 'ok', text: `Pengingat aktif. Kami kabari Anda saat ${productName} tersedia di ${outletName} (berlaku 30 hari atau sampai terkirim sekali).` });
        return;
      }
      if (r.status === 401) {
        goToLogin(returnPath);
        return;
      }
      if (r.code === 'already_in_stock') onAlreadyInStock?.();
      setMsg({ kind: 'error', text: r.message });
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (existing === null || existing === 'loading' || existing === 'off' || existing.id === null) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch<null>(`/api/me/restock-alerts/${encodeURIComponent(existing.id)}`, { method: 'DELETE' });
      if (r.ok) {
        setExisting(null);
        setMsg({ kind: 'ok', text: 'Pengingat dibatalkan.' });
      } else {
        setMsg({ kind: 'error', text: r.message });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`card ${styles.panel}`} style={{ marginTop: '1rem' }}>
      {active ? (
        <div className={styles.row}>
          <span>Pengingat stok aktif untuk {outletName}.</span>
          {existingId !== null && (
            <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => void cancel()}>
              Batalkan pengingat
            </button>
          )}
        </div>
      ) : !open ? (
        <div>
          <button type="button" className="btn btn-secondary" disabled={existing === 'loading'} onClick={() => setOpen(true)} aria-expanded={false}>
            Kabari saya bila tersedia
          </button>
        </div>
      ) : (
        <div className={styles.panel}>
          <fieldset>
            <legend>Kabari saya lewat ({outletName})</legend>
            {available.map((c) => (
              <div className="checkbox" key={c}>
                <input id={`nm-${c}`} type="checkbox" checked={selected.includes(c)} onChange={() => toggle(c)} />
                <label htmlFor={`nm-${c}`}>{NOTIFY_CHANNEL_LABEL[c]}</label>
              </div>
            ))}
          </fieldset>
          {(available.includes('email') || available.includes('wa_unofficial')) && (
            <p className="hint" style={{ margin: 0 }}>
              Email dan WhatsApp membutuhkan persetujuan info promo di <Link href="/akun/privasi">Pengaturan Privasi</Link>.
            </p>
          )}
          <div className={styles.row}>
            <button type="button" className="btn" disabled={busy} onClick={() => void create()}>
              {busy ? 'Menyimpan…' : 'Aktifkan pengingat'}
            </button>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setOpen(false)}>
              Batal
            </button>
          </div>
        </div>
      )}
      <div role="status" aria-live="polite">
        {msg && <p className={`alert ${msg.kind === 'ok' ? 'alert-success' : 'alert-error'}`}>{msg.text}</p>}
      </div>
    </div>
  );
}
