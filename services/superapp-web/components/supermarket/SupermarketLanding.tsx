import Link from 'next/link';
import type { CSSProperties } from 'react';
import { COMPANIES, type Brand } from '@/lib/brands';
import type { CatalogCategory } from '@/lib/catalog';
import { splitEmphasis } from '@/lib/landing-config';
import type { OrderMode } from '@/lib/order-modes';
import { AISLES, aisleHref, featuredAisles, type Aisle, type AisleIconId, type StoreSummary } from '@/lib/supermarket';
import { OrderModePicker } from '@/components/booking/OrderModePicker';
import { FeatureGate } from '@/components/engage/FeatureGate';
import { Reveal } from '@/components/landing/Reveal';
import { AisleIcon } from './AisleIcon';
import { HeroStage, type StageProduct, type StageTicket } from './HeroStage';
import styles from './supermarket.module.css';

/**
 * Landing supermarket (tenant `supermarket`, PT SMD). Tata letak "golden ratio": hero 1.618fr : 1fr, grid bento 8 kolom
 * (bentang Fibonacci 5 + 3), skala tipografi & spasi φ (supermarket.module.css). Gaya glassmorphism di atas latar mesh
 * gradasi yang bergerak pelan; semua animasi mati bila `prefers-reduced-motion`.
 *
 * Fitur = fitur situs brand lain yang relevan untuk ritel: katalog + cari, pickup, pre-order, poin, wishlist/notify-me/
 * price alert (di halaman produk, digerbang flag BFF), Pantau Harga, promo banner (HeroSlider di app/page.tsx).
 * TIDAK ada dine-in/QR meja/reservasi (supermarket tanpa area makan; `tableService: false`).
 * Data (harga, jumlah, toko, menit siap) dari katalog BFF; katalog gagal -> landing tetap tampil tanpa angka/foto.
 */
export interface PickCard {
  id: number;
  name: string;
  price: string;
  taxNote: string;
  src: string | null;
  href: string;
  icon: AisleIconId;
  tone: readonly [string, string];
  aisle: string;
}

export interface AisleStat {
  /** Jumlah produk (mis. "24+"), atau null. */
  count: string | null;
  /** Foto produk pertama bergambar, atau null. */
  src: string | null;
}

export interface SupermarketLandingProps {
  brand: Brand;
  /** Kanal untuk tautan (hanya bila brand punya > 1 kanal), selain itu null. */
  channelParam: string | null;
  categories: CatalogCategory[];
  aisleStats: Record<string, AisleStat>;
  picks: PickCard[];
  stage: StageProduct[];
  ticket: StageTicket;
  stores: StoreSummary[];
  modes: OrderMode[];
  loyaltyActive: boolean;
  productCount: string | null;
  fastestPrep: number | null;
}

function toneStyle(tone: readonly [string, string]): CSSProperties {
  return { '--tone-a': tone[0], '--tone-b': tone[1] } as CSSProperties;
}

function HeroTitle({ title, emphasis }: { title: string; emphasis: string }) {
  const [before, em, after] = splitEmphasis(title, emphasis);
  let i = 0;
  const words = (text: string) =>
    text
      .split(/(\s+)/)
      .filter((w) => w.length > 0)
      .map((w) =>
        /^\s+$/.test(w) ? (
          w
        ) : (
          <span key={`w${i}`} className={styles.word} style={{ '--i': i++ } as CSSProperties}>
            {w}
          </span>
        ),
      );
  return (
    <h1 id="sm-hero-judul" className={styles.heroTitle}>
      {words(before)}
      {em && (
        <em className={styles.heroEm}>
          <span className={styles.word} style={{ '--i': i++ } as CSSProperties}>
            {em}
          </span>
        </em>
      )}
      {words(after)}
    </h1>
  );
}

function AisleTile({ aisle, stat, href, big, index }: { aisle: Aisle; stat: AisleStat | undefined; href: string; big: boolean; index: number }) {
  return (
    <Reveal className={`${styles.tile} ${big ? styles.tileBig : ''}`} delay={index * 0.06}>
      <Link href={href} className={styles.tileLink} style={toneStyle(aisle.tone)}>
        <span className={styles.tileArt} aria-hidden="true">
          {stat?.src ? (
            // eslint-disable-next-line @next/next/no-img-element -- proxy gambar same-origin
            <img src={stat.src} alt="" loading="lazy" decoding="async" />
          ) : (
            <AisleIcon id={aisle.icon} size={big ? 96 : 56} />
          )}
        </span>
        <span className={styles.tileGlass}>
          <span className={styles.tileKicker}>{stat?.count ? `${stat.count} produk` : 'Lorong'}</span>
          <span className={styles.tileTitle}>{aisle.label}</span>
          {big && <span className={styles.tileBlurb}>{aisle.blurb}</span>}
          <span className={styles.tileArrow} aria-hidden="true">
            →
          </span>
        </span>
      </Link>
    </Reveal>
  );
}

