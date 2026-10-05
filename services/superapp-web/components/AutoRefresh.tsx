'use client';

import { useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';

/**
 * Memanggil router.refresh() berkala (re-render Server Component) untuk status async.
 * Dibatasi `maxTimes` agar tidak polling tanpa akhir; berhenti saat tab tersembunyi.
 */
export function AutoRefresh({ intervalMs, maxTimes }: { intervalMs: number; maxTimes: number }) {
  const router = useRouter();
  const count = useRef(0);

  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      count.current += 1;
      if (count.current > maxTimes) {
        clearInterval(timer);
        return;
      }
      router.refresh();
    }, intervalMs);
    return () => clearInterval(timer);
  }, [router, intervalMs, maxTimes]);

  return null;
}
