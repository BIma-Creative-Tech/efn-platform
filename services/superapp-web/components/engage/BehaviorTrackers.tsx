'use client';

/**
 * Pencatat event dari halaman (G50; docs/04 §28.6). Tidak merender apa pun. Semuanya no-op bila consent perangkat tidak ada
 * (`trackEvent` memeriksa). Tanpa console.*.
 */
import { useEffect } from 'react';
import { VisibleTimer } from '@/lib/tracking';
import { flushOnHide, trackEvent } from '@/lib/tracking-client';

/** `product_view` + `duration_s` (waktu tab terlihat, maks 600 dtk) dikirim saat meninggalkan halaman/berpindah produk. */
export function ProductViewTracker({ channelCode, productId }: { channelCode: string; productId: number }) {
  useEffect(() => {
    const timer = new VisibleTimer(() => Date.now(), document.visibilityState === 'visible');
    let sent = false;
    const send = () => {
      if (sent) return;
      sent = true;
      trackEvent({ type: 'product_view', channelCode, productId, durationS: timer.seconds() });
    };
    const onVis = () => {
      timer.setVisible(document.visibilityState === 'visible');
    };
    document.addEventListener('visibilitychange', onVis);
    // Saat tab ditutup: catat lalu kirim segera lewat beacon (flush `pagehide` global bisa berjalan lebih dulu).
    const onPageHide = () => {
      send();
      flushOnHide();
    };
    window.addEventListener('pagehide', onPageHide);
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      window.removeEventListener('pagehide', onPageHide);
      send(); // navigasi klien (unmount)
    };
  }, [channelCode, productId]);
  return null;
}

/** `search`: kata kunci APA ADANYA (server yang meredaksi) + jumlah hasil, sekali per kombinasi. */
export function SearchTracker({ channelCode, query, resultCount }: { channelCode: string; query: string; resultCount: number }) {
  useEffect(() => {
    if (!query) return;
    trackEvent({ type: 'search', channelCode, query, resultCount });
  }, [channelCode, query, resultCount]);
  return null;
}
