/**
 * G22 — poin sebagai alat bayar per PT (docs/04 §14.C.2), info poin hangus (§14.D.6), parsing pesanan dengan poin.
 */
import { describe, expect, it } from 'vitest';
import { parseQuote } from '@/lib/cart';
import { cashDue, ONLINE_ERROR_MESSAGES, parseOrderDetail, onlineErrorMessage } from '@/lib/orders';
import {
  canRedeemAny,
  daysUntil,
  expiryNotice,
  formatDateId,
  normalizeDate,
  parsePointsExpiry,
  parsePointsInput,
  parseQuotePoints,
  POINTS_REFRESH_CODES,
  pointsErrorMessage,
  resolveAllocation,
  sanitizePointsRedeem,
  suggestMaxPoints,
} from '@/lib/points';

const QUOTE_ID = '9b1d2c3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e';
const ORDER_ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';

const rawPoints = (over: Record<string, unknown> = {}) => ({
  balance_available: 1200,
  point_value_idr: 100,
  groups: [{ channel_code: 'PB', redeem_allowed: true, min_points: 50, max_points: 375, max_value_idr: 37500 }],
  ...over,
});
const points = () => parseQuotePoints(rawPoints())!;
const PB = { channel_code: 'PB', amount_payable: 100000 };

describe('parseQuotePoints', () => {
  it('allowlist + nilai wajar', () => {
    const p = parseQuotePoints({ ...rawPoints(), secret: 1 })!;
    expect(p).toEqual({
      balance_available: 1200,
      point_value_idr: 100,
      groups: [{ channel_code: 'PB', redeem_allowed: true, min_points: 50, max_points: 375, max_value_idr: 37500 }],
    });
    expect(JSON.stringify(p)).not.toContain('secret');
  });
  it('rusak → null; kanal duplikat/buruk dibuang; max 0 = tidak boleh', () => {
    for (const bad of [null, [], {}, rawPoints({ balance_available: -1 }), rawPoints({ point_value_idr: 0 }), rawPoints({ groups: 'x' }), rawPoints({ balance_available: 1.5 })]) {
      expect(parseQuotePoints(bad)).toBeNull();
    }
    const p = parseQuotePoints(
      rawPoints({
        groups: [
          { channel_code: 'PB', redeem_allowed: true, max_points: 0 },
          { channel_code: 'PB', redeem_allowed: true, max_points: 5 },
          { channel_code: 'bad code', redeem_allowed: true, max_points: 5 },
          { channel_code: 'GL', redeem_allowed: false, min_points: 10, max_points: 5 },
        ],
      }),
    )!;
    expect(p.groups.map((g) => [g.channel_code, g.redeem_allowed])).toEqual([
      ['PB', false],
      ['GL', false],
    ]);
  });
  it('canRedeemAny: saldo 0 atau tak ada kelompok boleh → false', () => {
    expect(canRedeemAny(points())).toBe(true);
    expect(canRedeemAny(parseQuotePoints(rawPoints({ balance_available: 0 })))).toBe(false);
    expect(canRedeemAny(parseQuotePoints(rawPoints({ groups: [{ channel_code: 'PB', redeem_allowed: false, max_points: 9 }] })))).toBe(false);
    expect(canRedeemAny(null)).toBe(false);
  });
  it('parseQuote membawa points (null bila BFF tidak mengirim)', () => {
    const base = {
      quote_id: QUOTE_ID,
      cart_version: 1,
      amount_payable_total: 100000,
      groups: [{ channel_code: 'PB', company_code: 'PB', outlet_code: 'RESTO-1', fulfilment: 'pickup', lines: [], tax_groups: [], amount_total: 100000, amount_payable: 100000, issues: [] }],
    };
    expect(parseQuote(base)!.points).toBeNull();
    expect(parseQuote({ ...base, points: rawPoints() })!.points!.balance_available).toBe(1200);
  });
});

