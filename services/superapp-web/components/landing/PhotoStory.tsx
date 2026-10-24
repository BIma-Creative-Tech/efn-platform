'use client';

/**
 * "Scroll story" foto produk (v5 = desain v3 dipertahankan, tumpang tindih diperbaiki). Panggung menempel (sticky)
 * setinggi layar; bab-bab teks menggulir di atasnya; kartu foto produk ASLI masuk/keluar mengikuti scroll (naik/turun,
 * putar 3D, skala) dan miring halus mengikuti mouse.
 *
 * Perbaikan tumpang tindih (laporan user 21 Okt 2026), tanpa mengubah konsep:
 *  - Lanskap: kartu tiap bab punya SISI TETAP (pembuka kanan, lalu kiri/kanan bergantian) di "zona foto" yang lebarnya
 *    dihitung dari grid konten, berseberangan dengan kolom teks bab itu. Panggung tidak lagi melintas ke tengah layar
 *    (v3 menggeser panggung kanan↔kiri sehingga sempat menutupi teks).
 *  - Satu kartu per bab (kartu latar v3 yang sengaja menumpuk di belakang kartu depan dihapus). Kartu bab berurutan
 *    selalu di sisi berlawanan dan jendela tampilnya bergantian, jadi foto tidak pernah saling menimpa.
 *  - Kemiringan mouse = rotasi kecil di tempat (v3 juga menggeser kartu ±16 px ke samping).
 *  - Penjaga geometris: setiap baris teks yang mendekati kartu yang sedang terlihat memudar (opacity) sebelum
 *    bersentuhan. Penting di potret/HP: kartu menempel di atas, teks menggulir di bawahnya lalu memudar saat mendekat.
 *
 * Foto katalog berlatar terang & kecil (≈ 500 px) -> kartu putih berbingkai, lebar dibatasi. Sumber: proxy same-origin
 * `/api/catalog/images/:sha` (CSP `img-src 'self'`).
 * Degradasi: SSR/tanpa JS -> kartu bab pertama statis; `prefers-reduced-motion` -> tanpa gerak, kartu berganti per bab.
 * Tidak ada gerakan otomatis (semua dipicu scroll/pointer; WCAG 2.2.2 tidak berlaku).
 */
import { motion, useMotionValue, useScroll, useSpring, useTransform, type MotionValue } from 'framer-motion';
import { createRef, useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode, type RefObject } from 'react';
import styles from './landing.module.css';
import { useReducedMotionSafe } from './useReducedMotionSafe';

export interface StoryPhoto {
  /** URL same-origin (dari `imageUrl`, sudah divalidasi). */
  src: string;
  name: string;
  /** Harga terformat (mis. "Rp 22.000"), null = tidak ditampilkan. */
  price: string | null;
}

export interface StorySegment {
  /** id elemen (anchor HUD). */
  id: string;
  /** Label HUD. */
  label: string;
  /** Foto produk; yang pertama dipakai untuk kartu. Kosong -> kartu teks label. */
  photos: StoryPhoto[];
  content: ReactNode;
}

/** Sisi teks per segmen: pembuka & bab genap teks kiri, bab ganjil teks kanan. Kartu di sisi sebaliknya. */
function sideOf(i: number): 'hero' | 'left' | 'right' {
  if (i === 0) return 'hero';
  return i % 2 === 1 ? 'left' : 'right';
}
/** Sisi kartu (lanskap). */
function cardSide(i: number): 'left' | 'right' {
  return sideOf(i) === 'left' ? 'left' : 'right';
}

/**
 * Jendela kartu pada progres segmennya sendiri (offset 'start end' -> 'end start'; 0,5 = segmen memenuhi layar).
 * Masuk 0,25–0,40, penuh 0,40–0,60, keluar 0,60–0,75: kartu bab berikutnya mulai masuk tepat saat kartu ini selesai
 * keluar (dan berada di sisi berlawanan).
 */
const WIN = [0.25, 0.4, 0.6, 0.75];

