import { describe, expect, it } from 'vitest';
import {
  engageMessage,
  isFeatureOff,
  mapEngageCode,
  parsePriceAlerts,
  parseRestockAlerts,
  parseTargetPrice,
  parseWishlist,
  targetPriceError,
  validatePriceAlertCreate,
  validateRestockCreate,
  validateWishlistRef,
} from '../lib/engage';

const rp = (n: number) => `Rp${n}`;

describe('parsers (allowlist, toleran)', () => {
  it('wishlist: membuang item rusak dan kanal tidak valid', () => {
    const items = parseWishlist({
      items: [
        { channel_code: 'GL', product_id: 5, added_at: '2026-11-20T00:00:00Z' },
        { channel_code: 'gl', product_id: 5 },
        { channel_code: 'GL', product_id: -1 },
        { channel_code: 'GL', product_id: '7' },
        null,
        'x',
      ],
    });
    expect(items).toEqual([{ channel_code: 'GL', product_id: 5, added_at: '2026-11-20T00:00:00Z' }]);
    expect(parseWishlist(null)).toEqual([]);
    expect(parseWishlist({ items: 'x' })).toEqual([]);
  });

  it('restock: id string/angka, kanal notifikasi di-filter', () => {
    const items = parseRestockAlerts({
      items: [
        { id: 12, channel_code: 'GL', outlet_code: 'TOKO-01', product_id: 3, channels: ['push', 'sms', 'email'], created_at: 'a', expires_at: 'b' },
        { id: '../x', channel_code: 'GL', outlet_code: 'TOKO-01', product_id: 3, channels: [] },
        { id: 'ok', channel_code: 'GL', outlet_code: 'toko', product_id: 3, channels: [] },
      ],
    });
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ id: '12', channels: ['push', 'email'] });
  });

  it('price alert: target harus bilangan bulat positif', () => {
    const items = parsePriceAlerts({
      items: [
        { channel_code: 'GL', product_id: 3, target_price_idr: 9000 },
        { channel_code: 'GL', product_id: 3, target_price_idr: 90.5 },
        { channel_code: 'GL', product_id: 3, target_price_idr: 0 },
      ],
    });
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ target_price_idr: 9000 });
  });
});

describe('validasi input', () => {
  it('wishlist ref', () => {
    expect(validateWishlistRef({ channel_code: 'GL', product_id: 1 })).toEqual({ ok: true, value: { channel_code: 'GL', product_id: 1 } });
    expect(validateWishlistRef({ channel_code: 'GL', product_id: 1.5 }).ok).toBe(false);
    expect(validateWishlistRef(null).ok).toBe(false);
  });
  it('restock: minimal 1 kanal valid, duplikat dibuang, kanal asing ditolak', () => {
    const base = { channel_code: 'GL', outlet_code: 'TOKO-01', product_id: 9 };
    expect(validateRestockCreate({ ...base, channels: ['push', 'push'] })).toMatchObject({ ok: true, value: { channels: ['push'] } });
    expect(validateRestockCreate({ ...base, channels: [] }).ok).toBe(false);
    expect(validateRestockCreate({ ...base, channels: ['sms'] }).ok).toBe(false);
    expect(validateRestockCreate({ ...base, outlet_code: 'x y', channels: ['push'] }).ok).toBe(false);
  });
  it('price alert create', () => {
    expect(validatePriceAlertCreate({ channel_code: 'GL', product_id: 1, target_price_idr: 5000 }).ok).toBe(true);
    expect(validatePriceAlertCreate({ channel_code: 'GL', product_id: 1, target_price_idr: 0 }).ok).toBe(false);
    expect(validatePriceAlertCreate({ channel_code: 'GL', product_id: 1, target_price_idr: 1.5 }).ok).toBe(false);
    expect(validatePriceAlertCreate({ channel_code: 'GL', product_id: 1, target_price_idr: '5000' }).ok).toBe(false);
  });
});

describe('harga target (UX)', () => {
  it('parseTargetPrice: digit dengan pemisah ribuan', () => {
    expect(parseTargetPrice('12.500')).toBe(12500);
    expect(parseTargetPrice(' 9000 ')).toBe(9000);
    expect(parseTargetPrice('')).toBeNull();
    expect(parseTargetPrice('0')).toBeNull();
    expect(parseTargetPrice('12,5')).toBeNull();
    expect(parseTargetPrice('abc')).toBeNull();
  });
  it('harus lebih rendah dari harga saat ini', () => {
    expect(targetPriceError('9000', 10000, rp)).toBeNull();
    expect(targetPriceError('10000', 10000, rp)).toMatch(/lebih rendah/);
    expect(targetPriceError('11000', 10000, rp)).toMatch(/lebih rendah/);
    expect(targetPriceError('x', 10000, rp)).toMatch(/angka/);
  });
});

describe('peta error', () => {
  it('already_in_stock diteruskan; 409 tak dikenal = batas; 404 = fitur mati; 429/403/400', () => {
    expect(mapEngageCode(409, 'already_in_stock')).toBe('already_in_stock');
    expect(mapEngageCode(409, 'whatever')).toBe('limit_reached');
    expect(mapEngageCode(404, 'http_404')).toBe('feature_unavailable');
    expect(mapEngageCode(429, 'http_429')).toBe('rate_limited');
    expect(mapEngageCode(403, 'x')).toBe('forbidden');
    expect(mapEngageCode(422, 'x')).toBe('invalid_request');
    expect(mapEngageCode(500, 'x')).toBe('upstream_error');
  });
  it('kode nyata BFF (wishlist_full, invalid_target_price, alert_limit_reached) dipetakan ke kode klien', () => {
    expect(mapEngageCode(409, 'wishlist_full')).toBe('wishlist_limit');
    expect(mapEngageCode(409, 'alert_limit_reached')).toBe('limit_reached');
    expect(mapEngageCode(400, 'invalid_target_price')).toBe('price_not_lower');
    expect(mapEngageCode(400, 'channel_unavailable')).toBe('channel_unavailable');
  });
  it('flag mati = 404/501 atau feature_unavailable', () => {
    expect(isFeatureOff(404, 'http_404')).toBe(true);
    expect(isFeatureOff(501, 'x')).toBe(true);
    expect(isFeatureOff(409, 'feature_unavailable')).toBe(true);
    expect(isFeatureOff(409, 'already_in_stock')).toBe(false);
  });
  it('pesan selalu Indonesia dan tidak membocorkan kode asing', () => {
    expect(engageMessage('already_in_stock')).toMatch(/sudah tersedia/);
    expect(engageMessage('kode_aneh')).toBe(engageMessage('upstream_error'));
    expect(engageMessage(null)).toBe(engageMessage('upstream_error'));
  });
});
