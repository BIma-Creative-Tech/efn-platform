'use client';

/**
 * Rak etalase: produk ASLI dari katalog (BFF). Lebar rak = lebar grid konten (`.inner`), BUKAN full-bleed, jadi tepi
 * kiri & kanan kartu sejajar dengan judul seksi. Navigasi: tombol ← → (geser satu "halaman" kartu), seret/gulir
 * horizontal, dan keyboard (fokus kartu). Tombol nonaktif di ujung.
 * Muncul sekali sebagai daftar (stagger ≤ 0,5 dtk). `prefers-reduced-motion` -> tanpa animasi & scroll instan.
 */
import { motion } from 'framer-motion';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import styles from './landing.module.css';
import { useReducedMotionSafe } from './useReducedMotionSafe';

export interface RailItem {
  id: number;
  name: string;
  href: string;
  imageSrc: string | null;
  price: string;
  note: string;
}

const EASE_OUT = [0.16, 1, 0.3, 1] as const;

export function ShowcaseRail({ items, labelledBy, toolbar }: { items: RailItem[]; labelledBy: string; toolbar?: ReactNode }) {
  const reduce = useReducedMotionSafe();
  const list = useRef<HTMLUListElement>(null);
  const [edge, setEdge] = useState({ start: true, end: false });

  const update = useCallback(() => {
    const el = list.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    setEdge({ start: el.scrollLeft <= 2, end: el.scrollLeft >= max - 2 });
  }, []);

  useEffect(() => {
    const el = list.current;
    if (!el) return;
    const raf = requestAnimationFrame(update);
    el.addEventListener('scroll', update, { passive: true });
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null;
    ro?.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      el.removeEventListener('scroll', update);
      ro?.disconnect();
    };
  }, [update]);

  const page = (dir: 1 | -1) => {
    const el = list.current;
    if (!el) return;
    const first = el.querySelector('li');
    const gap = parseFloat(getComputedStyle(el).columnGap) || 0;
    const step = first ? first.getBoundingClientRect().width + gap : el.clientWidth;
    const perPage = Math.max(1, Math.floor((el.clientWidth + gap) / step));
    el.scrollBy({ left: dir * step * perPage, behavior: reduce ? 'auto' : 'smooth' });
  };

  return (
    <div className={styles.railWrap}>
      <div className={styles.railBar}>
        <div className={styles.railTools}>{toolbar}</div>
        <div className={styles.railNav}>
        <button type="button" className={styles.railBtn} onClick={() => page(-1)} disabled={edge.start} aria-label="Produk sebelumnya">
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <button type="button" className={styles.railBtn} onClick={() => page(1)} disabled={edge.end} aria-label="Produk berikutnya">
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path d="M9 5l7 7-7 7" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        </div>
      </div>
      <motion.ul
        ref={list}
        className={styles.rail}
        aria-labelledby={labelledBy}
        initial={reduce ? false : 'hidden'}
        whileInView="shown"
        viewport={{ once: true, amount: 0.15 }}
        variants={{ hidden: {}, shown: { transition: { staggerChildren: Math.min(0.06, 0.5 / Math.max(items.length, 1)) } } }}
      >
        {items.map((p) => (
          <motion.li
            key={p.id}
            className={styles.railItem}
            data-reveal=""
            variants={{
              hidden: { opacity: 0, y: 32, filter: 'blur(6px)' },
              shown: { opacity: 1, y: 0, filter: 'blur(0px)', transition: { duration: 0.7, ease: EASE_OUT } },
            }}
          >
            <Link href={p.href} className={styles.railCard}>
              <span className={styles.railImage}>
                {p.imageSrc ? (
                  // eslint-disable-next-line @next/next/no-img-element -- proxy same-origin ber-cache immutable (lihat ProductImage)
                  <img src={p.imageSrc} alt="" width={512} height={512} loading="lazy" decoding="async" />
                ) : (
                  <span className={styles.railNoImage}>Tanpa gambar</span>
                )}
              </span>
              <span className={styles.railName}>{p.name}</span>
              <span className={styles.railPrice}>
                {p.price}
                {p.note && <span className={styles.railNote}> · {p.note}</span>}
              </span>
            </Link>
          </motion.li>
        ))}
      </motion.ul>
    </div>
  );
}