/**
 * Nilai statis untuk `prefers-reduced-motion`. Harus objek nilai (bukan `undefined`): melepas prop style tidak
 * menghapus transform/opacity yang sudah ditulis framer-motion, jadi kartu tetap tersembunyi.
 */
const STILL_TEXT = { opacity: 1, y: 0, filter: 'none' } as const;
const STILL_CARD = { opacity: 1, y: 0, scale: 1, rotateY: 0, rotateX: 0 } as const;
const STILL_IMG = { y: '0%' } as const;

function Segment({ index, segment, reduce, segRef }: { index: number; segment: StorySegment; reduce: boolean; segRef: RefObject<HTMLDivElement | null> }) {
  const { scrollYProgress } = useScroll({ target: segRef, offset: ['start end', 'end start'] });
  const first = index === 0;
  // Pembuka: terlihat sejak awal (tidak menunggu hidrasi), hanya memudar saat ditinggalkan.
  const stops = first ? [0.5, 0.85] : [0.18, 0.42, 0.58, 0.82];
  const opacity = useTransform(scrollYProgress, stops, first ? [1, 0] : [0, 1, 1, 0]);
  const y = useTransform(scrollYProgress, stops, first ? [0, -90] : [80, 0, 0, -60]);
  const blurPx = useTransform(scrollYProgress, stops, first ? [0, 6] : [10, 0, 0, 8]);
  const filter = useTransform(blurPx, (v) => `blur(${v.toFixed(2)}px)`);
  return (
    <div ref={segRef} id={segment.id} className={styles.segment} data-side={sideOf(index)} tabIndex={-1}>
      <motion.div className={styles.segmentInner} style={reduce ? STILL_TEXT : { opacity, y, filter }} data-reveal="" data-guard-root="">
        {segment.content}
      </motion.div>
    </div>
  );
}

function PhotoCard({
  index,
  count,
  segment,
  segRef,
  reduce,
  active,
}: {
  index: number;
  count: number;
  segment: StorySegment;
  segRef: RefObject<HTMLDivElement | null>;
  reduce: boolean;
  active: boolean;
}) {
  const { scrollYProgress: p } = useScroll({ target: segRef, offset: ['start end', 'end start'] });
  const first = index === 0;
  const last = index === count - 1;
  const turn = cardSide(index) === 'left' ? 1 : -1;
  const opacity = useTransform(p, WIN, [first ? 1 : 0, 1, 1, last ? 1 : 0]);
  const y = useTransform(p, WIN, [first ? 0 : 140, 0, 0, last ? 0 : -140]);
  const scrollRotY = useTransform(p, WIN, [first ? 0 : 28 * turn, 0, 0, last ? 0 : -18 * turn]);
  const scale = useTransform(p, WIN, [first ? 1 : 0.86, 1, 1, last ? 1 : 0.92]);
  // Foto bergerak pelan di dalam bingkai sepanjang segmen (parallax), bingkai sendiri bergerak bersama kartu.
  const imgY = useTransform(p, [0, 1], ['-5%', '5%']);

  // Kemiringan mouse: rotasi kecil di tempat (tanpa geser ke samping), pegas agar lembut.
  const tx = useMotionValue(0);
  const ty = useMotionValue(0);
  const tiltX = useSpring(tx, { stiffness: 140, damping: 20 });
  const tiltY = useSpring(ty, { stiffness: 140, damping: 20 });
  const rotateY = useTransform(() => scrollRotY.get() + tiltY.get());
  const onMove = (e: PointerEvent<HTMLElement>) => {
    if (reduce || e.pointerType !== 'mouse') return;
    const b = e.currentTarget.getBoundingClientRect();
    ty.set((((e.clientX - b.left) / b.width) * 2 - 1) * 6);
    tx.set(-(((e.clientY - b.top) / b.height) * 2 - 1) * 5);
  };
  const onLeave = () => {
    tx.set(0);
    ty.set(0);
  };

  const photo = segment.photos[0];
  return (
    <div className={styles.stack} data-side={cardSide(index)} data-index={index} data-active={active ? 'true' : 'false'} data-stack="" aria-hidden={!active}>
      <motion.figure
        className={styles.frontCard}
        data-card=""
        style={reduce ? STILL_CARD : { opacity, y, scale, rotateY, rotateX: tiltX }}
        onPointerMove={onMove}
        onPointerLeave={onLeave}
      >
        {photo ? (
          <>
            <span className={styles.photoFrame}>
              <motion.img
                src={photo.src}
                alt=""
                width={512}
                height={358}
                decoding="async"
                loading={first ? 'eager' : 'lazy'}
                fetchPriority={first ? 'high' : 'auto'}
                style={reduce ? STILL_IMG : { y: imgY }}
              />
            </span>
            <figcaption className={styles.photoCaption}>
              <span className={styles.photoName}>{photo.name}</span>
              {photo.price && <span className={styles.photoPrice}>{photo.price}</span>}
            </figcaption>
          </>
        ) : (
          <span className={styles.photoEmpty}>{segment.label}</span>
        )}
      </motion.figure>
    </div>
  );
}

