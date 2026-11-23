'use client';

/**
 * Konteks G50: flag fitur dari `GET /api/config` (-> BFF `/v1/config`), status login, dan pengumuman status
 * (live region + toast yang bisa ditutup). Semua fitur tersembunyi sampai flag `true` diterima; kegagalan memuat = fitur mati.
 * Juga menjalankan pengirim event (flush periodik, visibilitychange/pagehide) hanya bila tracking aktif.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import Link from 'next/link';
import { apiFetch } from '@/lib/cart-client';
import { coerceFeatures, NO_FEATURES, type Features } from '@/lib/tracking';
import { choiceSnapshot, isTrackingActive, setLinked, setVisitLog, startFlushing, subscribeTracking } from '@/lib/tracking-client';
import styles from './engage.module.css';

interface Ctx {
  features: Features;
  ready: boolean;
  loggedIn: boolean;
  /** Umumkan hasil aksi (live region + toast). `href` opsional untuk tautan lanjutan. */
  announce: (text: string, link?: { href: string; label: string }) => void;
  /** Server menyatakan fitur tracking mati (202 disabled): sembunyikan banner & berhenti. */
  disableTracking: () => void;
}

const EngageContext = createContext<Ctx>({
  features: NO_FEATURES,
  ready: false,
  loggedIn: false,
  announce: () => undefined,
  disableTracking: () => undefined,
});

export function useEngage(): Ctx {
  return useContext(EngageContext);
}

export function EngageProvider({ loggedIn, children }: { loggedIn: boolean; children: ReactNode }) {
  const [features, setFeatures] = useState<Features>(NO_FEATURES);
  const [ready, setReady] = useState(false);
  const [toast, setToast] = useState<{ text: string; link?: { href: string; label: string } } | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let active = true;
    // Satu panggilan per muat penuh halaman (provider hidup di layout, tidak dipasang ulang saat navigasi klien).
    void apiFetch<{ features?: unknown }>('/api/config').then((r) => {
      if (!active) return;
      const f = r.ok ? coerceFeatures(r.data?.features) : NO_FEATURES;
      setVisitLog(f.visitLog); // sebelum render ulang: pencatat anak langsung boleh mengirim event anonim
      setFeatures(f);
      setReady(true);
    });
    return () => {
      active = false;
    };
  }, []);

  const announce = useCallback((text: string, link?: { href: string; label: string }) => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    setToast({ text, ...(link ? { link } : {}) });
    timer.current = window.setTimeout(() => setToast(null), 8000);
  }, []);

  const disableTracking = useCallback(() => {
    setVisitLog(false); // visit_log efektif hanya bila tracking aktif di server
    setFeatures((f) => ({ ...f, tracking: false, visitLog: false }));
  }, []);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const value = useMemo<Ctx>(() => ({ features, ready, loggedIn, announce, disableTracking }), [features, ready, loggedIn, announce, disableTracking]);

  return (
    <EngageContext.Provider value={value}>
      <TrackingRuntime />
      {children}
      {/* Live region selalu ada agar pembaca layar menangkap perubahan; tampil sebagai toast hanya saat ada pesan. */}
      <div role="status" aria-live="polite" className={toast ? styles.toast : 'visually-hidden'}>
        {toast && (
          <>
            <span>{toast.text}</span>
            {toast.link && <Link href={toast.link.href}>{toast.link.label}</Link>}
            <button type="button" className="btn btn-secondary btn-small" onClick={() => setToast(null)}>
              Tutup
            </button>
          </>
        )}
      </div>
    </EngageContext.Provider>
  );
}

/**
 * Pengirim event: berjalan bila perangkat sudah setuju (flag `tracking`) ATAU log kunjungan anonim menyala (`visit_log`, V-01).
 * Penautan akun hanya bila consent aktif. Tidak merender apa pun.
 */
function TrackingRuntime() {
  const { features, loggedIn } = useEngage();
  const choice = useSyncExternalStore(subscribeTracking, choiceSnapshot, () => 'ssr');
  const active = features.tracking && choice !== 'ssr' && isTrackingActive();
  const flushing = active || features.visitLog;

  useEffect(() => {
    if (!flushing) return;
    return startFlushing();
  }, [flushing]);

  // Tautan akun: hanya bila member SUDAH mengaktifkan consent akun `tracking` (tombol eksplisit di pengaturan).
  useEffect(() => {
    if (!active || !loggedIn) {
      setLinked(false);
      return;
    }
    let alive = true;
    void apiFetch<{ granted?: unknown }>('/api/me/tracking-consent').then((r) => {
      if (alive) setLinked(r.ok && r.data?.granted === true);
    });
    return () => {
      alive = false;
    };
  }, [active, loggedIn]);

  return null;
}
