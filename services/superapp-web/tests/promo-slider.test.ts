/** Promo Super-App web: slider hero + pop-up (docs/04 §18.4). */
import { describe, expect, it } from 'vitest';
import { brandChannelMap, localDay, parsePromoFeed, parsePromoLink, parseSeen, pickPopup, promoHref, promoImageUrl, type PromoItem } from '@/lib/promo';

const SHA = 'a'.repeat(64);
const it1 = (id: string, extra: Record<string, unknown> = {}) => ({ id, title: `Promo ${id}`, subtitle: 'x', cta_label: 'Lihat', link: { type: 'brand', brand: 'PB' }, image_sha: SHA, ...extra });

describe('parsePromoFeed', () => {
  it('allowlist: id `<PT>-<n>`, judul wajib, field asing dibuang, frequency default once', () => {
    const f = parsePromoFeed({ banners: [it1('PB-1', { secret: 1 }), it1('bad'), it1('PB-2', { title: '' })], popups: [it1('GL-3', { frequency: 'daily' }), it1('GL-4')] })!;
    expect(f.banners.map((b) => b.id)).toEqual(['PB-1']);
    expect(f.banners[0]).not.toHaveProperty('secret');
    expect(f.popups.map((p) => p.frequency)).toEqual(['daily', 'once']);
    expect(parsePromoFeed({ banners: [] })).toBeNull();
    expect(parsePromoFeed(null)).toBeNull();
  });
  it('tautan berbahaya ditolak', () => {
    expect(parsePromoLink({ type: 'url', url: 'javascript:alert(1)' })).toBeNull();
    expect(parsePromoLink({ type: 'url', url: 'http://a.test' })).toBeNull();
    expect(parsePromoLink({ type: 'url', url: 'https://a.test/x"onerror' })).toBeNull();
  });
});

describe('promoHref / promoImageUrl', () => {
  it('brand -> katalog kanal; produk -> detail; url -> eksternal', () => {
    const ch = (b: string) => (b === 'PB' ? 'PB' : null);
    expect(promoHref({ type: 'brand', brand: 'PB' }, ch)).toEqual({ href: '/belanja?channel=PB', external: false });
    expect(promoHref({ type: 'brand', brand: 'ZZ' }, ch)).toEqual({ href: '/belanja', external: false });
    expect(promoHref({ type: 'product', channel: 'GL', product_id: 9 }, ch)).toEqual({ href: '/belanja/9?channel=GL', external: false });
    expect(promoHref({ type: 'url', url: 'https://x.test' }, ch)).toEqual({ href: 'https://x.test', external: true });
    expect(promoHref(null, ch)).toBeNull();
  });
  it('gambar lewat origin sendiri, lebar sesuai jenis', () => {
    expect(promoImageUrl(SHA, 'banner')).toBe(`/api/promo/images/${SHA}?w=1600`);
    expect(promoImageUrl(SHA, 'popup')).toBe(`/api/promo/images/${SHA}?w=1024`);
    expect(promoImageUrl('../x', 'banner')).toBeNull();
  });
});

describe('pickPopup (frequency)', () => {
  const p = (id: string, frequency: PromoItem['frequency']): PromoItem => ({ id, title: 't', subtitle: '', cta_label: '', link: null, image_sha: null, frequency });
  it('once: sekali; daily: sekali per hari; every_open: selalu', () => {
    expect(pickPopup([p('PB-1', 'once')], {}, '2026-10-27')?.id).toBe('PB-1');
    expect(pickPopup([p('PB-1', 'once')], { 'PB-1': '2026-10-01' }, '2026-10-27')).toBeNull();
    expect(pickPopup([p('PB-2', 'daily')], { 'PB-2': '2026-10-27' }, '2026-10-27')).toBeNull();
    expect(pickPopup([p('PB-2', 'daily')], { 'PB-2': '2026-10-26' }, '2026-10-27')?.id).toBe('PB-2');
    expect(pickPopup([p('PB-3', 'every_open')], { 'PB-3': '2026-10-27' }, '2026-10-27')?.id).toBe('PB-3');
    expect(pickPopup([p('PB-1', 'once'), p('PB-2', 'daily')], { 'PB-1': '2026-10-27' }, '2026-10-27')?.id).toBe('PB-2');
  });
  it('riwayat rusak = kosong; tanggal lokal', () => {
    expect(parseSeen('{bad')).toEqual({});
    expect(parseSeen(JSON.stringify({ 'PB-1': '2026-10-27', x: 'y', 'PB-2': 5 }))).toEqual({ 'PB-1': '2026-10-27' });
    expect(localDay(new Date(2026, 0, 5))).toBe('2026-01-05');
  });
});

describe('brandChannelMap (semua brand)', () => {
  it('kode brand, PT, dan kanal -> kanal online', () => {
    const m = brandChannelMap([
      { channel_code: 'PB', company_code: 'PB', brand_code: 'PB' },
      { channel_code: 'BCR', company_code: 'BCR', brand_code: 'BACHA' },
      { channel_code: 'GL', company_code: 'GL', brand_code: null },
    ]);
    expect(m).toMatchObject({ PB: 'PB', BACHA: 'BCR', BCR: 'BCR', GL: 'GL' });
  });
});