describe('resolveAllocation (UX; BFF tetap validator)', () => {
  it('isian valid: nilai poin, porsi tunai, badan permintaan', () => {
    const a = resolveAllocation(points(), [PB], { PB: '300' });
    expect(a.valid).toBe(true);
    expect(a.lines).toEqual([{ channel_code: 'PB', points: 300, value_idr: 30000, cash_idr: 70000, error: null }]);
    expect(a.total_points).toBe(300);
    expect(a.total_value_idr).toBe(30000);
    expect(a.total_cash_idr).toBe(70000);
    expect(a.request).toEqual([{ channel_code: 'PB', points: 300 }]);
  });
  it('kosong / 0 → tidak memakai poin', () => {
    const cases: Record<string, string>[] = [{}, { PB: '' }, { PB: '0' }, { PB: '  ' }];
    for (const inputs of cases) {
      const a = resolveAllocation(points(), [PB], inputs);
      expect(a.valid).toBe(true);
      expect(a.request).toEqual([]);
      expect(a.total_cash_idr).toBe(100000);
    }
    expect(resolveAllocation(null, [PB], { PB: '300' }).request).toEqual([]);
  });
  it('di bawah minimum / di atas maksimum / format salah / PT tidak boleh', () => {
    expect(resolveAllocation(points(), [PB], { PB: '10' }).lines[0]!.error).toBe('below_minimum');
    expect(resolveAllocation(points(), [PB], { PB: '376' }).lines[0]!.error).toBe('exceeds_max');
    expect(resolveAllocation(points(), [PB], { PB: '1.5' }).lines[0]!.error).toBe('invalid');
    expect(resolveAllocation(points(), [PB], { PB: '-3' }).lines[0]!.error).toBe('invalid');
    expect(resolveAllocation(points(), [PB], { PB: '1234567890' }).lines[0]!.error).toBe('invalid');
    const gl = { channel_code: 'GL', amount_payable: 50000 };
    const r = resolveAllocation(points(), [PB, gl], { PB: '100', GL: '100' });
    expect(r.lines.find((l) => l.channel_code === 'GL')!.error).toBe('not_allowed');
    expect(r.valid).toBe(false);
    expect(r.request).toEqual([{ channel_code: 'PB', points: 100 }]); // hanya entri valid
  });
  it('porsi tunai minimal Rp1 (cover_full) — sisa < 1 ditolak', () => {
    const a = resolveAllocation(parseQuotePoints(rawPoints({ groups: [{ channel_code: 'PB', redeem_allowed: true, min_points: 1, max_points: 500, max_value_idr: 50000 }] })), [{ channel_code: 'PB', amount_payable: 20000 }], { PB: '200' });
    expect(a.lines[0]!.error).toBe('cover_full');
    expect(a.valid).toBe(false);
    const ok = resolveAllocation(parseQuotePoints(rawPoints({ groups: [{ channel_code: 'PB', redeem_allowed: true, min_points: 1, max_points: 500, max_value_idr: 50000 }] })), [{ channel_code: 'PB', amount_payable: 20000 }], { PB: '199' });
    expect(ok.valid).toBe(true);
    expect(ok.total_cash_idr).toBe(100);
  });
  it('total poin lintas PT tidak boleh melebihi saldo tersedia', () => {
    const p = parseQuotePoints(
      rawPoints({
        balance_available: 400,
        groups: [
          { channel_code: 'PB', redeem_allowed: true, min_points: 50, max_points: 375, max_value_idr: 37500 },
          { channel_code: 'GL', redeem_allowed: true, min_points: 50, max_points: 375, max_value_idr: 37500 },
        ],
      }),
    );
    const groups = [PB, { channel_code: 'GL', amount_payable: 100000 }];
    const a = resolveAllocation(p, groups, { PB: '300', GL: '200' });
    expect(a.insufficient_balance).toBe(true);
    expect(a.valid).toBe(false);
    expect(resolveAllocation(p, groups, { PB: '200', GL: '200' }).valid).toBe(true);
    // "Maks": saldo dikurangi isian PT lain, dibatasi max kanal, nol bila di bawah minimum
    expect(suggestMaxPoints(p!, groups, { PB: '300' }, 'GL')).toBe(100);
    expect(suggestMaxPoints(p!, groups, {}, 'PB')).toBe(375);
    expect(suggestMaxPoints(p!, groups, { PB: '380' }, 'GL')).toBe(0);
    expect(suggestMaxPoints(p!, groups, {}, 'ZZ')).toBe(0);
  });
  it('parsePointsInput', () => {
    expect(parsePointsInput('')).toBeNull();
    expect(parsePointsInput(' 42 ')).toBe(42);
    expect(parsePointsInput('4e2')).toBe('invalid');
  });
  it('pesan error ber-Indonesia memuat batas', () => {
    const info = points().groups[0];
    expect(pointsErrorMessage('below_minimum', info)).toMatch(/Minimal 50 poin/);
    expect(pointsErrorMessage('exceeds_max', info)).toMatch(/Maksimal 375 poin/);
    expect(pointsErrorMessage('cover_full', info)).toMatch(/Rp1/);
  });
});

