'use client';

/**
 * Panggung hero supermarket: kartu kaca (glassmorphism) melayang dengan parallax mengikuti pointer (framer-motion).
 * Foto produk ASLI dari katalog bila ada; tanpa foto -> ikon lorong di atas gradasi lembut. Kartu "tiket ambil"
 * memakai data toko dari katalog (nama toko, menit siap) — tanpa angka manual.
 * `prefers-reduced-motion` -> statis (tanpa parallax & tanpa animasi melayang; CSS juga mematikannya).
 * Dekoratif kecuali tautan produk: tiap kartu produk = tautan ke halaman produk.
 */
import Link from 'next/link';
import { motion, useMotionValue, useSpring, useTransform, type MotionValue } from 'framer-motion';
import type { CSSProperties, PointerEvent } from 'react';
import type { AisleIconId } from '@/lib/supermarket';
import { useReducedMotionSafe } from '@/components/landing/useReducedMotionSafe';
import { AisleIcon } from './AisleIcon';
import styles from './supermarket.module.css';

export interface StageProduct {
  id: number;
  name: string;
  price: string;
  src: string | null;
  href: string;
  icon: AisleIconId;
  tone: readonly [string, string];
}

export interface StageTicket {
  store: string | null;
  prepMinutes: number | null;
}

/** Kedalaman parallax per kartu (px pada tepi panggung). */
const DEPTH = [26, -18, 14] as const;

function useLayer(x: MotionValue<number>, y: MotionValue<number>, depth: number) {
  return { x: useTransform(x, (v) => v * depth), y: useTransform(y, (v) => v * depth) };
}

function ProductCard({ p, index }: { p: StageProduct; index: number }) {
  return (
    <Link href={p.href} className={styles.stageCard} data-slot={index} aria-label={`${p.name}, ${p.price}`}>
      <span className={styles.stagePhoto} style={{ '--tone-a': p.tone[0], '--tone-b': p.tone[1] } as CSSProperties}>
        {p.src ? (
          // eslint-disable-next-line @next/next/no-img-element -- proxy gambar same-origin ber-cache immutable
          <img src={p.src} alt="" loading={index === 0 ? 'eager' : 'lazy'} decoding="async" />
        ) : (
          <AisleIcon id={p.icon} size={56} />
        )}
      </span>
      <span className={styles.stageMeta}>
        <span className={styles.stageName}>{p.name}</span>
        <span className={styles.stagePrice}>{p.price}</span>
      </span>
    </Link>
  );
}

function Ticket({ t }: { t: StageTicket }) {
  return (
    <div className={styles.ticket} aria-hidden="true">
      <span className={styles.ticketDot} />
      <span className={styles.ticketBody}>
        <span className={styles.ticketTitle}>Pesanan siap diambil</span>
        <span className={styles.ticketText}>
          {t.store ?? 'Toko pilihan Anda'}
          {t.prepMinutes ? ` · ± ${t.prepMinutes} menit` : ''}
        </span>
      </span>
      <span className={styles.ticketCode}>
        {Array.from({ length: 16 }, (_, i) => (
          <i key={i} data-on={[0, 2, 3, 5, 6, 9, 10, 12, 15].includes(i) ? '' : undefined} />
        ))}
      </span>
    </div>
  );
}

export function HeroStage({ products, ticket }: { products: StageProduct[]; ticket: StageTicket }) {
  const reduce = useReducedMotionSafe();
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const sx = useSpring(mx, { stiffness: 80, damping: 18, mass: 0.6 });
  const sy = useSpring(my, { stiffness: 80, damping: 18, mass: 0.6 });
  const l0 = useLayer(sx, sy, DEPTH[0]);
  const l1 = useLayer(sx, sy, DEPTH[1]);
  const l2 = useLayer(sx, sy, DEPTH[2]);
  const layers = [l0, l1, l2];
  const tx = useTransform(sx, (v) => v * -10);
  const ty = useTransform(sy, (v) => v * -10);

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (reduce || e.pointerType !== 'mouse') return;
    const r = e.currentTarget.getBoundingClientRect();
    mx.set((e.clientX - r.left) / r.width - 0.5);
    my.set((e.clientY - r.top) / r.height - 0.5);
  };
  const onLeave = () => {
    mx.set(0);
    my.set(0);
  };

  const shown = products.slice(0, 3);
  return (
    <div className={styles.stage} onPointerMove={onMove} onPointerLeave={onLeave} data-reduced={reduce ? '' : undefined}>
      <span className={styles.stageHalo} aria-hidden="true" />
      <span className={styles.stageRing} aria-hidden="true" />
      {shown.map((p, i) => (
        <motion.div key={p.id} className={styles.stageLayer} data-slot={i} style={reduce ? undefined : layers[i]}>
          <ProductCard p={p} index={i} />
        </motion.div>
      ))}
      <motion.div className={styles.ticketLayer} style={reduce ? undefined : { x: tx, y: ty }}>
        <Ticket t={ticket} />
      </motion.div>
    </div>
  );
}
