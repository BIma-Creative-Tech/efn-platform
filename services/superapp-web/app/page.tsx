import Link from 'next/link';
import { headers } from 'next/headers';
import { getBrand } from '@/lib/brand-server';
import { allPpnCompanies, COMPANIES, type Brand } from '@/lib/brands';
import { loadCategories, loadChannels, loadProducts, pickChannel } from '@/lib/catalog-server';
import { channelBusinessType, imageUrl, priceDisplay, type CatalogChannel, type ProductSummary } from '@/lib/catalog';
import { formatRupiah } from '@/lib/format';
import { chapterStats, findCategory, landingFor, pickPhotos, type LandingConfig } from '@/lib/landing-config';
import { orderModes } from '@/lib/order-modes';
import { BRAND_PANEL_COPY, brandShopHref, brandSiteUrl, groupChannelsByBrand, HERO_MAX_ITEMS, interleave } from '@/lib/superapp';
import { brandHasTableService } from '@/lib/booking';
import { BrandLanding, type ChapterData } from '@/components/landing/Landing';
import { SuperAppLanding } from '@/components/landing/SuperAppLanding';
import type { BrandPanelProps } from '@/components/landing/BrandPanel';
import type { HeroItem } from '@/components/landing/Hero3D';
import type { ShelfTab } from '@/components/landing/Shelf';
import type { WallBrand } from '@/components/landing/BrandWall';
import { PORTFOLIO } from '@/lib/portfolio';
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
  const loaded = await Promise.all(
    groups.map(async (g) => {
      // Kanal pertama brand yang punya produk (Bacha: butik BCR + kafe BCF; kafe bisa belum punya produk online).
      for (const ch of g.channels) {
        const r = await loadProducts({ channel: ch.channel_code, limit: 24 });
        if (r.ok && r.data.items.length > 0) return { g, ch, items: r.data.items, more: Boolean(r.data.next_cursor) };
      }
      return { g, ch: g.channels[0] ?? null, items: [] as ProductSummary[], more: false };
    }),
  );
  const panels: Omit<BrandPanelProps, 'index'>[] = loaded.map(({ g, ch, items, more }) => {
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
      facts: channelFacts(g.channels, items.length, more),
      shopHref: ch ? `/belanja?${new URLSearchParams({ channel: ch.channel_code })}` : brandShopHref(g),
      siteHref: brandSiteUrl(host, g.brand),
      reserveHref: brandHasTableService(g.brand) ? '/reservasi' : null,
    };
  });

  const productHref = (id: number, channel: string) => `/belanja/${id}?${new URLSearchParams({ channel })}`;
  // Hero 3D: foto produk asli bergiliran antar-brand (bukan satu brand saja).
  const heroItems: HeroItem[] = interleave(
    loaded.map(({ g, ch, items }) =>
      ch
        ? items
            .filter((p) => p.image_sha)
            .map((p) => ({
              id: p.product_id,
              name: p.name,
              brand: g.brand.name,
              price: formatRupiah(priceDisplay(p, channelBusinessType(ch)).amount),
              src: imageUrl(p.image_sha, 512) ?? '',
              href: productHref(p.product_id, ch.channel_code),
            }))
            .filter((v) => v.src)
        : [],
    ),
    HERO_MAX_ITEMS,
    (v) => v.id,
  );
  const shelf: ShelfTab[] = loaded
    .filter(({ ch }) => ch !== null)
    .map(({ g, ch, items, more }) => {
      const bt = channelBusinessType(ch!);
      return {
        id: g.brand.id,
        label: g.brand.name,
        kind: BRAND_PANEL_COPY[g.brand.id as keyof typeof BRAND_PANEL_COPY].kind,
        shopHref: `/belanja?${new URLSearchParams({ channel: ch!.channel_code })}`,
        total: items.length > 0 ? `${items.length}${more ? '+' : ''}` : '',
        products: items.slice(0, 8).map((p) => {
          const pd = priceDisplay(p, bt);
          return { id: p.product_id, name: p.name, price: formatRupiah(pd.amount), taxNote: pd.taxNote, src: imageUrl(p.image_sha, 512), href: productHref(p.product_id, ch!.channel_code) };
        }),
      };
    });
  const total = loaded.reduce((n, l) => n + l.items.length, 0);
  // Dinding portofolio: semua brand EFN (lib/portfolio.ts); brand dengan katalog online diberi tautan + fakta katalog.
  const portfolio: WallBrand[] = PORTFOLIO.map((b) => {
    const l = b.tenant ? loaded.find((x) => x.g.brand.id === b.tenant) : undefined;
    const online = Boolean(l?.ch && l.items.length > 0);
    return {
      ...b,
      // Brand berlabel online tanpa katalog yang termuat -> diperlakukan "di gerai" (tidak menjanjikan pesan online).
      status: b.status === 'online' && !online ? 'store' : b.status,
      href: online && l?.ch ? `/belanja?${new URLSearchParams({ channel: l.ch.channel_code })}` : null,
      fact: online && l ? `${l.items.length}${l.more ? '+' : ''} produk` : null,
    };
  });
  const outlets = new Set(all.flatMap((c) => c.outlets.filter((o) => o.pickup_enabled).map((o) => o.outlet_code))).size;
  return (
    <SuperAppLanding
      title="Semua rasa favorit, satu akun."
      lead={brand.hero.lead}
      emphasis="satu"
      brandNames={groups.map((g, i) => ({ name: g.brand.name, href: `#brand-${i}` }))}
      modes={orderModes({ brand, channels: channels.ok ? all : null, channelParam: null })}
      panels={panels}
      loyaltyActive={all.some((c) => c.loyalty?.earn === true || c.loyalty?.redeem === true)}
      heroItems={heroItems}
      search={loaded.filter(({ ch }) => ch !== null).map(({ g, ch }) => ({ code: ch!.channel_code, label: g.brand.name }))}
      stats={{ brands: PORTFOLIO.length, products: total > 0 ? `${total}${loaded.some((l) => l.more) ? '+' : ''}` : null, outlets }}
      shelf={shelf}
      portfolio={portfolio}
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
  // G24: band katering — produk kategori katering (mis. PB "Cakes", Wetzel's "Catering"); gagal -> band tanpa rak.
  const cateringCat = channel && landing.catering ? findCategory(cats, landing.catering.categoryName) : null;
  const catering = channel && cateringCat ? await loadProducts({ channel: channel.channel_code, category: cateringCat.category_id, limit: 12 }) : null;
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
      cateringProducts={catering?.ok ? catering.data.items : []}
      preorder={Boolean(channel?.outlets.some((o) => o.slot_enabled === true))}
    />
  );
}
