'use client';

/**
 * Etalase marketplace Super-App: tab per brand -> grid produk ASLI dari katalog (foto, nama, harga, catatan pajak).
 * Ganti tab = transisi framer-motion (AnimatePresence + layout), kartu miring 3D mengikuti pointer. Tab = tombol
 * (aria-pressed) di atas satu daftar; tanpa JS tab pertama tetap tampil & setiap kartu tetap tautan ke produk.
 */
import { AnimatePresence, motion, useMotionValue, useSpring, useTransform } from 'framer-motion';
import Link from 'next/link';
import { useState, type CSSProperties, type PointerEvent } from 'react';
import styles from './superapp.module.css';
import { useReducedMotionSafe } from './useReducedMotionSafe';

export interface ShelfProduct {
  id: number;
  name: string;
  price: string;
  taxNote: string;
  src: string | null;
  href: string;
}

export interface ShelfTab {
  id: string;
  label: string;
  kind: string;
  shopHref: string;
  products: ShelfProduct[];
  /** Jumlah produk online (dari katalog; `+` bila ada halaman berikutnya). */
  total: string;
}

function TiltCard({ p, i, reduce }: { p: ShelfProduct; i: number; reduce: boolean }) {
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const rx = useSpring(useTransform(my, [-0.5, 0.5], [7, -7]), { stiffness: 220, damping: 20 });
  const ry = useSpring(useTransform(mx, [-0.5, 0.5], [-9, 9]), { stiffness: 220, damping: 20 });
  function onMove(e: PointerEvent<HTMLAnchorElement>) {
    if (reduce || e.pointerType !== 'mouse') return;
    const r = e.currentTarget.getBoundingClientRect();
    mx.set((e.clientX - r.left) / r.width - 0.5);
    my.set((e.clientY - r.top) / r.height - 0.5);
  }
  function onLeave() {
    mx.set(0);
    my.set(0);
  }
  return (
    <motion.li
      layout={!reduce}
      initial={reduce ? false : { opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, y: -12, transition: { duration: 0.18 } }}
      transition={{ duration: 0.55, ease: [0.16, 1, 0.3, 1], delay: reduce ? 0 : Math.min(i, 7) * 0.045 }}
      className={styles.shelfItem}
    >
      <motion.div style={reduce ? undefined : ({ rotateX: rx, rotateY: ry } as unknown as CSSProperties)} className={styles.shelfTilt}>
        <Link href={p.href} className={styles.shelfCard} onPointerMove={onMove} onPointerLeave={onLeave}>
          <span className={styles.shelfImg}>
            {p.src ? (
              // eslint-disable-next-line @next/next/no-img-element -- proxy same-origin ber-cache immutable
              <img src={p.src} alt="" width={512} height={512} loading="lazy" decoding="async" />
            ) : (
              <span className={styles.shelfNoImg} aria-hidden="true">
                {p.name.slice(0, 1)}
              </span>
            )}
          </span>
          <span className={styles.shelfBody}>
            <span className={styles.shelfName}>{p.name}</span>
            <span className={styles.shelfPrice}>{p.price}</span>
            {p.taxNote && <span className={styles.shelfTax}>{p.taxNote}</span>}
          </span>
        </Link>
      </motion.div>
    </motion.li>
  );
}

export function Shelf({ tabs }: { tabs: ShelfTab[] }) {
  const reduce = useReducedMotionSafe();
  const [active, setActive] = useState(tabs[0]?.id ?? '');
  const tab = tabs.find((t) => t.id === active) ?? tabs[0];
  if (!tab) return null;
  return (
    <section className={`${styles.band} ${styles.shelf}`} aria-labelledby="etalase-judul" data-shelf="">
      <div className={styles.inner}>
        <div className={styles.shelfHead}>
          <div>
            <p className={styles.kicker}>
              <span>Etalase</span>
              <span aria-hidden="true" className={styles.kickerRule} />
              <span>Harga dari katalog gerai</span>
            </p>
            <h2 id="etalase-judul" className={styles.h2}>
              Belanja langsung dari semua brand.
            </h2>
          </div>
          <div className={styles.tabs} role="group" aria-label="Pilih brand">
            {tabs.map((t) => (
              <button key={t.id} type="button" className={styles.tab} aria-pressed={t.id === tab.id} onClick={() => setActive(t.id)}>
                {t.id === tab.id && !reduce && <motion.span layoutId="shelf-tab" className={styles.tabPill} transition={{ type: 'spring', stiffness: 380, damping: 32 }} />}
                <span className={styles.tabLabel}>{t.label}</span>
              </button>
            ))}
          </div>
        </div>

        <div className={styles.shelfMeta}>
          <p>
            <strong>{tab.label}</strong> · {tab.kind}
            {tab.total && <> · {tab.total} produk online</>}
          </p>
          <Link href={tab.shopHref} className={styles.shelfAll}>
            Lihat semua <span aria-hidden="true">→</span>
          </Link>
        </div>

        {tab.products.length === 0 ? (
          <p className={styles.shelfEmpty}>Produk {tab.label} belum tersedia online.</p>
        ) : (
          <motion.ul className={styles.shelfGrid} layout={!reduce} aria-live="polite">
            <AnimatePresence mode="popLayout" initial={false}>
              {tab.products.map((p, i) => (
                <TiltCard key={`${tab.id}-${p.id}`} p={p} i={i} reduce={reduce} />
              ))}
            </AnimatePresence>
          </motion.ul>
        )}
      </div>
    </section>
  );
}
