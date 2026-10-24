/**
 * Landing page brand (beranda sebelum e-commerce). Server Component: data katalog dimuat di server (BFF); scroll story
 * foto & animasi di komponen klien (PhotoStory, ShowcaseRail). Konfigurasi per brand: lib/landing-config.ts; warna:
 * token brand (CSS custom properties, R-T14). Desain & aturan: docs/design/landing-pages.md.
 *
 * Warna PB = warna resmi parisbaguette.co.id: latar navy #002663, teks putih, sekunder #aebcd3, aksen kuning #fcb134
 * (lib/brands.ts, skema gelap). Semua angka & foto (pilihan, harga, menit siap, metode bayar) dari katalog.
 *
 * Katalog tidak tersedia -> story tetap tampil tanpa foto/statistik; etalase & tabel gerai disembunyikan; CTA tetap
 * ke /belanja (yang menampilkan pesan "belum tersedia" sendiri).
 */
import Link from 'next/link';
import { Fragment } from 'react';
import { brandHasTableService } from '@/lib/booking';
import type { Brand } from '@/lib/brands';
import { channelBusinessType, imageUrl, priceDisplay, type CatalogCategory, type CatalogChannel, type ProductSummary } from '@/lib/catalog';
import { formatRupiah } from '@/lib/format';
import { categoryHref, type ChapterStats, type LandingConfig } from '@/lib/landing-config';
import type { OrderMode } from '@/lib/order-modes';
import { OrderModePicker } from '@/components/booking/OrderModePicker';
import { PhotoStory, type StoryPhoto, type StorySegment } from './PhotoStory';
import { ShowcaseRail, type RailItem } from './Rail';
import styles from './landing.module.css';

export interface ChapterData {
  stats: ChapterStats | null;
  /** Produk terpilih untuk kartu foto (sudah bergambar), urutan = depan, latar 1, latar 2. */
  photos: ProductSummary[];
}

interface Props {
  brand: Brand;
  landing: LandingConfig;
  channel: CatalogChannel | null;
  /** true = brand punya > 1 kanal -> tautan menyertakan `?channel=`. */
  multiChannel: boolean;
  categories: CatalogCategory[];
  products: ProductSummary[];
  /** Per bab (urutan = landing.chapters). */
  chapters: ChapterData[];
  /** Foto kartu pembuka. */
  heroPhotos: ProductSummary[];
  /** G23 §15.7: mode pesan (band tambahan sesudah story; story & geraknya tidak diubah). Kosong -> band disembunyikan. */
  orderModes?: OrderMode[];
  /** G24: produk kategori katering (landing.catering.categoryName); kosong -> band tanpa rak foto. */
  cateringProducts?: ProductSummary[];
  /** true = ada outlet pre-order berjadwal -> CTA katering ke `?mode=preorder`. */
  preorder?: boolean;
}

const PAYMENT_LABEL: Record<string, string> = { qris: 'QRIS', counter: 'Bayar di kasir' };

/** Judul dipecah per kata untuk animasi CSS (tanpa JS); kata `emphasis` diberi gaya khusus. */
function Headline({ text, emphasis, id }: { text: string; emphasis: string | null; id: string }) {
  const words = text.split(/\s+/).filter(Boolean);
  return (
    <h1 id={id} className={styles.title}>
      {words.map((w, i) => {
        const plain = w.replace(/[^\p{L}\p{N}]/gu, '').toLowerCase();
        const em = emphasis !== null && plain === emphasis.toLowerCase();
        return (
          <Fragment key={`${w}-${i}`}>
            <span className={styles.word}>
              <span className={em ? styles.wordEm : undefined} style={{ ['--i' as string]: i }}>
                {w}
              </span>
            </span>
            {/* Spasi DI LUAR inline-block: di dalamnya spasi akhir dibuang browser (judul jadi menempel). */}
            {i < words.length - 1 ? ' ' : null}
          </Fragment>
        );
      })}
    </h1>
  );
}

