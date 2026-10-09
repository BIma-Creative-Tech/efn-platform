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
 *
 * altcha v3 (Gelombang 16, GHSA-mpmc-qchh-r9q8): atribut `challengeurl` -> `challenge`, `strings` dihapus
 * (teks lewat registry global `$altcha.i18n` + `language`), opsi lain lewat atribut JSON `configuration`.
 * Widget v3 tetap menerima challenge format v1 dari BFF (`{algorithm, challenge, salt, signature}`) dan
 * mengirim payload v1 (`base64({algorithm, challenge, number, salt, signature, took})`) — kontrak BFF
 * dan `lib/altcha.ts` tidak berubah. Event `statechange` tetap `{ state, payload }`.
 * `humanInteractionSignature: false`: widget tidak mengumpulkan event pointer/scroll (minimisasi data, UU PDP).
 */
import { useEffect, useRef, useState } from 'react';

type AltchaState = 'unverified' | 'verifying' | 'verified' | 'expired' | 'error' | 'code';

interface StateChangeDetail {
  state?: AltchaState;
  payload?: string;
}

/** Bagian registry global yang didaftarkan modul `altcha` v3 (lihat altcha/types `AltchaGlobal`). */
interface AltchaI18nRegistry {
  i18n: {
    get: (lang: string) => Record<string, string> | undefined;
    set: (lang: string, strings: Record<string, string>) => void;
  };
}

const LANGUAGE = 'id';

const STRINGS_ID: Record<string, string> = {
  ariaLinkLabel: 'Kunjungi altcha.org',
  cancel: 'Batal',
  error: 'Verifikasi gagal. Coba lagi.',
  expired: 'Verifikasi kedaluwarsa. Coba lagi.',
  footer: '',
  label: 'Saya bukan robot',
  loading: 'Memuat…',
  reload: 'Muat ulang',
  verify: 'Verifikasi',
  verificationRequired: 'Verifikasi diperlukan.',
  verified: 'Terverifikasi',
  verifying: 'Memverifikasi…',
  waitAlert: 'Sedang memverifikasi… mohon tunggu.',
};

const WIDGET_CONFIGURATION = JSON.stringify({
  hideFooter: true,
  humanInteractionSignature: false,
});

/** Daftarkan teks Bahasa Indonesia (di atas default `en` agar kunci yang tidak diterjemahkan tetap ada). */
function registerIndonesianStrings(): void {
  const registry = (globalThis as unknown as { $altcha?: AltchaI18nRegistry }).$altcha;
  if (!registry) return;
  registry.i18n.set(LANGUAGE, { ...(registry.i18n.get('en') ?? {}), ...STRINGS_ID });
}

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
        registerIndonesianStrings();
        el = document.createElement('altcha-widget');
        el.setAttribute('challenge', challengeUrl);
        el.setAttribute('name', 'altcha');
        el.setAttribute('language', LANGUAGE);
        // Mulai menghitung proof-of-work saat pengguna fokus ke form (bukan saat halaman dimuat).
        el.setAttribute('auto', 'onfocus');
        el.setAttribute('configuration', WIDGET_CONFIGURATION);
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
