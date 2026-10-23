'use client';

/**
 * Hero 3D landing Super-App (`app.`). Panggung CSS 3D (perspective + preserve-3d) berisi foto produk ASLI dari katalog
 * pada kedalaman berbeda (translateZ). Gerak: pointer (mouse) memutar seluruh panggung + paralaks per kedalaman +
 * sorotan cahaya mengikuti kursor; scroll (naik & turun) memiringkan dan menjauhkan panggung. Tanpa autoplay; masuk
 * awal = animasi CSS (tetap tampil tanpa JS); `prefers-reduced-motion` -> statis. Setiap kartu = tautan ke produk
 * (marketplace), panggung di kolom sendiri sehingga tidak menimpa teks.
 */
import { motion, useMotionTemplate, useMotionValue, useScroll, useSpring, useTransform, type MotionValue } from 'framer-motion';
import Link from 'next/link';
import { Fragment, useRef, type CSSProperties, type PointerEvent } from 'react';
import { safePortfolioLogo } from '@/lib/portfolio';
import { HERO_MAX_ITEMS } from '@/lib/superapp';
import type { WallBrand } from './BrandWall';
import styles from './superapp.module.css';
import { useReducedMotionSafe } from './useReducedMotionSafe';

export interface HeroItem {
  id: number;
  name: string;
  brand: string;
  price: string | null;
  src: string;
  href: string;
}

export interface SearchChannel {
  code: string;
  label: string;
}

export interface HeroStats {
  brands: number;
  products: string | null;
  outlets: number;
}

/** Posisi kartu di panggung (% lebar/tinggi panggung), kedalaman (px), lebar (% panggung), rotasi (deg). */
const SLOTS = [
  { x: 50, y: 50, z: 150, w: 38, r: -2 },
  { x: 19, y: 30, z: -40, w: 26, r: -7 },
  { x: 81, y: 27, z: 10, w: 27, r: 6 },
  { x: 17, y: 75, z: 60, w: 25, r: 5 },
  { x: 83, y: 76, z: 80, w: 26, r: -5 },
  { x: 49, y: 12, z: -200, w: 21, r: 3 },
  { x: 51, y: 90, z: -160, w: 21, r: -3 },
] as const;

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

function Card({ item, i, px, py, reduce }: { item: HeroItem; i: number; px: MotionValue<number>; py: MotionValue<number>; reduce: boolean }) {
  const s = SLOTS[i]!;
  // Paralaks: kartu dekat (z besar) bergeser lebih jauh dari kartu jauh.
  const depth = (s.z + 240) / 400;
  const x = useTransform(px, [-0.5, 0.5], [-30 * depth, 30 * depth]);
  const y = useTransform(py, [-0.5, 0.5], [-22 * depth, 22 * depth]);
  const pos = {
    left: `${s.x}%`,
    top: `${s.y}%`,
    width: `${s.w}%`,
    '--z': `${s.z}px`,
    '--r': `${s.r}deg`,
    '--d': `${180 + i * 90}ms`,
  } as CSSProperties;
  const label = `${item.name} — ${item.brand}${item.price ? `, ${item.price}` : ''}`;
  return (
    <motion.div className={styles.h3dSlot} style={reduce ? pos : ({ ...pos, x, y } as unknown as CSSProperties)} data-hero-card={i}>
      <Link href={item.href} className={styles.h3dCard} aria-label={label}>
        <span className={styles.h3dFrame}>
          {/* eslint-disable-next-line @next/next/no-img-element -- proxy same-origin ber-cache immutable */}
          <img src={item.src} alt="" width={512} height={512} loading={i < 3 ? 'eager' : 'lazy'} decoding="async" draggable={false} />
          <span className={styles.h3dCap} aria-hidden="true">
            <span className={styles.h3dBrand}>{item.brand}</span>
            <span className={styles.h3dName}>{item.name}</span>
            {item.price && <span className={styles.h3dPrice}>{item.price}</span>}
          </span>
        </span>
      </Link>
    </motion.div>
  );
}

function Headline({ text, emphasis }: { text: string; emphasis: string }) {
  const words = text.split(/\s+/).filter(Boolean);
  return (
    <h1 id="superapp-judul" className={styles.title}>
      {words.map((w, i) => {
        const em = w.replace(/[^\p{L}\p{N}]/gu, '').toLowerCase() === emphasis.toLowerCase();
        return (
          <Fragment key={`${w}-${i}`}>
            <span className={styles.word}>
              <span className={em ? styles.wordEm : undefined} style={{ ['--i' as string]: i }}>
                {w}
              </span>
            </span>
            {i < words.length - 1 ? ' ' : null}
          </Fragment>
        );
      })}
    </h1>
  );
}