export function BrandLanding({
  brand,
  landing,
  channel,
  multiChannel,
  categories,
  products,
  chapters,
  heroPhotos,
  orderModes = [],
  cateringProducts = [],
  preorder = false,
}: Props) {
  const ch = channel && multiChannel ? channel.channel_code : null;
  const shop = ch ? `/belanja?channel=${encodeURIComponent(ch)}` : '/belanja';
  const businessType = channel ? channelBusinessType(channel) : null;
  const fnb = businessType === 'fnb';
  // Kalimat QR meja hanya untuk brand dengan layanan meja (PB/BCF); kios takeaway (WTZ) tidak.
  const tableService = brandHasTableService(brand);

  const toPhoto = (p: ProductSummary): StoryPhoto | null => {
    const src = imageUrl(p.image_sha, 512);
    if (!src) return null;
    return { src, name: p.name, price: formatRupiah(priceDisplay(p, businessType).amount) };
  };
  const photos = (list: ProductSummary[]) => list.map(toPhoto).filter((v): v is StoryPhoto => v !== null);

  const outlets = channel?.outlets.filter((o) => o.pickup_enabled) ?? [];
  const minPrep = outlets.length > 0 ? Math.min(...outlets.map((o) => o.prep_minutes)) : null;
  const payLabels = (channel?.payment_modes ?? []).map((m) => PAYMENT_LABEL[m]).filter((v): v is string => Boolean(v));
  const specs = [channel?.fulfilment.pickup ? 'Ambil di gerai' : null, ...payLabels, minPrep !== null ? `± ${minPrep} menit` : null].filter(
    (v): v is string => Boolean(v),
  );
  const pad = (n: number) => String(n).padStart(2, '0');

  const segments: StorySegment[] = [
    {
      id: 'pembuka',
      label: 'Pembuka',
      photos: photos(heroPhotos),
      content: (
        <div className={styles.heroCopy}>
          <Headline text={brand.hero.title} emphasis={landing.emphasis} id="landing-judul" />
          <p className={styles.lead}>{brand.hero.lead}</p>
          <p className={styles.actions}>
            <Link href={shop} className={styles.ctaPrimary}>
              Pesan sekarang
            </Link>
            <a href="#cara-pesan" className={styles.ctaGhost}>
              Cara pesan
            </a>
          </p>
          {specs.length > 0 && (
            <ul className={styles.specs} aria-label="Ringkasan layanan">
              {specs.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          )}
          <p className={styles.scrollCue} aria-hidden="true">
            Gulir
          </p>
        </div>
      ),
    },
    ...landing.chapters.map((m, i): StorySegment => {
      const data = chapters[i];
      const st = data?.stats ?? null;
      return {
        id: `bab-${m.id}`,
        label: m.label,
        photos: photos(data?.photos ?? []),
        content: (
          <article className={styles.chapter} aria-labelledby={`bab-${m.id}-judul`}>
            <p className={styles.kicker}>
              <span>{pad(i + 1)}</span>
              <span aria-hidden="true" className={styles.kickerRule} />
              <span>{m.label}</span>
            </p>
            <h2 id={`bab-${m.id}-judul`} className={styles.chapterTitle}>
              {m.title}
            </h2>
            <p className={styles.chapterBody}>{m.body}</p>
            {st && st.count > 0 && (
              <dl className={styles.stats}>
                <div>
                  <dt>Pilihan</dt>
                  <dd>
                    {st.count}
                    {st.more ? '+' : ''}
                  </dd>
                </div>
                {st.minPrice !== null && (
                  <div>
                    <dt>Mulai dari</dt>
                    <dd>
                      {formatRupiah(st.minPrice)}
                      {fnb && <span className={styles.statNote}>sebelum pajak</span>}
                    </dd>
                  </div>
                )}
                {st.names.length > 0 && (
                  <div className={styles.statWide}>
                    <dt>Favorit</dt>
                    <dd className={styles.statNames}>{st.names.join(' · ')}</dd>
                  </div>
                )}
              </dl>
            )}
            <Link href={categoryHref(categories, m.categoryName, ch)} className={styles.chapterLink}>
              {m.linkLabel}
              <span aria-hidden="true">→</span>
            </Link>
          </article>
        ),
      };
    }),
  ];

  const toRail = (list: ProductSummary[]): RailItem[] =>
    channel
      ? list.slice(0, 12).map((p) => {
          const price = priceDisplay(p, businessType);
          return {
            id: p.product_id,
            name: p.name,
            href: `/belanja/${p.product_id}?channel=${encodeURIComponent(channel.channel_code)}`,
            imageSrc: imageUrl(p.image_sha, 512),
            price: formatRupiah(price.amount),
            note: price.taxNote,
          };
        })
      : [];
  const rail = toRail(products);
  const cat = landing.catering;
  const cateringRail = cat ? toRail(cateringProducts) : [];
  // CTA katering: pre-order berjadwal bila ada outlet slot (G23 §15.4), kalau tidak kategori katering biasa.
  const cateringHref = cat
    ? preorder
      ? `/belanja?${new URLSearchParams({ ...(ch ? { channel: ch } : {}), mode: 'preorder' })}`
      : categoryHref(categories, cat.categoryName, ch)
    : shop;
  const topCats = categories.filter((c) => c.parent_id === null).slice(0, 8);

  return (
    <div className={styles.landing} data-landing="">
      <noscript>
        <style>{'[data-reveal]{opacity:1!important;transform:none!important;filter:none!important;clip-path:none!important}'}</style>
      </noscript>

      <PhotoStory segments={segments} />

      {orderModes.length > 0 && (
        <section className={`${styles.band} ${styles.modesBand}`} aria-labelledby="mode-judul">
          <div className={styles.inner}>
            <p className={styles.kicker}>
              <span>Cara pesan</span>
              <span aria-hidden="true" className={styles.kickerRule} />
              <span>Pilih sesuai kebutuhan</span>
            </p>
            <h2 id="mode-judul" className={styles.h2} style={{ marginBottom: '2.5rem' }}>
              Ambil, pre-order, makan di tempat, atau reservasi.
            </h2>
            <OrderModePicker modes={orderModes} labelledBy="mode-judul" onDark />
          </div>
        </section>
      )}

      {rail.length > 0 && (
        <section className={`${styles.band} ${styles.showcase}`} aria-labelledby="etalase-judul">
          <div className={styles.inner}>
            <div className={styles.sectionHead}>
              <div>
                <p className={styles.kicker}>
                  <span>Menu</span>
                  <span aria-hidden="true" className={styles.kickerRule} />
                  <span>{fnb ? 'Harga sebelum pajak' : 'Harga termasuk pajak'}</span>
                </p>
                <h2 id="etalase-judul" className={styles.h2}>
                  Dari etalase kami
                </h2>
              </div>
              <Link href={shop} className={styles.chapterLink}>
                Lihat semua menu<span aria-hidden="true">→</span>
              </Link>
            </div>
            <ShowcaseRail
              items={rail}
              labelledBy="etalase-judul"
              toolbar={
                topCats.length > 0 ? (
                  <nav aria-label="Kategori menu">
                    <ul className={styles.cats}>
                      {topCats.map((c) => (
                        <li key={c.category_id}>
                          <Link className={styles.cat} href={categoryHref(categories, c.name, ch)}>
                            {c.name}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </nav>
                ) : null
              }
            />
            <p className={styles.fine}>
              {fnb
                ? 'Harga belum termasuk pajak. Rincian pajak ditampilkan di keranjang sebelum Anda membayar.'
                : 'Harga sudah termasuk pajak; total akhir dihitung saat checkout.'}
            </p>
          </div>
        </section>
      )}

      {cat && (
        <section id="katering" className={`${styles.band} ${styles.catering}`} aria-labelledby="katering-judul" data-catering="">
          <div className={styles.inner}>
            <div className={styles.sectionHead}>
              <div>
                <p className={styles.kicker}>
                  <span>Katering</span>
                  <span aria-hidden="true" className={styles.kickerRule} />
                  <span>{preorder ? 'Pre-order, ambil di gerai' : 'Ambil di gerai'}</span>
                </p>
                <h2 id="katering-judul" className={styles.h2}>
                  {cat.title}
                </h2>
              </div>
              <p className={styles.cateringLead}>{cat.body}</p>
            </div>
            <ul className={styles.occasions}>
              {cat.occasions.map((o) => (
                <li key={o.title}>
                  <span className={styles.stepTitle}>{o.title}</span>
                  <span className={styles.stepBody}>{o.body}</span>
                </li>
              ))}
            </ul>
            {cateringRail.length > 0 && <ShowcaseRail items={cateringRail} labelledBy="katering-judul" />}
            {cat.guide.length > 0 && (
              <div className={styles.outlets}>
                <h3 className={styles.h3}>Berapa banyak yang perlu dipesan?</h3>
                <table className={styles.specTable}>
                  <thead>
                    <tr>
                      <th scope="col">Jumlah tamu</th>
                      <th scope="col">Saran</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cat.guide.map((g) => (
                      <tr key={g.title}>
                        <td>{g.title}</td>
                        <td>{g.body}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className={styles.actions} style={{ marginTop: '2.5rem' }}>
              <Link href={cateringHref} className={styles.ctaPrimary}>
                {preorder ? 'Pre-order katering' : 'Lihat menu katering'}
              </Link>
              {cat.contactHref && cat.contactLabel && (
                <a href={cat.contactHref} className={styles.ctaGhost} rel="noopener noreferrer" target="_blank">
                  {cat.contactLabel}
                  <span className="sr-only"> (membuka situs resmi di tab baru)</span>
                </a>
              )}
            </p>
          </div>
        </section>
      )}

      <section id="cara-pesan" className={`${styles.band} ${styles.how}`} aria-labelledby="cara-judul" tabIndex={-1}>
        <div className={styles.inner}>
          <p className={styles.kicker}>
            <span>Cara pesan</span>
            <span aria-hidden="true" className={styles.kickerRule} />
            <span>3 langkah</span>
          </p>
          <h2 id="cara-judul" className={styles.h2}>
            Pesan dari ponsel, ambil di gerai.
          </h2>
          <ol className={styles.steps}>
            <li>
              <span className={styles.stepTitle}>{landing.pickStep}</span>
              <span className={styles.stepBody}>Tentukan gerai tempat Anda mengambil pesanan.</span>
            </li>
            <li>
              <span className={styles.stepTitle}>Masuk dengan nomor HP</span>
              <span className={styles.stepBody}>Bayar sekali dengan {payLabels.length > 0 ? payLabels.join(' atau ') : 'metode yang tersedia'}.</span>
            </li>
            <li>
              <span className={styles.stepTitle}>Tunjukkan kode ambil di kasir</span>
              <span className={styles.stepBody}>
                {minPrep !== null ? `Pesanan biasanya siap sekitar ${minPrep} menit setelah dibayar.` : 'Kami beri tahu saat pesanan siap.'}
              </span>
            </li>
          </ol>

          {outlets.length > 0 && (
            <div className={styles.outlets}>
              <h3 className={styles.h3}>Gerai yang melayani ambil sendiri</h3>
              <table className={styles.specTable}>
                <thead>
                  <tr>
                    <th scope="col">Gerai</th>
                    <th scope="col">Waktu siap</th>
                  </tr>
                </thead>
                <tbody>
                  {outlets.map((o) => (
                    <tr key={o.outlet_code}>
                      <td>{o.public_name}</td>
                      <td>± {o.prep_minutes} menit</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <section className={`${styles.band} ${styles.final}`} aria-labelledby="akhir-judul">
        <div className={`${styles.inner} ${styles.finalGrid}`}>
          <h2 id="akhir-judul" className={styles.finalTitle}>
            Lapar? Pesan sekarang, ambil saat <em>siap</em>.
          </h2>
          <div className={styles.finalSide}>
            <p className={styles.finalBody}>
              {tableService
                ? 'Sedang duduk di gerai? Pindai kode QR di meja Anda untuk memesan langsung dari ponsel, tanpa perlu antre di kasir.'
                : 'Pesan dari ponsel sebelum tiba, lalu ambil di kasir tanpa perlu antre.'}
            </p>
            <p className={styles.actions}>
              <Link href={shop} className={styles.ctaPrimary}>
                Pesan sekarang
              </Link>
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