describe('sanitizePointsRedeem (Route Handler)', () => {
  it('allowlist dua field, satu entri per kanal, poin bulat > 0', () => {
    expect(sanitizePointsRedeem([{ channel_code: 'PB', points: 300, value_idr: 1 }])).toEqual([{ channel_code: 'PB', points: 300 }]);
    expect(sanitizePointsRedeem(undefined)).toBeUndefined();
    expect(sanitizePointsRedeem(null)).toBeUndefined();
    expect(sanitizePointsRedeem([])).toBeUndefined();
    for (const bad of ['x', {}, [{ channel_code: 'PB', points: 0 }], [{ channel_code: 'PB', points: 1.5 }], [{ channel_code: 'PB', points: '3' }], [{ channel_code: 'pb', points: 3 }], [{ channel_code: 'PB', points: 3 }, { channel_code: 'PB', points: 4 }], [{ channel_code: 'PB', points: 2_000_000_000 }], [1], Array.from({ length: 11 }, (_, i) => ({ channel_code: `C${i}X`, points: 1 }))]) {
      expect(sanitizePointsRedeem(bad)).toBeNull();
    }
  });
});

describe('pesanan dengan poin', () => {
  const detail = (over: Record<string, unknown> = {}, part: Record<string, unknown> = {}) => ({
    order_id: ORDER_ID,
    status: 'awaiting_payment',
    amount_payable: 100000,
    amount_points_value: 30000,
    amount_cash: 70000,
    parts: [{ channel_code: 'PB', company_code: 'PB', outlet_code: 'RESTO-1', outlet_name: 'PB Kemang', status: 'placed', lines: [], tax_groups: [], amount_total: 100000, amount_payable: 100000, points_redeemed: 300, points_value_idr: 30000, amount_cash: 70000, points_earned: 0, points_restored: 0, ...part }],
    ...over,
  });
  it('field poin di-parse; tanpa field → 0 / null (kompatibel)', () => {
    const o = parseOrderDetail(detail({}, { points_earned: 10, points_restored: 5 }))!;
    expect(o.amount_points_value).toBe(30000);
    expect(o.amount_cash).toBe(70000);
    expect(o.parts[0]).toMatchObject({ points_redeemed: 300, points_value_idr: 30000, amount_cash: 70000, points_earned: 10, points_restored: 5 });
    expect(cashDue(o)).toBe(70000);
    const legacy = parseOrderDetail({ order_id: ORDER_ID, status: 'paid', amount_payable: 5000, parts: [{ channel_code: 'GL', status: 'confirmed' }] })!;
    expect(legacy.amount_points_value).toBe(0);
    expect(legacy.amount_cash).toBeNull();
    expect(cashDue(legacy)).toBe(5000);
    expect(legacy.parts[0]).toMatchObject({ points_redeemed: 0, points_value_idr: 0, amount_cash: null, points_earned: 0, points_restored: 0 });
  });
  it('kode error poin punya pesan Indonesia & memicu hitung ulang', () => {
    for (const code of ['insufficient_points', 'below_minimum', 'exceeds_max_pct', 'points_unavailable', 'points_cover_full_amount']) {
      expect(Object.keys(ONLINE_ERROR_MESSAGES)).toContain(code);
      expect(onlineErrorMessage(code)).not.toMatch(/Terjadi kendala/);
    }
    expect(POINTS_REFRESH_CODES).toEqual(['insufficient_points', 'below_minimum', 'exceeds_max_pct', 'points_cover_full_amount']);
    expect(POINTS_REFRESH_CODES).not.toContain('points_unavailable');
  });
});