export function SupermarketLanding(p: SupermarketLandingProps) {
  const { brand, channelParam, categories } = p;
  const shop = channelParam ? `/belanja?${new URLSearchParams({ channel: channelParam })}` : '/belanja';
  const preorder = p.modes.find((m) => m.id === 'preorder' && m.href);
  const featured = featuredAisles();
  const stats = [
    p.productCount ? { value: p.productCount, label: 'produk siap dipesan' } : null,
    p.stores.length > 0 ? { value: String(p.stores.length), label: 'toko untuk ambil' } : null,
    p.fastestPrep ? { value: `± ${p.fastestPrep}`, label: 'menit hingga siap' } : null,
  ].filter((v): v is { value: string; label: string } => v !== null);

  return (
    <div className={styles.page} data-landing="" data-supermarket="">
      <noscript>
        <style>{'[data-reveal]{opacity:1!important;transform:none!important;filter:none!important}'}</style>
      </noscript>
      <div className={styles.mesh} aria-hidden="true">
        <span className={styles.blobA} />
        <span className={styles.blobB} />
        <span className={styles.blobC} />
      </div>

      {/* ===== Hero: 1.618fr : 1fr ===== */}
      <section className={`${styles.wrap} ${styles.hero}`} aria-labelledby="sm-hero-judul">
        <div className={styles.heroCopy}>
          <p className={styles.eyebrow}>
            <span className={styles.eyebrowDot} aria-hidden="true" />
            {brand.hero.eyebrow}
          </p>
          <HeroTitle title={brand.hero.title} emphasis="siap" />
          <p className={styles.heroLead}>{brand.hero.lead}</p>

          <form className={styles.search} action="/belanja" method="get" role="search" aria-label="Cari produk">
            {channelParam && <input type="hidden" name="channel" value={channelParam} />}
            <label htmlFor="sm-cari" className="visually-hidden">
              Cari produk
            </label>
            <svg className={styles.searchIcon} viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
              <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="1.8" />
              <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
            <input id="sm-cari" name="q" type="search" placeholder="Cari sayur, susu, roti…" maxLength={80} autoComplete="off" />
            <button type="submit" className={styles.searchBtn}>
              Cari
            </button>
          </form>

          <div className={styles.heroActions}>
            <Link href={shop} className={styles.btnPrimary}>
              Mulai belanja
            </Link>
            {preorder?.href && (
              <Link href={preorder.href} className={styles.btnGlass}>
                Pre-order jadwal ambil
              </Link>
            )}
          </div>

          <ul className={styles.trust} aria-label="Keunggulan">
            <li>Bayar di aplikasi</li>
            <li>Ambil tanpa antre</li>
            {p.loyaltyActive && <li>Kumpulkan poin</li>}
          </ul>
        </div>
        <div className={styles.heroVisual}>
          <HeroStage products={p.stage} ticket={p.ticket} />
        </div>
      </section>

      {/* ===== Marquee lorong ===== */}
      <div className={styles.marquee} aria-hidden="true">
        <div className={styles.marqueeTrack}>
          {[...AISLES, ...AISLES].map((a, i) => (
            <span key={`${a.id}-${i}`} className={styles.marqueeItem}>
              <AisleIcon id={a.icon} size={20} />
              {a.label}
            </span>
          ))}
        </div>
      </div>

      {/* ===== Lorong (bento golden) ===== */}
      <section className={`${styles.wrap} ${styles.section}`} aria-labelledby="sm-lorong">
        <Reveal className={styles.sectionHead}>
          <p className={styles.kicker}>Lorong pilihan</p>
          <h2 id="sm-lorong" className={styles.h2}>
            Semua kebutuhan dapur, <em>tertata rapi.</em>
          </h2>
          <p className={styles.sectionLead}>Telusuri lorong favorit seperti di toko, lengkap dengan harga dan stok per toko.</p>
        </Reveal>
        <div className={styles.bento}>
          {featured.map((a, i) => (
            <AisleTile key={a.id} aisle={a} stat={p.aisleStats[a.id]} href={aisleHref(categories, a, channelParam)} big={i === 0 || i === 3} index={i} />
          ))}
        </div>
        <Reveal>
          <ul className={styles.chips} aria-label="Lorong lainnya">
            {AISLES.filter((a) => !featured.includes(a)).map((a) => (
              <li key={a.id}>
                <Link href={aisleHref(categories, a, channelParam)} className={styles.chip} style={toneStyle(a.tone)}>
                  <AisleIcon id={a.icon} size={18} />
                  {a.label}
                </Link>
              </li>
            ))}
            <li>
              <Link href={shop} className={`${styles.chip} ${styles.chipAll}`}>
                Semua produk →
              </Link>
            </li>
          </ul>
        </Reveal>
      </section>

      {/* ===== Rak produk ===== */}
      {p.picks.length > 0 && (
        <section className={`${styles.section} ${styles.shelfSection}`} aria-labelledby="sm-pilihan">
          <div className={styles.wrap}>
            <Reveal className={styles.sectionHeadRow}>
              <div>
                <p className={styles.kicker}>Pilihan minggu ini</p>
                <h2 id="sm-pilihan" className={styles.h2}>
                  Segar dari rak, <em>langsung ke keranjang.</em>
                </h2>
              </div>
              <Link href={shop} className={styles.linkArrow}>
                Lihat semua →
              </Link>
            </Reveal>
          </div>
          <ul className={styles.shelf} aria-label="Produk pilihan">
            {p.picks.map((c, i) => (
              <Reveal as="li" key={c.id} className={styles.shelfItem} delay={Math.min(i, 6) * 0.05}>
                <Link href={c.href} className={styles.product}>
                  <span className={styles.productPhoto} style={toneStyle(c.tone)}>
                    {c.src ? (
                      // eslint-disable-next-line @next/next/no-img-element -- proxy gambar same-origin
                      <img src={c.src} alt="" loading="lazy" decoding="async" />
                    ) : (
                      <AisleIcon id={c.icon} size={64} />
                    )}
                    <span className={styles.productTag}>{c.aisle}</span>
                  </span>
                  <span className={styles.productBody}>
                    <span className={styles.productName}>{c.name}</span>
                    <span className={styles.productPrice}>
                      {c.price}
                      {c.taxNote && <small> · {c.taxNote}</small>}
                    </span>
                  </span>
                </Link>
              </Reveal>
            ))}
          </ul>
        </section>
      )}

      {/* ===== Cara belanja (pickup) ===== */}
      <section className={`${styles.wrap} ${styles.section}`} aria-labelledby="sm-cara">
        <div className={styles.how}>
          <Reveal className={styles.howIntro}>
            <p className={styles.kicker}>Ambil di toko</p>
            <h2 id="sm-cara" className={styles.h2}>
              Tiga langkah, <em>tanpa antre.</em>
            </h2>
            <p className={styles.sectionLead}>Belanja kapan saja dari ponsel. Kami siapkan pesanan Anda, Anda tinggal datang dan menunjukkan kode ambil.</p>
            <h3 id="sm-mode" className={styles.h3}>
              Pilih cara pesan
            </h3>
            <OrderModePicker modes={p.modes} labelledBy="sm-mode" />
          </Reveal>
          <ol className={styles.steps}>
            {[
              { t: 'Pilih produk & toko', b: 'Lihat harga dan stok di toko terdekat, lalu masukkan ke keranjang.' },
              { t: 'Bayar di aplikasi', b: 'Pesanan diproses setelah pembayaran diterima. Aman, tanpa uang tunai.' },
              { t: 'Tunjukkan kode ambil', b: 'Saat pesanan siap, tunjukkan kode QR di kasir. Selesai.' },
            ].map((s, i) => (
              <Reveal as="li" key={s.t} className={styles.step} delay={i * 0.08}>
                <span className={styles.stepNo} aria-hidden="true">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <span className={styles.stepBody}>
                  <span className={styles.stepTitle}>{s.t}</span>
                  <span className={styles.stepText}>{s.b}</span>
                </span>
              </Reveal>
            ))}
          </ol>
        </div>
      </section>

      {/* ===== Angka (dari katalog) ===== */}
      {stats.length > 0 && (
        <section className={`${styles.wrap} ${styles.statsSection}`} aria-label="Sekilas">
          <Reveal className={styles.stats}>
            {stats.map((s) => (
              <div key={s.label} className={styles.stat}>
                <span className={styles.statValue}>{s.value}</span>
                <span className={styles.statLabel}>{s.label}</span>
              </div>
            ))}
          </Reveal>
        </section>
      )}

      {/* ===== Lebih dari belanja ===== */}
      <section className={`${styles.wrap} ${styles.section}`} aria-labelledby="sm-member">
        <Reveal className={styles.sectionHead}>
          <p className={styles.kicker}>Untuk member</p>
          <h2 id="sm-member" className={styles.h2}>
            Belanja makin <em>cerdas.</em>
          </h2>
        </Reveal>
        <div className={styles.perks}>
          {p.loyaltyActive && (
            <Reveal className={`${styles.perk} ${styles.perkWide}`}>
              <span className={styles.perkIcon} aria-hidden="true">
                <AisleIcon id="star" size={28} />
              </span>
              <h3 className={styles.h3}>Poin di setiap belanja</h3>
              <p>Kumpulkan poin dari belanja online dengan satu akun nomor HP.</p>
              <Link href="/akun" className={styles.linkArrow}>
                Cek poin saya →
              </Link>
            </Reveal>
          )}
          <FeatureGate feature={['wishlist', 'restock', 'priceAlert']}>
            <Reveal className={styles.perk}>
              <span className={styles.perkIcon} aria-hidden="true">
                <AisleIcon id="fruit" size={28} />
              </span>
              <h3 className={styles.h3}>Favorit & pengingat</h3>
              <p>Simpan produk favorit, dapat kabar saat stok kembali atau harga turun.</p>
              <Link href="/wishlist" className={styles.linkArrow}>
                Buka favorit →
              </Link>
            </Reveal>
          </FeatureGate>
          <Reveal className={styles.perk}>
            <span className={styles.perkIcon} aria-hidden="true">
              <AisleIcon id="jar" size={28} />
            </span>
            <h3 className={styles.h3}>Pantau Harga</h3>
            <p>Lihat harga lebih murah di tempat lain? Foto label harganya. Laporan valid mendapat poin.</p>
            <Link href="/pantau-harga" className={styles.linkArrow}>
              Kirim laporan →
            </Link>
          </Reveal>
          <Reveal className={styles.perk}>
            <span className={styles.perkIcon} aria-hidden="true">
              <AisleIcon id="home" size={28} />
            </span>
            <h3 className={styles.h3}>Riwayat pesanan</h3>
            <p>Pantau status pembayaran dan pesanan, lalu buka kode ambil saat pesanan siap.</p>
            <Link href="/pesanan" className={styles.linkArrow}>
              Pesanan saya →
            </Link>
          </Reveal>
        </div>
      </section>

      {/* ===== Toko ===== */}
      {p.stores.length > 0 && (
        <section className={`${styles.wrap} ${styles.section}`} aria-labelledby="sm-toko">
          <Reveal className={styles.sectionHead}>
            <p className={styles.kicker}>Toko kami</p>
            <h2 id="sm-toko" className={styles.h2}>
              Ambil di toko <em>terdekat.</em>
            </h2>
          </Reveal>
          <ul className={styles.stores}>
            {p.stores.map((s, i) => (
              <Reveal as="li" key={s.code} className={styles.store} delay={Math.min(i, 5) * 0.05}>
                <span className={styles.storePin} aria-hidden="true" />
                <span className={styles.storeName}>{s.name}</span>
                <span className={styles.storeMeta}>
                  {s.prepMinutes > 0 ? `Siap ± ${s.prepMinutes} menit` : 'Ambil di toko'}
                  {s.preorder ? ' · Pre-order' : ''}
                </span>
              </Reveal>
            ))}
          </ul>
        </section>
      )}

      {/* ===== Penutup ===== */}
      <section className={`${styles.wrap} ${styles.closingSection}`} aria-labelledby="sm-penutup">
        <Reveal className={styles.closing}>
          <h2 id="sm-penutup" className={styles.closingTitle}>
            Daftar belanja beres, <em>tinggal ambil.</em>
          </h2>
          <p className={styles.closingLead}>Harga sudah termasuk pajak. Dilayani oleh {brand.companies.map((c) => COMPANIES[c].displayName).join(', ')}.</p>
          <div className={styles.heroActions}>
            <Link href={shop} className={styles.btnPrimary}>
              Mulai belanja
            </Link>
            <Link href="/masuk" className={styles.btnGlass}>
              Masuk dengan nomor HP
            </Link>
          </div>
        </Reveal>
      </section>
    </div>
  );
}