function Hud({
  segments,
  active,
  progress,
  visible,
  note,
}: {
  segments: StorySegment[];
  active: number;
  progress: MotionValue<number>;
  visible: boolean;
  note: string;
}) {
  const chapters = segments.slice(1);
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    <div className={styles.hud} data-visible={visible ? 'true' : 'false'} inert={!visible}>
      <p className={styles.hudNote}>{note}</p>
      <nav aria-label="Bab halaman" className={styles.hudNav}>
        <ol>
          {chapters.map((s, i) => (
            <li key={s.id}>
              <a href={`#${s.id}`} aria-current={active === i + 1 ? 'step' : undefined}>
                <span className={styles.hudNum}>{pad(i + 1)}</span>
                <span className={styles.hudLabel}>{s.label}</span>
              </a>
            </li>
          ))}
        </ol>
      </nav>
      <p className={styles.hudCount} aria-hidden="true">
        {pad(Math.max(active, 1))} / {pad(chapters.length)}
      </p>
      <div className={styles.hudBar} aria-hidden="true">
        <motion.div className={styles.hudBarFill} style={{ scaleX: progress }} />
      </div>
    </div>
  );
}

/** Jarak (px) tempat teks mulai memudar sebelum menyentuh kartu, dan margin aman di sekitar kartu. */
const GUARD_FADE = 56;
const GUARD_MARGIN = 12;

