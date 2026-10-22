import Link from 'next/link';
import { headers } from 'next/headers';
import { getBrand } from '@/lib/brand-server';
import { allPpnCompanies, COMPANIES, type Brand } from '@/lib/brands';
import { loadCategories, loadChannels, loadProducts, pickChannel } from '@/lib/catalog-server';
import { channelBusinessType, imageUrl, priceDisplay, type CatalogChannel } from '@/lib/catalog';
import { formatRupiah } from '@/lib/format';
import { chapterStats, findCategory, landingFor, pickPhotos, type LandingConfig } from '@/lib/landing-config';
import { orderModes } from '@/lib/order-modes';
import { BRAND_PANEL_COPY, brandShopHref, brandSiteUrl, groupChannelsByBrand } from '@/lib/superapp';
import { brandHasFnb } from '@/lib/booking';
import { BrandLanding, type ChapterData } from '@/components/landing/Landing';
import { SuperAppLanding } from '@/components/landing/SuperAppLanding';
import type { BrandPanelProps } from '@/components/landing/BrandPanel';
import { OrderModePicker } from '@/components/booking/OrderModePicker';

/**
 * Beranda per brand. Satu komponen untuk semua tenant; copy hero dari `Brand.hero` (lib/brands.ts), warna/tipografi dari
 * CSS custom properties (R-T14). Pajak tidak dihitung di sini: label pajak datang dari BFF/Odoo; kalimat pajak hanya
 * membedakan PT ritel (harga termasuk PPN) vs ada PT F&B (pajak dirinci di keranjang) — UX-04.
 *
 * Brand dengan konfigurasi landing (lib/landing.ts; saat ini Paris Baguette) memakai landing page animatif + hero 3D
 * (components/landing). Katalog gagal dimuat -> landing tetap tampil tanpa etalase.
 */
export default async function HomePage() {
  const brand = await getBrand();
  const landing = landingFor(brand.id);
  if (landing) return <LandingHome brand={brand} landing={landing} />;
  // G23 §15.7: beranda `app.` (holding) = landing Super-App bergaya Eraspace.
  if (brand.id === 'efn') return <SuperAppHome brand={brand} />;
  const companies = brand.companies.map((c) => COMPANIES[c].displayName);
  const allRetail = allPpnCompanies(brand.companies);
  const channels = await loadChannels(brand);
  const modes = orderModes({ brand, channels: channels.ok ? channels.data : null, channelParam: null });

  return (
    <div className="stack">
      <section className="hero" aria-labelledby="hero-judul">
        <p className="hero-eyebrow">{brand.hero.eyebrow}</p>
        <h1 id="hero-judul">{brand.hero.title}</h1>
        <p style={{ margin: 0 }}>{brand.hero.lead}</p>
        <p className="hero-actions">
          <Link href="/belanja" className="btn">
            Mulai belanja
          </Link>
          <Link href="/keranjang" className="btn btn-secondary">
            Lihat keranjang
          </Link>
        </p>
      </section>

      <section className="stack" aria-labelledby="mode-pesan">
        <h2 id="mode-pesan" style={{ margin: 0 }}>
          Cara pesan
        </h2>
        <OrderModePicker modes={modes} labelledBy="mode-pesan" />
      </section>

      <section className="card stack" aria-labelledby="belanja-judul">
        <h2 id="belanja-judul" style={{ margin: 0 }}>
          Pesan online, ambil di toko
        </h2>
        <ol style={{ margin: 0, paddingLeft: '1.25rem' }}>
          <li>Pilih produk dan toko tempat Anda mengambil pesanan.</li>
          <li>Masuk dengan nomor HP, lalu bayar sekali dengan QRIS.</li>
          <li>Tunjukkan kode ambil di kasir saat pesanan siap.</li>
        </ol>
        <p className="small muted" style={{ margin: 0 }}>
          {allRetail
            ? 'Harga sudah termasuk pajak.'
            : 'Rincian pajak per PT ditampilkan di keranjang sebelum Anda membayar.'}{' '}
          Dilayani oleh: {companies.join(', ')}.
        </p>
        <p style={{ margin: 0 }}>
          <Link href="/pesanan">Lihat pesanan saya</Link>
        </p>
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Pantau Harga, dapat poin</h2>
        <p style={{ marginBottom: 0 }}>
          Lihat harga lebih murah di toko lain? Foto label harganya dan kirim lewat{' '}
          <Link href="/pantau-harga">Pantau Harga</Link>. Laporan yang valid mendapat poin.
        </p>
      </section>
    </div>
  );
}

/** Fakta layanan kanal dari katalog (tanpa klaim manual): ambil di gerai, menit siap tercepat, pre-order. */
function channelFacts(channels: CatalogChannel[], productCount: number, more: boolean): string[] {
  const outlets = channels.flatMap((c) => c.outlets.filter((o) => o.pickup_enabled));
  const minPrep = outlets.length > 0 ? Math.min(...outlets.map((o) => o.prep_minutes)) : null;
  return [
    productCount > 0 ? `${productCount}${more ? '+' : ''} pilihan` : null,
    channels.some((c) => c.fulfilment.pickup) ? 'Ambil di gerai' : null,
    minPrep !== null && minPrep > 0 ? `± ${minPrep} menit` : null,
    channels.some((c) => c.outlets.some((o) => o.slot_enabled === true)) ? 'Pre-order' : null,
  ].filter((v): v is string => Boolean(v));
}

