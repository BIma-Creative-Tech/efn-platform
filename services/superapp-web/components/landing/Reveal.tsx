'use client';

/**
 * Muncul saat masuk viewport (framer-motion `whileInView`): naik + memudar sekali. Reduced motion -> elemen biasa.
 * Tanpa JS: `<noscript>` di SuperAppLanding memaksa `[data-reveal]` terlihat.
 */
import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import { useReducedMotionSafe } from './useReducedMotionSafe';

const EASE = [0.16, 1, 0.3, 1] as const;

export function Reveal({ children, as = 'div', delay = 0, className }: { children: ReactNode; as?: 'div' | 'li'; delay?: number; className?: string }) {
  const reduce = useReducedMotionSafe();
  const M = as === 'li' ? motion.li : motion.div;
  if (reduce) {
    return as === 'li' ? <li className={className}>{children}</li> : <div className={className}>{children}</div>;
  }
  return (
    <M
      className={className}
      data-reveal=""
      initial={{ opacity: 0, y: 28, filter: 'blur(6px)' }}
      whileInView={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      viewport={{ once: true, margin: '0px 0px -12% 0px' }}
      transition={{ duration: 0.9, ease: EASE, delay }}
    >
      {children}
    </M>
  );
}
