'use client';

/**
 * Ikon hati wishlist (G50; docs/04 §28.8). Tersembunyi bila `features.wishlist` mati atau endpoint 404/501.
 * Tamu: klik -> arahkan masuk (wishlist butuh akun). Tombol toggle dengan `aria-pressed`; hasil diumumkan lewat live region.
 * Wishlist = permintaan member (tidak butuh consent tracking); event `wishlist_add/remove` hanya terkirim bila consent aktif.
 */
import { useEffect, useState } from 'react';
import { goToLogin } from '@/lib/cart-client';
import { trackEvent } from '@/lib/tracking-client';
import { useEngage } from './EngageProvider';
import { loadWishlist, setWishlisted, useWishlistState } from './wishlist-store';
import styles from './engage.module.css';

interface Props {
  channelCode: string;
  productId: number;
  name: string;
  variant: 'card' | 'detail';
  /** Path kembali setelah masuk (tamu). */
  returnPath?: string;
}

function Heart() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 21s-7.5-4.6-9.6-9.2C.9 8.4 2.7 5 6 5c2 0 3.4 1.1 4 2.2h4C14.6 6.1 16 5 18 5c3.3 0 5.1 3.4 3.6 6.8C19.5 16.4 12 21 12 21z" strokeLinejoin="round" />
    </svg>
  );
}

export function WishlistButton({ channelCode, productId, name, variant, returnPath }: Props) {
  const { features, ready, loggedIn, announce } = useEngage();
  const state = useWishlistState(channelCode);
  const [busy, setBusy] = useState(false);
  const enabled = ready && features.wishlist;

  useEffect(() => {
    if (enabled && loggedIn) void loadWishlist(channelCode);
  }, [enabled, loggedIn, channelCode]);

  if (!enabled || (loggedIn && state.status === 'off')) return null;
  const pressed = loggedIn && state.ids.has(productId);

  async function onClick() {
    if (!loggedIn) {
      goToLogin(returnPath ?? `${window.location.pathname}${window.location.search}`);
      return;
    }
    setBusy(true);
    try {
      const want = !pressed;
      const r = await setWishlisted(channelCode, productId, want);
      if (r.ok) {
        trackEvent({ type: want ? 'wishlist_add' : 'wishlist_remove', channelCode, productId });
        announce(
          want ? `${name} ditambahkan ke wishlist.` : `${name} dihapus dari wishlist.`,
          want ? { href: '/wishlist', label: 'Lihat wishlist' } : undefined,
        );
      } else if (r.unauthenticated) {
        goToLogin(returnPath ?? `${window.location.pathname}${window.location.search}`);
      } else {
        announce(r.message);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      className={`${styles.heart} ${variant === 'card' ? styles.cardHeart : styles.heartDetail}`}
      aria-pressed={pressed}
      aria-label={`Wishlist: ${name}`}
      disabled={busy}
      onClick={() => void onClick()}
    >
      <Heart />
      {variant === 'detail' && <span aria-hidden="true">Wishlist</span>}
    </button>
  );
}
