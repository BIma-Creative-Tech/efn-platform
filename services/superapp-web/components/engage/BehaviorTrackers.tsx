'use client';

/**
 * Pencatat event dari halaman (G50; docs/04 §28.6). Tidak merender apa pun. No-op bila tidak ada consent perangkat DAN log
 * kunjungan anonim (`features.visit_log`) mati (`trackEvent` memeriksa). Tanpa console.*.
 */
import { useEffect } from 'react';
import { usePathname } from 'next/navigation';
import { channelsOfBrandCode } from '@/lib/brands';
import { pageForPath, pickEventChannel, VisibleTimer } from '@/lib/tracking';
import { flushOnHide, trackEvent } from '@/lib/tracking-client';
import { useEngage } from './EngageProvider';

/**
 * `page_view` per navigasi (V-01), hanya bila `features.visit_log`. Yang dikirim HANYA nilai enum `page` (pemetaan
 * `pageForPath`), tidak pernah path/URL/query/referrer mentah. `channel_code` = `?channel=` bila valid (hanya kode kanal
 * yang dibaca, bukan URL), selain itu kode brand tenant; holding tanpa keduanya = tidak dikirim.
 */
export function PageViewTracker({ brandCode }: { brandCode: string | null }) {
  const pathname = usePathname();
  const { features } = useEngage();
  const visitLog = features.visitLog;
  useEffect(() => {
    if (!visitLog) return;
    const sp = new URLSearchParams(window.location.search);
    // Pemilihan kanal = fungsi murni `pickEventChannel` (diuji); brand_code != kode kanal untuk brand multi-kanal (Bacha).
    const channelCode = pickEventChannel(sp.get('channel'), channelsOfBrandCode(brandCode));
    if (!channelCode) return;
    trackEvent({ type: 'page_view', channelCode, page: pageForPath(pathname, (sp.get('q') ?? '').trim().length > 0) });
  }, [pathname, visitLog, brandCode]);
  return null;
}

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
  const { ready } = useEngage(); // tunggu flag fitur (log anonim) dimuat agar pencarian pertama tidak hilang
  useEffect(() => {
    if (!ready || !query) return;
    trackEvent({ type: 'search', channelCode, query, resultCount });
  }, [ready, channelCode, query, resultCount]);
  return null;
}
