/**
 * Landing Super-App (`app.`, brand holding `efn`) — docs/04 §15.7, docs/design/superapp-concept.md §6.
 * Pola Eraspace (satu akun, banyak brand) dengan bahasa visual landing PB (Fraunces/Geist, kicker mono, garis tipis,
 * gerak dipicu scroll/pointer). Urutan: hero + deretan brand → mode pesan → panel per brand (palet & foto produk asli
 * brand itu) → satu akun & poin → penutup. Semua angka & foto dari katalog BFF; copy = DRAF.
 * Katalog gagal -> panel tetap tampil tanpa foto/fakta; mode pesan tetap (halaman tujuan menjelaskan ketersediaan).
 */
import Link from 'next/link';
import { Fragment } from 'react';
import type { OrderMode } from '@/lib/order-modes';
import { OrderModePicker } from '@/components/booking/OrderModePicker';
import { BrandPanel, type BrandPanelProps } from './BrandPanel';
import styles from './superapp.module.css';

export interface SuperAppLandingProps {
  title: string;
  lead: string;
  /** Kata judul yang diberi penekanan. */
  emphasis: string;
  brandNames: { name: string; href: string }[];
  modes: OrderMode[];
  panels: Omit<BrandPanelProps, 'index'>[];
  loyaltyActive: boolean;
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

export function SuperAppLanding({ title, lead, emphasis, brandNames, modes, panels, loyaltyActive }: SuperAppLandingProps) {
  return (
    <div className={styles.landing} data-landing="" data-superapp="">
      <section className={styles.hero} aria-labelledby="superapp-judul">
        <div className={styles.inner}>
          <Headline text={title} emphasis={emphasis} />
          <p className={styles.lead}>{lead}</p>
          <nav aria-label="Brand di Super-App">
            <ul className={styles.marquee}>
              {brandNames.map((b) => (
                <li key={b.name}>
                  <a href={b.href}>{b.name}</a>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </section>

      <section className={styles.band} aria-labelledby="mode-judul">
        <div className={styles.inner}>
          <p className={styles.kicker}>
            <span>Cara pesan</span>
            <span aria-hidden="true" className={styles.kickerRule} />
            <span>Pilih sesuai kebutuhan</span>
          </p>
          <h2 id="mode-judul" className={styles.h2}>
            Ambil, pre-order, makan di tempat, atau reservasi.
          </h2>
          <OrderModePicker modes={modes} labelledBy="mode-judul" onDark />
        </div>
      </section>

      {panels.map((p, i) => (
        <BrandPanel key={p.name} index={i} {...p} />
      ))}

      <section className={`${styles.band} ${styles.account}`} aria-labelledby="akun-judul">
        <div className={`${styles.inner} ${styles.accountGrid}`}>
          <div>
            <p className={styles.kicker}>
              <span>Satu akun</span>
              <span aria-hidden="true" className={styles.kickerRule} />
              <span>Semua brand</span>
            </p>
            <h2 id="akun-judul" className={styles.h2}>
              Masuk sekali dengan nomor HP, belanja di semua brand.
            </h2>
          </div>
          <ul className={styles.benefits}>
            <li>
              <strong>Satu keranjang</strong>
              <span>Pesanan dari beberapa brand dibayar sekali; tiap PT tetap menerbitkan notanya sendiri.</span>
            </li>
            <li>
              <strong>Poin lintas brand</strong>
              <span>{loyaltyActive ? 'Poin dari satu brand bisa dipakai di brand lain sesuai kebijakan tiap PT.' : 'Poin lintas brand segera hadir di Super-App.'}</span>
            </li>
            <li>
              <strong>Riwayat di satu tempat</strong>
              <span>Pesanan dan reservasi semua brand tercatat di akun Anda.</span>
            </li>
          </ul>
        </div>
      </section>

      <section className={`${styles.band} ${styles.final}`} aria-labelledby="akhir-superapp">
        <div className={`${styles.inner} ${styles.finalGrid}`}>
          <h2 id="akhir-superapp" className={styles.finalTitle}>
            Semua brand favorit, <em>satu</em> aplikasi.
          </h2>
          <p className={styles.actions}>
            <Link href="/belanja" className={styles.ctaPrimary}>
              Mulai belanja
            </Link>
            <Link href="/masuk" className={styles.ctaGhost}>
              Masuk
            </Link>
          </p>
        </div>
      </section>
    </div>
  );
}
