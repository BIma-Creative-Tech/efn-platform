'use client';

/**
 * Slider hero (docs/04 §18.4, permintaan 27 Okt 2026): slide 1 = hero lama (anak komponen, tidak berubah), slide
 * berikut = banner promo dari Odoo (`efn_promo`, lewat `/api/promo`). Geser otomatis 6 s; berhenti saat hover/fokus/
 * sentuh dan bila `prefers-reduced-motion`. Tanpa banner -> hanya hero lama (tidak ada kontrol slider).
 * Pop-up promo: satu per kunjungan beranda sesuai `frequency`, bisa ditutup (Esc / tombol / latar).
 */
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { EMPTY_PROMO, POPUP_SEEN_KEY, localDay, parsePromoFeed, parseSeen, pickPopup, promoHref, promoImageUrl, type PromoFeed, type PromoItem } from '@/lib/promo';
import styles from './promo.module.css';
import { useReducedMotionSafe } from './useReducedMotionSafe';

const AUTOPLAY_MS = 6000;

function Cta({ item, channelOfBrand, className }: { item: PromoItem; channelOfBrand: (b: string) => string | null; className: string }) {
  const t = promoHref(item.link, channelOfBrand);
  if (!t) return null;
  const label = item.cta_label || 'Lihat promo';
  return t.external ? (
    <a className={className} href={t.href} target="_blank" rel="noopener noreferrer">
      {label}
    </a>
  ) : (
    <Link className={className} href={t.href}>
      {label}
    </Link>
  );
}

function Banner({ item, channelOfBrand }: { item: PromoItem; channelOfBrand: (b: string) => string | null }) {
  const src = promoImageUrl(item.image_sha, 'banner');
  return (
    <div className={styles.banner}>
      {/* eslint-disable-next-line @next/next/no-img-element -- proxy same-origin ber-cache immutable */}
      {src ? <img className={styles.bannerImg} src={src} alt="" loading="lazy" decoding="async" /> : null}
      <div className={styles.bannerShade} aria-hidden="true" />
      <div className={styles.bannerCopy}>
        <p className={styles.bannerTag}>Promo</p>
        <h2 className={styles.bannerTitle}>{item.title}</h2>
        {item.subtitle ? <p className={styles.bannerText}>{item.subtitle}</p> : null}
        <Cta item={item} channelOfBrand={channelOfBrand} className={styles.bannerCta} />
      </div>
    </div>
  );
}

