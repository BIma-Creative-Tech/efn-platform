'use client';

/**
 * Pengingat harga (G50; docs/04 §28.8): member menentukan harga target (bilangan bulat rupiah) di BAWAH harga saat ini.
 * Validasi klien hanya untuk UX; BFF yang memutuskan (harga sumber = katalog BFF). Batas 50 aktif, kedaluwarsa 90 hari.
 */
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { apiFetch, goToLogin } from '@/lib/cart-client';
import { isFeatureOff, parsePriceAlerts, parseTargetPrice, targetPriceError, type PriceAlert as PriceAlertItem } from '@/lib/engage';
import { formatRupiah } from '@/lib/format';
import { trackEvent } from '@/lib/tracking-client';
import { useEngage } from './EngageProvider';
import styles from './engage.module.css';

interface Props {
  channelCode: string;
  productId: number;
  productName: string;
  /** Harga yang ditampilkan di halaman produk (rupiah, bilangan bulat). */
  currentPrice: number;
  returnPath: string;
}

type Msg = { kind: 'ok' | 'error'; text: string } | null;

export function PriceAlert({ channelCode, productId, productName, currentPrice, returnPath }: Props) {
  const { features, ready, loggedIn } = useEngage();
  const enabled = ready && features.priceAlert;
  const [existing, setExisting] = useState<PriceAlertItem | null | 'loading' | 'off'>('loading');
  const [value, setValue] = useState('');
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  useEffect(() => {
    if (!enabled || !loggedIn) return;
    let alive = true;
    void apiFetch<{ items: unknown }>('/api/me/price-alerts').then((r) => {
      if (!alive) return;
      if (!r.ok) {
        setExisting(isFeatureOff(r.status, r.code) ? 'off' : null);
        return;
      }
      setExisting(parsePriceAlerts(r.data).find((a) => a.channel_code === channelCode && a.product_id === productId) ?? null);
    });
    return () => {
      alive = false;
    };
  }, [enabled, loggedIn, channelCode, productId]);

  if (!enabled || existing === 'off' || currentPrice <= 1) return null;

  if (!loggedIn) {
    return (
      <p className="hint" style={{ margin: 0 }}>
        <Link href={`/masuk?next=${encodeURIComponent(returnPath)}`}>Masuk</Link> untuk dikabari saat harga turun.
      </p>
    );
  }

  async function save() {
    const err = targetPriceError(value, currentPrice, formatRupiah);
    if (err) {
      setFieldError(err);
      document.getElementById('pa-target')?.focus();
      return;
    }
    const target = parseTargetPrice(value);
    if (target === null) return;
    setFieldError(null);
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch<unknown>('/api/me/price-alerts', {
        method: 'POST',
        body: JSON.stringify({ channel_code: channelCode, product_id: productId, target_price_idr: target }),
      });
      if (r.ok) {
        const now = new Date().toISOString();
        setExisting({ channel_code: channelCode, product_id: productId, target_price_idr: target, created_at: now, expires_at: null });
        setValue('');
        trackEvent({ type: 'price_alert_set', channelCode, productId });
        setMsg({ kind: 'ok', text: `Pengingat harga aktif: kami kabari Anda bila ${productName} turun ke ${formatRupiah(target)} atau kurang (berlaku 90 hari).` });
        return;
      }
      if (r.status === 401) {
        goToLogin(returnPath);
        return;
      }
      setMsg({ kind: 'error', text: r.message });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch<null>('/api/me/price-alerts', {
        method: 'DELETE',
        body: JSON.stringify({ channel_code: channelCode, product_id: productId }),
      });
      if (r.ok) {
        setExisting(null);
        setMsg({ kind: 'ok', text: 'Pengingat harga dihapus.' });
      } else {
        setMsg({ kind: 'error', text: r.message });
      }
    } finally {
      setBusy(false);
    }
  }

  const active = existing !== null && existing !== 'loading' && existing !== 'off' ? existing : null;

  return (
    <div className={styles.panel}>
      <h2 id="pa-title" style={{ margin: 0, fontSize: '1.0625rem' }}>
        Pengingat harga
      </h2>
      {active ? (
        <div className={styles.row}>
          <span>
            Aktif: dikabari bila harga turun ke <strong>{formatRupiah(active.target_price_idr)}</strong> atau kurang.
          </span>
          <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={() => void remove()}>
            Hapus pengingat
          </button>
        </div>
      ) : (
        <>
          <div className="field">
            <label htmlFor="pa-target">Harga target (Rp)</label>
            <input
              id="pa-target"
              className={styles.priceInput}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              value={value}
              disabled={busy || existing === 'loading'}
              aria-invalid={fieldError ? true : undefined}
              aria-describedby={`pa-hint${fieldError ? ' pa-error' : ''}`}
              onChange={(e) => {
                setValue(e.target.value);
                if (fieldError) setFieldError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  void save();
                }
              }}
            />
            <span id="pa-hint" className="hint">
              Harus lebih rendah dari harga saat ini ({formatRupiah(currentPrice)}). Contoh: {formatRupiah(Math.max(1, Math.floor(currentPrice * 0.9)))}.
            </span>
            {fieldError && (
              <span id="pa-error" role="alert" style={{ color: 'var(--color-danger)' }}>
                {fieldError}
              </span>
            )}
          </div>
          <div>
            <button type="button" className="btn btn-secondary" disabled={busy || existing === 'loading'} onClick={() => void save()}>
              {busy ? 'Menyimpan…' : 'Aktifkan pengingat harga'}
            </button>
          </div>
        </>
      )}
      <div role="status" aria-live="polite">
        {msg && <p className={`alert ${msg.kind === 'ok' ? 'alert-success' : 'alert-error'}`}>{msg.text}</p>}
      </div>
    </div>
  );
}
