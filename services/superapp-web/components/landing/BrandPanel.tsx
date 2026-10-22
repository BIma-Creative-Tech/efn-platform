'use client';

/**
 * Panel brand Super-App (landing `app.`, docs/design/superapp-concept.md §6). Pola Eraspace: satu panel per brand,
 * memakai PALET BRAND ITU SENDIRI (token dari lib/brands.ts disuntik ke variabel lokal panel) dan foto produk ASLI
 * dari katalog (proxy same-origin). Gerak dipicu scroll (naik & turun) dan pointer: kartu foto bertumpuk dengan
 * perspektif + rotasi kecil (kedalaman 3D ringan). Tanpa autoplay; `prefers-reduced-motion` -> statis.
 * Foto dan teks berada di kolom terpisah (grid), jadi foto tidak pernah menimpa teks.
 */
import { motion, useMotionValue, useScroll, useSpring, useTransform, type MotionValue } from 'framer-motion';
import Link from 'next/link';
import { useRef, useSyncExternalStore, type CSSProperties, type PointerEvent } from 'react';
import styles from './superapp.module.css';
import { useReducedMotionSafe } from './useReducedMotionSafe';

export interface PanelPhoto {
  src: string;
  name: string;
  price: string | null;
}

export interface BrandPanelProps {
  index: number;
  name: string;
  kind: string;
  line: string;
  /** Palet brand (hex tervalidasi lib/brands.ts). */
  palette: { bg: string; text: string; muted: string; accent: string; card: string; onAccent: string };
  photos: PanelPhoto[];
  /** Fakta dari katalog (mis. "12+ pilihan", "Ambil di gerai"). */
  facts: string[];
  shopHref: string;
  siteHref: string | null;
  reserveHref: string | null;
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/*
 * Amplitudo gerak per tata letak: satu kolom (≤ 56rem) panggung foto berada DI BAWAH teks, jadi geser vertikal
 * diperkecil agar kartu tetap di dalam panggungnya (tidak menutupi teks / terpotong tepi panel). Diukur oleh sweep
 * scroll naik-turun (g23-superapp-sweep). SSR = 1 (desktop), lalu disinkronkan via useSyncExternalStore.
 */
const NARROW_MQ = '(max-width: 56rem)';
function subscribeNarrow(cb: () => void) {
  const m = window.matchMedia(NARROW_MQ);
  m.addEventListener('change', cb);
  return () => m.removeEventListener('change', cb);
}
const useMotionAmp = () =>
  useSyncExternalStore(
    subscribeNarrow,
    () => (window.matchMedia(NARROW_MQ).matches ? 0.3 : 0.7),
    () => 0.7,
  );

/** Satu kartu foto; hook per kartu di komponen sendiri (aturan hooks). */
function PhotoCard({ photo, i, progress, tiltX, tiltY, reduce, amp }: { photo: PanelPhoto; i: number; progress: MotionValue<number>; tiltX: MotionValue<number>; tiltY: MotionValue<number>; reduce: boolean; amp: number }) {
  // Rentang input dijepit ke [0,1] (WAAPI ScrollTimeline menolak offset di luar 0..1).
  const a = clamp01(0.1 + i * 0.06);
  const b = clamp01(0.5);
  const c = clamp01(0.9 - i * 0.04);
  const base = [-14, 0, 12][i] ?? 0;
  const y = useTransform(progress, [a, b, c], [(90 + i * 30) * amp, i * 14 * amp, (-60 - i * 20) * amp]);
  const rotZ = useTransform(progress, [a, b, c], [base - 6, base * 0.45, base + 5]);
  const rotY = useTransform(progress, [a, b, c], [24 - i * 8, -4 + i * 3, -18]);
  const scale = useTransform(progress, [a, b, c], [0.86, 1 - i * 0.07, 0.94 - i * 0.05]);
  const ry = useTransform(() => rotY.get() + tiltY.get());
  const rx = useTransform(() => tiltX.get());
  const style = reduce ? { rotate: base * 0.45, scale: 1 - i * 0.07 } : { y, rotate: rotZ, rotateY: ry, rotateX: rx, scale };
  return (
    <motion.figure className={styles.pcard} data-i={i} style={{ ...style, zIndex: 3 - i } as unknown as CSSProperties}>
      {/* eslint-disable-next-line @next/next/no-img-element -- proxy same-origin ber-cache immutable */}
      <img src={photo.src} alt="" width={512} height={512} loading="lazy" decoding="async" />
      {i === 0 && (
        <figcaption>
          <span>{photo.name}</span>
          {photo.price && <span className={styles.pprice}>{photo.price}</span>}
        </figcaption>
      )}
    </motion.figure>
  );
}

export function BrandPanel({ index, name, kind, line, palette, photos, facts, shopHref, siteHref, reserveHref }: BrandPanelProps) {
  const ref = useRef<HTMLElement>(null);
  const reduce = useReducedMotionSafe();
  const amp = useMotionAmp();
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start end', 'end start'] });
  const progress = useSpring(scrollYProgress, { stiffness: 120, damping: 24, mass: 0.4 });
  const px = useMotionValue(0);
  const py = useMotionValue(0);
  const tiltY = useSpring(useTransform(px, [-0.5, 0.5], [-8, 8]), { stiffness: 140, damping: 18 });
  const tiltX = useSpring(useTransform(py, [-0.5, 0.5], [6, -6]), { stiffness: 140, damping: 18 });
  const copyY = useTransform(progress, [0, 0.5, 1], [40 * amp, 0, -30 * amp]);

  function onMove(e: PointerEvent<HTMLDivElement>) {
    if (reduce || e.pointerType !== 'mouse') return;
    const r = e.currentTarget.getBoundingClientRect();
    px.set((e.clientX - r.left) / r.width - 0.5);
    py.set((e.clientY - r.top) / r.height - 0.5);
  }
  function onLeave() {
    px.set(0);
    py.set(0);
  }

  const vars = {
    '--p-bg': palette.bg,
    '--p-text': palette.text,
    '--p-muted': palette.muted,
    '--p-accent': palette.accent,
    '--p-card': palette.card,
    '--p-on-accent': palette.onAccent,
  } as CSSProperties;
  const flip = index % 2 === 1;
  const id = `brand-${index}`;

  return (
    <section ref={ref} className={`${styles.panel} ${flip ? styles.panelFlip : ''}`} style={vars} aria-labelledby={id} data-brand-panel="">
      <div className={styles.panelInner}>
        <motion.div className={styles.panelCopy} style={reduce ? undefined : { y: copyY }}>
          <p className={styles.pkicker}>
            <span>{String(index + 1).padStart(2, '0')}</span>
            <span aria-hidden="true" className={styles.prule} />
            <span>{kind}</span>
          </p>
          <h2 id={id} className={styles.ptitle}>
            {name}
          </h2>
          <p className={styles.pline}>{line}</p>
          {facts.length > 0 && (
            <ul className={styles.pfacts} aria-label={`Layanan ${name}`}>
              {facts.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
          <p className={styles.pactions}>
            <Link href={shopHref} className={styles.pbtn}>
              Belanja {name}
            </Link>
            {reserveHref && (
              <Link href={reserveHref} className={styles.pghost}>
                Reservasi meja
              </Link>
            )}
            {siteHref && (
              <a href={siteHref} className={styles.plink}>
                Situs {name}
                <span aria-hidden="true"> ↗</span>
              </a>
            )}
          </p>
        </motion.div>
        <div className={styles.pstage} onPointerMove={onMove} onPointerLeave={onLeave} aria-hidden="true">
          {photos.length > 0 ? (
            photos.slice(0, 3).map((p, i) => <PhotoCard key={p.src} photo={p} i={i} progress={progress} tiltX={tiltX} tiltY={tiltY} reduce={reduce} amp={amp} />)
          ) : (
            <div className={styles.pempty}>{name}</div>
          )}
        </div>
      </div>
    </section>
  );
}
