'use client';

/**
 * Dinding brand portofolio EFN (landing `app.` & halaman /brand): grid kartu logo semua brand di bawah EFN, filter status
 * (Semua · Pesan online · Gerai · Segera hadir) dengan pil framer-motion `layoutId` dan grid `AnimatePresence`.
 * Kartu: logo resmi (bila file tersedia) atau logotipe teks, sorotan cahaya mengikuti kursor + miring 3D ringan.
 * Kartu brand online menaut ke katalognya; brand lain bukan tautan (tanpa klaim layanan yang belum ada).
 * Tanpa JS: semua kartu tampil (filter "Semua"). `prefers-reduced-motion` -> tanpa miring/transisi.
 */
import { AnimatePresence, motion, useMotionTemplate, useMotionValue, useSpring, useTransform } from 'framer-motion';
import Link from 'next/link';
import { useState, type CSSProperties, type PointerEvent, type ReactNode } from 'react';
import { portfolioCounts, safePortfolioLogo, STATUS_LABEL, type PortfolioBrand, type PortfolioStatus } from '@/lib/portfolio';
import styles from './superapp.module.css';
import { useReducedMotionSafe } from './useReducedMotionSafe';

export interface WallBrand extends PortfolioBrand {
  /** Tautan katalog Super-App (brand online). */
  href: string | null;
  /** Fakta katalog (mis. "24+ produk"), hanya brand online. */
  fact: string | null;
}

type Filter = 'all' | PortfolioStatus;
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'Semua' },
  { id: 'online', label: 'Pesan online' },
  { id: 'store', label: 'Di gerai' },
  { id: 'soon', label: 'Segera hadir' },
];

function Wordmark({ name }: { name: string }) {
  return <span className={styles.wallWordmark}>{name}</span>;
}

function Card({ b, i, reduce }: { b: WallBrand; i: number; reduce: boolean }) {
  const mx = useMotionValue(50);
  const my = useMotionValue(50);
  const rx = useSpring(useTransform(my, [0, 100], [6, -6]), { stiffness: 200, damping: 20 });
  const ry = useSpring(useTransform(mx, [0, 100], [-8, 8]), { stiffness: 200, damping: 20 });
  const spot = useMotionTemplate`radial-gradient(18rem circle at ${mx}% ${my}%, color-mix(in oklab, var(--l-accent) 22%, transparent), transparent 60%)`;
  function onMove(e: PointerEvent<HTMLElement>) {
    if (reduce || e.pointerType !== 'mouse') return;
    const r = e.currentTarget.getBoundingClientRect();
    mx.set(((e.clientX - r.left) / r.width) * 100);
    my.set(((e.clientY - r.top) / r.height) * 100);
  }
  function onLeave() {
    mx.set(50);
    my.set(50);
  }
  const logo = safePortfolioLogo(b);
  const body: ReactNode = (
    <>
      <motion.span className={styles.wallSpot} style={reduce ? undefined : { background: spot }} aria-hidden="true" />
      <span className={styles.wallTop}>
        <span className={styles.wallCat}>{b.category}</span>
        <span className={`${styles.wallBadge} ${styles[`wallBadge_${b.status}`]}`}>{b.status === 'soon' && b.eta ? `${STATUS_LABEL.soon} · ${b.eta}` : STATUS_LABEL[b.status]}</span>
      </span>
      <span className={styles.wallLogo}>
        {logo ? (
          // eslint-disable-next-line @next/next/no-img-element -- logo statis same-origin
          <img src={logo} alt={b.name} loading="lazy" decoding="async" />
        ) : (
          <Wordmark name={b.name} />
        )}
      </span>
      <span className={styles.wallLine}>{b.line}</span>
      <span className={styles.wallFoot}>
        <span className={styles.wallOrigin}>{b.origin ?? ''}</span>
        {b.fact && <span className={styles.wallFact}>{b.fact}</span>}
        {b.href && (
          <span className={styles.wallGo} aria-hidden="true">
            →
          </span>
        )}
      </span>
    </>
  );
  return (
    <motion.li
      layout={!reduce}
      initial={reduce ? false : { opacity: 0, scale: 0.94, y: 18 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.94, transition: { duration: 0.18 } }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1], delay: reduce ? 0 : Math.min(i, 8) * 0.04 }}
      className={styles.wallItem}
      data-wall-brand={b.id}
      data-status={b.status}
    >
      <motion.div className={styles.wallTilt} style={reduce ? undefined : ({ rotateX: rx, rotateY: ry } as unknown as CSSProperties)}>
        {b.href ? (
          <Link href={b.href} className={styles.wallCard} onPointerMove={onMove} onPointerLeave={onLeave} aria-label={`${b.name} — ${STATUS_LABEL[b.status]}`}>
            {body}
          </Link>
        ) : (
          <div className={`${styles.wallCard} ${b.status === 'soon' ? styles.wallSoon : ''}`} onPointerMove={onMove} onPointerLeave={onLeave}>
            {body}
          </div>
        )}
      </motion.div>
    </motion.li>
  );
}

export function BrandWall({ brands, title, kicker, lead, groupLogo }: { brands: WallBrand[]; title: string; kicker: string; lead?: string; groupLogo?: { src: string; alt: string } }) {
  const reduce = useReducedMotionSafe();
  const [filter, setFilter] = useState<Filter>('all');
  const counts = portfolioCounts(brands);
  const shown = filter === 'all' ? brands : brands.filter((b) => b.status === filter);
  return (
    <section className={`${styles.band} ${styles.wall}`} aria-labelledby="portofolio-judul" data-brand-wall="">
      <div className={styles.inner}>
        <div className={styles.wallHead}>
          <div>
            <p className={styles.kicker}>
              <span>{kicker}</span>
              <span aria-hidden="true" className={styles.kickerRule} />
              <span>{brands.length} brand</span>
            </p>
            <h2 id="portofolio-judul" className={styles.h2}>
              {title}
            </h2>
            {lead && <p className={styles.profileLead}>{lead}</p>}
          </div>
          {groupLogo && (
            // eslint-disable-next-line @next/next/no-img-element -- logo grup statis same-origin
            <img className={styles.wallGroupLogo} src={groupLogo.src} alt={groupLogo.alt} loading="lazy" decoding="async" />
          )}
        </div>

        <div className={styles.tabs} role="group" aria-label="Saring brand">
          {FILTERS.filter((f) => f.id === 'all' || counts[f.id] > 0).map((f) => (
            <button key={f.id} type="button" className={styles.tab} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
              {filter === f.id && !reduce && <motion.span layoutId="wall-filter" className={styles.tabPill} transition={{ type: 'spring', stiffness: 380, damping: 32 }} />}
              <span className={styles.tabLabel}>
                {f.label} <span className={styles.tabCount}>{counts[f.id]}</span>
              </span>
            </button>
          ))}
        </div>

        <motion.ul className={styles.wallGrid} layout={!reduce} aria-live="polite">
          <AnimatePresence mode="popLayout" initial={false}>
            {shown.map((b, i) => (
              <Card key={b.id} b={b} i={i} reduce={reduce} />
            ))}
          </AnimatePresence>
        </motion.ul>
      </div>
    </section>
  );
}