/** `note` = keterangan sumber foto di HUD (mis. "Foto: katalog Bacha Coffee"). */
export function PhotoStory({ segments, note = 'Foto: katalog produk' }: { segments: StorySegment[]; note?: string }) {
  const reduce = useReducedMotionSafe();
  const section = useRef<HTMLElement>(null);
  const n = segments.length;
  const segRefs = useMemo(() => Array.from({ length: n }, () => createRef<HTMLDivElement>()), [n]);
  const { scrollYProgress } = useScroll({ target: section, offset: ['start start', 'end end'] });

  const [active, setActive] = useState(0);
  const [inView, setInView] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    const root = section.current;
    if (!root) return;
    const raf0 = requestAnimationFrame(() => setHydrated(true));
    const io = typeof IntersectionObserver !== 'undefined' ? new IntersectionObserver(([e]) => setInView(Boolean(e?.isIntersecting)), { threshold: 0 }) : null;
    io?.observe(root);

    // Penjaga geometris + bab aktif, dihitung per frame saat scroll/resize/pointer (dua frame: nilai motion
    // framer-motion bisa diperbarui setelah callback pertama di frame yang sama).
    let raf = 0;
    let trail = 0;
    const run = () => {
      const vh = window.innerHeight;
      const cards: DOMRect[] = [];
      for (const c of root.querySelectorAll<HTMLElement>('[data-card]')) {
        const stack = c.closest<HTMLElement>('[data-stack]');
        if (!stack || getComputedStyle(stack).visibility === 'hidden') continue;
        if (parseFloat(getComputedStyle(c).opacity) <= 0.02) continue;
        const b = c.getBoundingClientRect();
        if (b.width > 0 && b.bottom > 0 && b.top < vh) cards.push(b);
      }
      for (const el of root.querySelectorAll<HTMLElement>('[data-guard-root] > * > *')) {
        const t = el.getBoundingClientRect();
        let f = 1;
        if (t.bottom > 0 && t.top < vh && t.width > 0) {
          for (const c of cards) {
            const hx = Math.min(t.right, c.right + GUARD_MARGIN) - Math.max(t.left, c.left - GUARD_MARGIN);
            if (hx <= 0) continue;
            const gap = Math.max(t.top - c.bottom, c.top - t.bottom) - GUARD_MARGIN;
            f = Math.min(f, Math.max(0, Math.min(1, gap / GUARD_FADE)));
          }
        }
        if (f >= 0.999) el.style.removeProperty('opacity');
        else el.style.setProperty('opacity', f.toFixed(3), 'important');
      }
      // Bab aktif = segmen yang memuat garis tengah layar.
      let a = 0;
      segRefs.forEach((r, i) => {
        const b = r.current?.getBoundingClientRect();
        if (b && b.top <= vh / 2 && b.bottom > vh / 2) a = i;
      });
      setActive((prev) => (prev === a ? prev : a));
    };
    const tick = () => {
      raf = 0;
      run();
      if (!trail)
        trail = requestAnimationFrame(() => {
          trail = 0;
          run();
        });
    };
    const req = () => {
      if (!raf) raf = requestAnimationFrame(tick);
    };
    req();
    window.addEventListener('scroll', req, { passive: true });
    window.addEventListener('resize', req);
    root.addEventListener('pointermove', req, { passive: true });
    root.addEventListener('pointerleave', req);
    // Tepi bawah kartu tertinggi dalam tata letak (offset* tidak terpengaruh transform) -> `--card-bottom`, dipakai CSS
    // potret agar teks bab dimulai di bawah kartu, termasuk bila nama produk membungkus jadi beberapa baris.
    const measureCards = () => {
      let bottom = 0;
      for (const c of root.querySelectorAll<HTMLElement>('[data-card]')) {
        const stack = c.closest<HTMLElement>('[data-stack]');
        if (stack) bottom = Math.max(bottom, stack.offsetTop + c.offsetTop + c.offsetHeight);
      }
      if (bottom > 0) root.style.setProperty('--card-bottom', `${bottom}px`);
      req();
    };
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measureCards) : null;
    root.querySelectorAll('[data-card]').forEach((c) => ro?.observe(c));
    measureCards();
    // Pegas kemiringan masih bergerak setelah pointer berhenti -> beberapa pemeriksaan susulan.
    const settle = window.setInterval(req, 250);
    return () => {
      cancelAnimationFrame(raf0);
      cancelAnimationFrame(raf);
      cancelAnimationFrame(trail);
      window.clearInterval(settle);
      window.removeEventListener('scroll', req);
      window.removeEventListener('resize', req);
      root.removeEventListener('pointermove', req);
      root.removeEventListener('pointerleave', req);
      ro?.disconnect();
      io?.disconnect();
    };
  }, [segRefs]);

  return (
    <section ref={section} className={styles.story} aria-label="Sorotan produk" data-hydrated={hydrated ? 'true' : 'false'} data-reduce={reduce ? 'true' : 'false'}>
      <div className={styles.stage}>
        <div className={styles.stageBg} aria-hidden="true" />
        <div className={styles.stageTrack}>
          {segments.map((s, i) => (
            <PhotoCard key={s.id} index={i} count={n} segment={s} segRef={segRefs[i]!} reduce={reduce} active={i === active} />
          ))}
        </div>
      </div>

      <div className={styles.segments}>
        {segments.map((s, i) => (
          <Segment key={s.id} index={i} segment={s} reduce={reduce} segRef={segRefs[i]!} />
        ))}
      </div>

      <Hud segments={segments} active={active} progress={scrollYProgress} visible={inView} note={note} />
    </section>
  );
}