/** Landing Super-App (`app.`): data katalog per brand dimuat paralel; gagal -> panel tanpa foto/fakta. */
async function SuperAppHome({ brand }: { brand: Brand }) {
  const host = (await headers()).get('host');
  const channels = await loadChannels(brand);
  const all = channels.ok ? channels.data : [];
  const groups = groupChannelsByBrand(all);
  const panels: Omit<BrandPanelProps, 'index'>[] = await Promise.all(
    groups.map(async (g) => {
      const ch = g.channels[0] ?? null;
      const products = ch ? await loadProducts({ channel: ch.channel_code, limit: 24 }) : null;
      const items = products?.ok ? products.data.items : [];
      const bt = ch ? channelBusinessType(ch) : null;
      const photos = pickPhotos(items, [], 3)
        .map((p) => {
          const src = imageUrl(p.image_sha, 512);
          return src ? { src, name: p.name, price: formatRupiah(priceDisplay(p, bt).amount) } : null;
        })
        .filter((v): v is { src: string; name: string; price: string } => v !== null);
      const t = g.brand.theme;
      const copy = BRAND_PANEL_COPY[g.brand.id as keyof typeof BRAND_PANEL_COPY];
      // Panel brand gelap (PB) memakai palet gelapnya; brand terang memakai latar alternatif + teks brand.
      const palette =
        t.scheme === 'dark'
          ? { bg: t.surfaceAlt, text: t.text, muted: t.textMuted, accent: t.accent, card: t.surface, onAccent: t.primaryContrast }
          : { bg: t.surfaceAlt, text: t.text, muted: t.textMuted, accent: t.primary, card: t.surface, onAccent: t.primaryContrast };
      return {
        name: g.brand.name,
        kind: copy.kind,
        line: copy.line,
        palette,
        photos,
        facts: channelFacts(g.channels, items.length, Boolean(products?.ok && products.data.next_cursor)),
        shopHref: brandShopHref(g),
        siteHref: brandSiteUrl(host, g.brand),
        reserveHref: brandHasFnb(g.brand) ? '/reservasi' : null,
      };
    }),
  );
  return (
    <SuperAppLanding
      title="Semua rasa favorit, satu akun."
      lead={brand.hero.lead}
      emphasis="satu"
      brandNames={groups.map((g) => ({ name: g.brand.name, href: `#brand-${groups.indexOf(g)}` }))}
      modes={orderModes({ brand, channels: channels.ok ? all : null, channelParam: null })}
      panels={panels}
      loyaltyActive={all.some((c) => c.loyalty?.earn === true || c.loyalty?.redeem === true)}
    />
  );
}

async function LandingHome({ brand, landing }: { brand: Brand; landing: LandingConfig }) {
  const channels = await loadChannels(brand);
  const channel = channels.ok ? pickChannel(channels.data, null) : null;
  const [categories, products] = channel
    ? await Promise.all([loadCategories(channel.channel_code), loadProducts({ channel: channel.channel_code, limit: 24 })])
    : [null, null];
  const cats = categories?.ok ? categories.data : [];
  // Per bab: produk kategori -> statistik (jumlah, harga terendah, favorit) + foto kartu (produk bergambar). Gagal -> kosong.
  const businessType = channel ? channelBusinessType(channel) : null;
  const chapters: ChapterData[] = await Promise.all(
    landing.chapters.map(async (m): Promise<ChapterData> => {
      const cat = channel ? findCategory(cats, m.categoryName) : null;
      if (!channel || !cat) return { stats: null, photos: [] };
      const r = await loadProducts({ channel: channel.channel_code, category: cat.category_id, limit: 50 });
      if (!r.ok) return { stats: null, photos: [] };
      return { stats: chapterStats(r.data.items, r.data.next_cursor, businessType), photos: pickPhotos(r.data.items, m.featured, 3) };
    }),
  );
  // Kartu pembuka: produk unggulan, tapi bukan foto depan salah satu bab (agar tidak tampil dua kali);
  // cadangan = foto bab pertama.
  const fronts = new Set(chapters.map((c) => c.photos[0]?.product_id).filter((v) => v !== undefined));
  const pool = [...chapters.flatMap((c) => c.photos), ...(products?.ok ? products.data.items : [])].filter((p) => !fronts.has(p.product_id));
  const heroPhotos = pickPhotos(pool, landing.heroFeatured, 3);
  return (
    <BrandLanding
      brand={brand}
      landing={landing}
      channel={channel}
      multiChannel={channels.ok && channels.data.length > 1}
      categories={cats}
      products={products?.ok ? products.data.items : []}
      chapters={chapters}
      heroPhotos={heroPhotos.length > 0 ? heroPhotos : (chapters[0]?.photos ?? [])}
      orderModes={orderModes({ brand, channels: channels.ok ? channels.data : null, channelParam: channels.ok && channels.data.length > 1 ? (channel?.channel_code ?? null) : null })}
    />
  );
}
