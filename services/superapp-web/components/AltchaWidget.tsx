'use client';

/**
 * Widget ALTCHA (paket npm `altcha`, MIT, self-hosted) — docs/04 §8.1, SEC-06.
 *
 * - Skrip berasal dari bundle aplikasi sendiri (dynamic import -> chunk `/_next/static`), BUKAN CDN:
 *   CSP `script-src 'self'` tetap. Challenge diambil dari `/api/auth/challenge` (origin sendiri,
 *   `connect-src 'self'`). Worker proof-of-work dibuat widget dari blob (`worker-src 'self' blob:` sudah ada).
 * - Elemen `<altcha-widget>` dibuat imperatif (bukan JSX) agar tidak bergantung pada deklarasi tipe
 *   custom element React 19 dan tidak dirender saat SSR (modul altcha butuh `HTMLElement`/`customElements`).
 * - Payload hanya diteruskan ke parent lewat `onPayload`; parent mengirimnya bersama `phone`.
 *   Untuk challenge baru (sekali pakai), parent me-remount komponen ini dengan `key` baru.
 */
import { useEffect, useRef, useState } from 'react';

type AltchaState = 'unverified' | 'verifying' | 'verified' | 'expired' | 'error';

interface StateChangeDetail {
  state?: AltchaState;
  payload?: string;
}

const STRINGS_ID = JSON.stringify({
  ariaLinkLabel: 'Kunjungi altcha.org',
  error: 'Verifikasi gagal. Coba lagi.',
  expired: 'Verifikasi kedaluwarsa. Coba lagi.',
  footer: '',
  label: 'Saya bukan robot',
  verified: 'Terverifikasi',
  verifying: 'Memverifikasi…',
  waitAlert: 'Sedang memverifikasi… mohon tunggu.',
});

export function AltchaWidget({
  onPayload,
  challengeUrl = '/api/auth/challenge',
}: {
  /** Dipanggil dengan payload base64 saat terverifikasi, atau null saat belum/kedaluwarsa/gagal. */
  onPayload: (payload: string | null) => void;
  challengeUrl?: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const onPayloadRef = useRef(onPayload);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    onPayloadRef.current = onPayload;
  }, [onPayload]);

  useEffect(() => {
    let cancelled = false;
    let el: HTMLElement | null = null;
    const listener = (ev: Event) => {
      const detail = (ev as CustomEvent<StateChangeDetail>).detail ?? {};
      if (detail.state === 'verified' && typeof detail.payload === 'string') onPayloadRef.current(detail.payload);
      else onPayloadRef.current(null);
    };

    import('altcha')
      .then(() => {
        if (cancelled || !hostRef.current) return;
        el = document.createElement('altcha-widget');
        el.setAttribute('challengeurl', challengeUrl);
        el.setAttribute('name', 'altcha');
        // Mulai menghitung proof-of-work saat pengguna fokus ke form (bukan saat halaman dimuat).
        el.setAttribute('auto', 'onfocus');
        el.setAttribute('hidefooter', '');
        el.setAttribute('strings', STRINGS_ID);
        el.addEventListener('statechange', listener);
        hostRef.current.appendChild(el);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });

    return () => {
      cancelled = true;
      if (el) {
        el.removeEventListener('statechange', listener);
        el.remove();
      }
      onPayloadRef.current(null);
    };
  }, [challengeUrl]);

  return (
    <div className="field">
      <div ref={hostRef} />
      {loadError && (
        <p className="hint" role="alert">
          Verifikasi keamanan gagal dimuat. Muat ulang halaman lalu coba lagi.
        </p>
      )}
    </div>
  );
}