export function Hero3D({
  title,
  emphasis,
  lead,
  items,
  search,
  stats,
  logos,
}: {
  title: string;
  emphasis: string;
  lead: string;
  items: HeroItem[];
  search: SearchChannel[];
  /** Tetap diterima untuk kompatibilitas (jangkar panel brand); deretan logo kini memakai `logos`. */
  brandNames?: { name: string; href: string }[];
  stats: HeroStats;
  logos: WallBrand[];
}) {
  const ref = useRef<HTMLElement>(null);
  const reduce = useReducedMotionSafe();
  const px = useMotionValue(0);
  const py = useMotionValue(0);
  const mx = useMotionValue(70);
  const my = useMotionValue(35);
  const spx = useSpring(px, { stiffness: 90, damping: 20, mass: 0.6 });
  const spy = useSpring(py, { stiffness: 90, damping: 20, mass: 0.6 });

  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end start'] });
  const prog = useSpring(scrollYProgress, { stiffness: 120, damping: 26, mass: 0.4 });
  const scrollRX = useTransform(prog, [clamp01(0), clamp01(1)], [0, 22]);
  const sceneY = useTransform(prog, [0, 1], [0, -70]);
  const sceneScale = useTransform(prog, [0, 1], [1, 0.9]);
  const sceneOpacity = useTransform(prog, [0, 0.85], [1, 0.25]);
  const tiltY = useTransform(spx, [-0.5, 0.5], [-16, 16]);
  const tiltX = useTransform(spy, [-0.5, 0.5], [11, -11]);
  // Kemiringan dasar (6°, -10°) agar kedalaman terlihat tanpa mouse (sentuh/HP).
  const rx = useTransform(() => 6 + tiltX.get() + scrollRX.get());
  const ry = useTransform(() => -10 + tiltY.get());
  const glow = useMotionTemplate`radial-gradient(42rem circle at ${mx}% ${my}%, color-mix(in oklab, var(--l-accent) 20%, transparent), transparent 62%)`;

  function onMove(e: PointerEvent<HTMLElement>) {
    if (reduce || e.pointerType !== 'mouse') return;
    const r = e.currentTarget.getBoundingClientRect();
    const fx = (e.clientX - r.left) / r.width;
    const fy = (e.clientY - r.top) / r.height;
    px.set(fx - 0.5);
    py.set(fy - 0.5);
    mx.set(fx * 100);
    my.set(fy * 100);
  }
  function onLeave() {
    px.set(0);
    py.set(0);
  }

  const shown = items.slice(0, HERO_MAX_ITEMS);

  return (
    <section ref={ref} className={styles.hero} aria-labelledby="superapp-judul" onPointerMove={onMove} onPointerLeave={onLeave} data-hero3d="">
      <motion.div className={styles.heroGlow} style={reduce ? undefined : { background: glow }} aria-hidden="true" />
      <div className={`${styles.inner} ${styles.heroGrid}`}>
        <div className={styles.heroCopy}>
          <Headline text={title} emphasis={emphasis} />
          <p className={styles.lead}>{lead}</p>

          {search.length > 0 && (
            <form role="search" action="/belanja" method="get" className={styles.heroSearch}>
              <label htmlFor="hero-brand" className="visually-hidden">
                Brand
              </label>
              <select id="hero-brand" name="channel" defaultValue={search[0]!.code}>
                {search.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.label}
                  </option>
                ))}
              </select>
              <label htmlFor="hero-q" className="visually-hidden">
                Cari produk
              </label>
              <input id="hero-q" type="search" name="q" maxLength={64} placeholder="Cari roti, kopi, kebutuhan harian…" autoComplete="off" />
              <button type="submit">Cari</button>
            </form>
          )}

          <dl className={styles.heroStats}>
            <div>
              <dt>Brand EFN</dt>
              <dd>{stats.brands}</dd>
            </div>
            {stats.products && (
              <div>
                <dt>Produk online</dt>
                <dd>{stats.products}</dd>
              </div>
            )}
            {stats.outlets > 0 && (
              <div>
                <dt>Gerai ambil</dt>
                <dd>{stats.outlets}</dd>
              </div>
            )}
          </dl>
        </div>

        <div className={styles.h3dStage} aria-label="Produk pilihan" role="group">
          <motion.div
            className={styles.h3dScene}
            style={reduce ? undefined : ({ rotateX: rx, rotateY: ry, y: sceneY, scale: sceneScale, opacity: sceneOpacity } as unknown as CSSProperties)}
          >
            <span className={styles.h3dRing} aria-hidden="true" />
            {shown.map((it, i) => (
              <Card key={it.id} item={it} i={i} px={spx} py={spy} reduce={reduce} />
            ))}
          </motion.div>
        </div>
      </div>

      {logos.length > 0 && (
        <nav className={styles.logoStrip} aria-label="Brand di bawah EFN">
          {/* Daftar asli (dibaca pembaca layar & tanpa JS); salinan kedua hanya dekoratif untuk loop marquee. */}
          <ul className={styles.logoTrack}>
            {[0, 1].map((copy) =>
              logos.map((b) => {
                const logo = safePortfolioLogo(b);
                const inner = logo ? (
                  // eslint-disable-next-line @next/next/no-img-element -- logo statis same-origin
                  <img src={logo} alt={b.name} loading="lazy" decoding="async" />
                ) : (
                  <span className={styles.logoText}>{b.name}</span>
                );
                return (
                  <li key={`${copy}-${b.id}`} aria-hidden={copy === 1 ? true : undefined} data-status={b.status}>
                    {b.href && copy === 0 ? (
                      <Link href={b.href} className={styles.logoItem}>
                        {inner}
                      </Link>
                    ) : (
                      <span className={styles.logoItem}>{inner}</span>
                    )}
                    {b.status === 'soon' && <span className={styles.logoSoon}>Segera</span>}
                  </li>
                );
              }),
            )}
          </ul>
        </nav>
      )}
    </section>
  );
}
