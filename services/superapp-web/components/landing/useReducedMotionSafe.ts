'use client';

import { useSyncExternalStore } from 'react';

/**
 * `prefers-reduced-motion` yang aman untuk SSR/hidrasi. `useReducedMotion` framer-motion langsung bernilai `true` di
 * render hidrasi pertama sementara server merender `false` -> atribut/style beda dan React TIDAK menambalnya (kartu
 * tetap tersembunyi). `useSyncExternalStore` memakai snapshot server (`false`) saat hidrasi, lalu render ulang.
 */
const QUERY = '(prefers-reduced-motion: reduce)';

function subscribe(cb: () => void) {
  const mq = window.matchMedia(QUERY);
  mq.addEventListener('change', cb);
  return () => mq.removeEventListener('change', cb);
}

export function useReducedMotionSafe(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false,
  );
}