describe('info poin hangus (§14.D.6)', () => {
  it('normalizeDate / parsePointsExpiry defensif', () => {
    expect(normalizeDate('2027-01-12')).toBe('2027-01-12');
    expect(normalizeDate('2027-01-12T00:00:00Z')).toBe('2027-01-12');
    for (const bad of ['2027-02-30', '12-01-2027', '', null, 5, '2027-13-01']) expect(normalizeDate(bad)).toBeNull();
    expect(parsePointsExpiry({ inactive_since: '2026-01-12', expires_on: '2027-01-12', notice_sent: true })).toEqual({ inactive_since: '2026-01-12', expires_on: '2027-01-12', notice_sent: true });
    expect(parsePointsExpiry({ expires_on: 'kemarin' })).toEqual({ inactive_since: null, expires_on: null, notice_sent: false });
    expect(parsePointsExpiry('x')).toBeNull();
    expect(parsePointsExpiry(undefined)).toBeNull();
  });
  it('formatDateId: tanggal kalender tanpa geser zona', () => {
    expect(formatDateId('2027-01-12')).toMatch(/^12 Januari 2027$/);
    expect(formatDateId('2027-12-31')).toMatch(/^31 Desember 2027$/);
    expect(formatDateId('salah')).toBe('');
  });
  it('daysUntil memakai hari kalender WIB', () => {
    expect(daysUntil('2027-01-12', Date.UTC(2027, 0, 10, 5, 0, 0))).toBe(2); // 10 Jan 12:00 WIB
    expect(daysUntil('2027-01-12', Date.UTC(2027, 0, 10, 20, 0, 0))).toBe(1); // 11 Jan 03:00 WIB
    expect(daysUntil('2027-01-12', Date.UTC(2027, 0, 12, 1, 0, 0))).toBe(0);
    expect(daysUntil('2027-01-12', Date.UTC(2027, 0, 14, 1, 0, 0))).toBe(-2);
    expect(daysUntil('bukan', 0)).toBeNull();
  });
  it('expiryNotice: tanggal & jumlah; null bila tak ada tanggal / saldo 0', () => {
    const now = Date.UTC(2026, 11, 20, 5, 0, 0);
    const n = expiryNotice({ inactive_since: '2026-01-12', expires_on: '2027-01-12', notice_sent: true }, 1200, now)!;
    expect(n.points).toBe(1200);
    expect(n.dateLabel).toBe('12 Januari 2027');
    expect(n.daysLeft).toBe(23);
    expect(n.urgent).toBe(false);
    expect(n.text).toMatch(/1\.200 poin/);
    expect(n.text).toMatch(/12 Januari 2027/);
    expect(n.text).toMatch(/23 hari lagi/);
    const soon = expiryNotice({ inactive_since: null, expires_on: '2026-12-22', notice_sent: false }, 50, now)!;
    expect(soon.urgent).toBe(true);
    expect(expiryNotice({ inactive_since: null, expires_on: '2026-12-21', notice_sent: false }, 50, now)!.text).toMatch(/besok/);
    expect(expiryNotice({ inactive_since: null, expires_on: '2026-12-20', notice_sent: false }, 50, now)!.text).toMatch(/hari ini/);
    expect(expiryNotice({ inactive_since: '2026-01-12', expires_on: null, notice_sent: false }, 1200, now)).toBeNull();
    expect(expiryNotice({ inactive_since: null, expires_on: '2027-01-12', notice_sent: false }, 0, now)).toBeNull();
    expect(expiryNotice(null, 1200, now)).toBeNull();
  });
});
