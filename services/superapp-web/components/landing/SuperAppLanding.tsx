/**
 * Landing Super-App (`app.`, brand holding `efn`) — docs/04 §15.7, docs/design/superapp-concept.md §6.
 * Urutan (permintaan user 22 Okt 2026: tampilan "stunning", hero 3D dengan mouse, framer-motion, terlihat mahal, tetap
 * mengutamakan profil & marketplace): hero 3D (foto produk asli + cari) → etalase marketplace per brand → dinding
 * portofolio semua brand EFN (logo, status online/gerai/segera, 23 Okt 2026) → panel profil per brand (palet brand sendiri) → cara pesan → satu akun → penutup.
 * Semua angka, harga & foto dari katalog BFF saat render; copy = DRAF (menunggu Marketing).
 * Katalog gagal -> hero tanpa kartu, etalase kosong yang jujur, panel tanpa foto; mode pesan tetap.
 */
import Link from 'next/link';
import type { OrderMode } from '@/lib/order-modes';
import { OrderModePicker } from '@/components/booking/OrderModePicker';
import { BrandPanel, type BrandPanelProps } from './BrandPanel';
import { BrandWall, type WallBrand } from './BrandWall';
import { Hero3D, type HeroItem, type HeroStats, type SearchChannel } from './Hero3D';
import { Reveal } from './Reveal';
import { Shelf, type ShelfTab } from './Shelf';
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
  heroItems: HeroItem[];
  search: SearchChannel[];
  stats: HeroStats;
  shelf: ShelfTab[];
  portfolio: WallBrand[];
}

export function SuperAppLanding({ title, lead, emphasis, brandNames, modes, panels, loyaltyActive, heroItems, search, stats, shelf, portfolio }: SuperAppLandingProps) {
  return (
    <div className={styles.landing} data-landing="" data-superapp="">
      <noscript>
        <style>{'[data-reveal]{opacity:1!important;transform:none!important;filter:none!important}'}</style>
      </noscript>
      <Hero3D title={title} emphasis={emphasis} lead={lead} items={heroItems} search={search} brandNames={brandNames} stats={stats} logos={portfolio} />

      <Shelf tabs={shelf} />

      <BrandWall
        brands={portfolio}
        kicker="Portofolio EFN"
        title="Satu rumah untuk brand makanan & minuman pilihan."
        lead="Erajaya Food & Nourishment (PT Era Boga Nusantara), bagian dari Erajaya Group, menghadirkan brand bakery, kopi, teh, kuliner, dan supermarket di Indonesia, lalu menyatukannya dalam satu Super-App."
        groupLogo={{ src: '/brands/efn/ebn.png', alt: 'Era Boga Nusantara' }}
      />

      {panels.map((p, i) => (
        <BrandPanel key={p.name} index={i} {...p} />
      ))}

      <section className={styles.band} aria-labelledby="mode-judul">
        <div className={styles.inner}>
          <Reveal>
            <p className={styles.kicker}>
              <span>Cara pesan</span>
              <span aria-hidden="true" className={styles.kickerRule} />
              <span>Pilih sesuai kebutuhan</span>
            </p>
            <h2 id="mode-judul" className={styles.h2}>
              Ambil, pre-order, makan di tempat, atau reservasi.
            </h2>
          </Reveal>
          <OrderModePicker modes={modes} labelledBy="mode-judul" onDark />
        </div>
      </section>

      <section className={`${styles.band} ${styles.account}`} aria-labelledby="akun-judul">
        <div className={`${styles.inner} ${styles.accountGrid}`}>
          <Reveal>
            <p className={styles.kicker}>
              <span>Satu akun</span>
              <span aria-hidden="true" className={styles.kickerRule} />
              <span>Semua brand</span>
            </p>
            <h2 id="akun-judul" className={styles.h2}>
              Masuk sekali dengan nomor HP, belanja di semua brand.
            </h2>
          </Reveal>
          <ul className={styles.benefits}>
            <Reveal as="li">
              <strong>Satu keranjang</strong>
              <span>Pesanan dari beberapa brand dibayar sekali; tiap PT tetap menerbitkan notanya sendiri.</span>
            </Reveal>
            <Reveal as="li" delay={0.08}>
              <strong>Poin lintas brand</strong>
              <span>{loyaltyActive ? 'Poin dari satu brand bisa dipakai di brand lain sesuai kebijakan tiap PT.' : 'Poin lintas brand segera hadir di Super-App.'}</span>
            </Reveal>
            <Reveal as="li" delay={0.16}>
              <strong>Riwayat di satu tempat</strong>
              <span>Pesanan dan reservasi semua brand tercatat di akun Anda.</span>
            </Reveal>
          </ul>
        </div>
      </section>

      <section className={`${styles.band} ${styles.final}`} aria-labelledby="akhir-superapp">
        <div className={`${styles.inner} ${styles.finalGrid}`}>
          <Reveal>
            <h2 id="akhir-superapp" className={styles.finalTitle}>
              Semua brand favorit, <em>satu</em> aplikasi.
            </h2>
          </Reveal>
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