function Popup({ item, channelOfBrand, onClose }: { item: PromoItem; channelOfBrand: (b: string) => string | null; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const src = promoImageUrl(item.image_sha, 'popup');
  return (
    <div className={styles.popupWrap} role="presentation" onClick={onClose}>
      <div className={styles.popup} role="dialog" aria-modal="true" aria-labelledby="promo-popup-judul" onClick={(e) => e.stopPropagation()}>
        <button ref={closeRef} type="button" className={styles.popupClose} onClick={onClose} aria-label="Tutup promo">
          ×
        </button>
        {/* eslint-disable-next-line @next/next/no-img-element -- proxy same-origin ber-cache immutable */}
        {src ? <img className={styles.popupImg} src={src} alt="" decoding="async" /> : null}
        <div className={styles.popupBody}>
          <h2 id="promo-popup-judul" className={styles.popupTitle}>
            {item.title}
          </h2>
          {item.subtitle ? <p className={styles.popupText}>{item.subtitle}</p> : null}
          <Cta item={item} channelOfBrand={channelOfBrand} className={styles.popupCta} />
        </div>
      </div>
    </div>
  );
}

/**
 * `variant="track"` (default): hero & banner sejajar, digeser horizontal (hero berdiri sendiri: `app.`, GL, Bacha, WTZ).
 * `variant="overlay"`: untuk landing scroll-story (PB) — hero TIDAK dibungkus wadah geser (sticky stage tetap utuh);
 * banner menutupi layar pertama saja dan slider kembali ke hero lama begitu halaman digulir.
 */
export function HeroSlider({
  children,
  brandChannels,
  variant = 'track',
  minHeight,
}: {
  children: ReactNode;
  brandChannels: Record<string, string>;
  variant?: 'track' | 'overlay';
  minHeight?: string;
}) {
  const reduce = useReducedMotionSafe();
  const [feed, setFeed] = useState<PromoFeed>(EMPTY_PROMO);
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const [popup, setPopup] = useState<PromoItem | null>(null);
  const channelOfBrand = useCallback((b: string) => brandChannels[b] ?? null, [brandChannels]);

  useEffect(() => {
    let alive = true;
    fetch('/api/promo', { credentials: 'omit' })
      .then((r) => (r.ok ? r.json() : null))
      .then((v: unknown) => {
        if (!alive) return;
        const f = parsePromoFeed(v) ?? EMPTY_PROMO;
        setFeed(f);
        let seen: Record<string, string> = {};
        try {
          seen = parseSeen(window.localStorage.getItem(POPUP_SEEN_KEY));
        } catch {
          // penyimpanan diblokir: pop-up tetap boleh tampil
        }
        const p = pickPopup(f.popups, seen, localDay(new Date()));
        if (p) {
          setPopup(p);
          try {
            window.localStorage.setItem(POPUP_SEEN_KEY, JSON.stringify({ ...seen, [p.id]: localDay(new Date()) }));
          } catch {
            // abaikan
          }
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const slides = 1 + feed.banners.length;
  const [atTop, setAtTop] = useState(true);
  useEffect(() => {
    if (variant !== 'overlay') return;
    const onScroll = () => {
      const top = window.scrollY < 40;
      setAtTop(top);
      if (!top) setIndex(0); // scroll-story PB berjalan di atas hero lama
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [variant]);
  useEffect(() => {
    if (slides < 2 || paused || reduce || !atTop) return;
    const t = setTimeout(() => setIndex((i) => (i + 1) % slides), AUTOPLAY_MS);
    return () => clearTimeout(t);
  }, [index, slides, paused, reduce, atTop]);

  const touch = useRef<number | null>(null);

  if (slides < 2) {
    return (
      <>
        {children}
        {popup ? <Popup item={popup} channelOfBrand={channelOfBrand} onClose={() => setPopup(null)} /> : null}
      </>
    );
  }

  const style = minHeight ? ({ ['--promo-h' as string]: minHeight } as CSSProperties) : undefined;
  const controls = (
    <div className={styles.controls}>
      <button type="button" className={styles.arrow} onClick={() => setIndex((i) => (i + slides - 1) % slides)} aria-label="Slide sebelumnya">
        ‹
      </button>
      <div className={styles.dots} role="tablist" aria-label="Pilih slide">
        {Array.from({ length: slides }, (_, i) => (
          <button key={i} type="button" role="tab" aria-selected={i === index} aria-label={`Slide ${i + 1}`} className={i === index ? styles.dotOn : styles.dot} onClick={() => setIndex(i)} />
        ))}
      </div>
      <button type="button" className={styles.arrow} onClick={() => setIndex((i) => (i + 1) % slides)} aria-label="Slide berikutnya">
        ›
      </button>
    </div>
  );

  if (variant === 'overlay') {
    const b = index > 0 ? feed.banners[index - 1] : undefined;
    return (
      <div className={styles.overlayHost} style={style} aria-roledescription="carousel" aria-label="Sorotan dan promo">
        {children}
        <div
          className={styles.overlayLayer}
          onMouseEnter={() => setPaused(true)}
          onMouseLeave={() => setPaused(false)}
          onFocusCapture={() => setPaused(true)}
          onBlurCapture={() => setPaused(false)}
        >
          {b ? (
            <div key={b.id} className={styles.overlaySlide} aria-roledescription="slide" aria-label={`${index + 1} dari ${slides}: ${b.title}`}>
              <Banner item={b} channelOfBrand={channelOfBrand} />
            </div>
          ) : null}
          {atTop ? controls : null}
        </div>
        {popup ? <Popup item={popup} channelOfBrand={channelOfBrand} onClose={() => setPopup(null)} /> : null}
      </div>
    );
  }

  return (
    <section
      style={style}
      className={styles.slider}
      aria-roledescription="carousel"
      aria-label="Sorotan dan promo"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
      onTouchStart={(e) => {
        touch.current = e.touches[0]?.clientX ?? null;
        setPaused(true);
      }}
      onTouchEnd={(e) => {
        const x0 = touch.current;
        const x1 = e.changedTouches[0]?.clientX;
        touch.current = null;
        if (x0 !== null && x1 !== undefined && Math.abs(x1 - x0) > 40) setIndex((i) => (i + (x1 < x0 ? 1 : slides - 1)) % slides);
      }}
    >
      <div className={styles.track} style={{ transform: `translateX(-${index * 100}%)`, transition: reduce ? 'none' : undefined }}>
        <div className={styles.slide} aria-roledescription="slide" aria-label={`1 dari ${slides}`} aria-hidden={index !== 0} inert={index !== 0 ? true : undefined}>
          {children}
        </div>
        {feed.banners.map((b, i) => (
          <div key={b.id} className={styles.slide} aria-roledescription="slide" aria-label={`${i + 2} dari ${slides}: ${b.title}`} aria-hidden={index !== i + 1} inert={index !== i + 1 ? true : undefined}>
            <Banner item={b} channelOfBrand={channelOfBrand} />
          </div>
        ))}
      </div>
      {controls}
      {popup ? <Popup item={popup} channelOfBrand={channelOfBrand} onClose={() => setPopup(null)} /> : null}
    </section>
  );
}
