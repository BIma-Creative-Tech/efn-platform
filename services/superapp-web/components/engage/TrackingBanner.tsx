'use client';

/**
 * Banner consent tracking (G50; ADR-047 §3.1 no. 2). Tampil hanya bila `features.tracking` true dan perangkat belum memilih
 * (atau memilih atas versi teks lama). "Izinkan" dan "Tolak" setara (ukuran, bobot, urutan netral). Ada di alur dokumen
 * (bukan menutupi konten) agar fokus keyboard tidak tertutup. Teks = DRAFT, MENUNGGU DPO.
 */
import { useState, useSyncExternalStore } from 'react';
import Link from 'next/link';
import { needsDecision, parseChoice, TRACKING_TEXT } from '@/lib/tracking';
import { choiceSnapshot, declineDeviceConsent, grantDeviceConsent, subscribeTracking } from '@/lib/tracking-client';
import { useEngage } from './EngageProvider';
import styles from './engage.module.css';

export function TrackingBanner() {
  const { features, ready, disableTracking } = useEngage();
  const raw = useSyncExternalStore(subscribeTracking, choiceSnapshot, () => 'ssr');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const visible = ready && features.tracking && raw !== 'ssr' && needsDecision(parseChoice(raw || null));
  if (!visible) return null;

  async function choose(grant: boolean) {
    setBusy(true);
    setError(null);
    try {
      const r = grant ? await grantDeviceConsent() : await declineDeviceConsent();
      if (!r.ok) {
        if (r.disabled) disableTracking();
        else setError(r.message);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.banner} role="region" aria-labelledby="trk-title" aria-describedby="trk-body">
      <div className={styles.bannerInner}>
        <h2 id="trk-title" className={styles.bannerTitle}>
          {TRACKING_TEXT.title}
        </h2>
        <p id="trk-body" className={styles.bannerText}>
          {TRACKING_TEXT.body}
        </p>
        <p className={styles.draft}>
          <strong>DRAFT</strong>: teks menunggu tinjauan DPO/Legal. Rincian ada di <Link href="/privasi">Kebijakan Privasi</Link>. Pilihan ini
          bisa diubah kapan saja di <Link href="/privasi/pelacakan">pengaturan pelacakan</Link>.
        </p>
        <div className={styles.choices}>
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void choose(true)}>
            Izinkan pelacakan
          </button>
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void choose(false)}>
            Tolak pelacakan
          </button>
        </div>
        <div role="status">{error && <p className="alert alert-error">{error}</p>}</div>
      </div>
    </section>
  );
}
