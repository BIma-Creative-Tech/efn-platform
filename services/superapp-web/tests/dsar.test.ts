import { describe, expect, it } from 'vitest';
import {
  daysUntil,
  dsarStatusLabel,
  dsarTypeLabel,
  exportFilename,
  findPendingDeletion,
  isCancellableDeletion,
  isDeleteConfirmed,
  isValidChallengeId,
  isValidOtpCode,
  parseDeleteAccepted,
  parseDsarRequests,
  parseExportAccepted,
  parseGraceDays,
  safeDownloadUrl,
} from '../lib/dsar';

const NOW = new Date('2026-10-06T00:00:00Z');

describe('safeDownloadUrl', () => {
  it('menerima https presigned', () => {
    const u = 'https://files.efn.example/market-scout/x.json?X-Amz-Signature=abc&X-Amz-Expires=86400';
    expect(safeDownloadUrl(u)).toBe(u);
  });
  it.each([
    'javascript:alert(1)',
    'data:application/json,{}',
    'http://files.efn.example/x.json',
    'https://user:pass@files.efn.example/x.json',
    '//files.efn.example/x.json',
    'bukan url',
    42,
    null,
  ])('menolak %j', (v) => {
    expect(safeDownloadUrl(v)).toBeNull();
  });
});

describe('parseDsarRequests', () => {
  it('normalisasi, buang item rusak, urut terbaru dulu, filter tautan', () => {
    const list = parseDsarRequests([
      { id: 'a', type: 'access', status: 'done', requested_at: '2026-10-01T00:00:00Z', download_url: 'javascript:x' },
      { id: 'b', type: 'delete', status: 'scheduled', requested_at: '2026-10-05T00:00:00Z', scheduled_at: '2026-10-12T00:00:00Z' },
      { type: 'access', status: 'done' }, // tanpa id
      'sampah',
    ]);
    expect(list.map((r) => r.id)).toEqual(['b', 'a']);
    expect(list[1]!.download_url).toBeNull();
  });
  it('menerima { items } dan request_id/created_at', () => {
    const [r] = parseDsarRequests({ items: [{ request_id: 'x', type: 'access', status: 'ready', created_at: '2026-10-06T00:00:00Z' }] });
    expect(r).toMatchObject({ id: 'x', requested_at: '2026-10-06T00:00:00Z' });
  });
  it('input sampah -> []', () => {
    expect(parseDsarRequests(null)).toEqual([]);
    expect(parseDsarRequests({ items: 3 })).toEqual([]);
  });
});

describe('penghapusan tertunda', () => {
  const base = {
    id: 'd',
    type: 'delete',
    status: 'scheduled',
    requested_at: null,
    completed_at: null,
    download_url: null,
    open_online_orders: 0,
    erasure_deadline: null,
  };
  it('dapat dibatalkan sebelum scheduled_at', () => {
    expect(isCancellableDeletion({ ...base, scheduled_at: '2026-10-10T00:00:00Z' }, NOW)).toBe(true);
  });
  it('tidak dapat dibatalkan setelah scheduled_at / status final / tipe lain', () => {
    expect(isCancellableDeletion({ ...base, scheduled_at: '2026-10-01T00:00:00Z' }, NOW)).toBe(false);
    expect(isCancellableDeletion({ ...base, status: 'processing', scheduled_at: null }, NOW)).toBe(false);
    expect(isCancellableDeletion({ ...base, status: 'cancelled', scheduled_at: null }, NOW)).toBe(false);
    expect(isCancellableDeletion({ ...base, type: 'access', scheduled_at: null }, NOW)).toBe(false);
  });
  it('findPendingDeletion', () => {
    expect(findPendingDeletion([{ ...base, scheduled_at: '2026-10-13T00:00:00Z' }], NOW)?.id).toBe('d');
    expect(findPendingDeletion([], NOW)).toBeNull();
  });
});

describe('parseDeleteAccepted / parseExportAccepted', () => {
  it('202 hapus akun', () => {
    expect(
      parseDeleteAccepted({ request_id: 'r1', scheduled_at: '2026-10-13T00:00:00Z', points_forfeited: 1200 }),
    ).toEqual({
      request_id: 'r1',
      scheduled_at: '2026-10-13T00:00:00Z',
      points_forfeited: 1200,
      open_online_orders: 0,
      erasure_deadline: null,
    });
  });
  it('poin tidak valid -> 0; tanpa request_id -> null', () => {
    expect(parseDeleteAccepted({ request_id: 'r1', points_forfeited: 'banyak' })?.points_forfeited).toBe(0);
    expect(parseDeleteAccepted({ request_id: 'r1', points_forfeited: -5 })?.points_forfeited).toBe(0);
    expect(parseDeleteAccepted({ scheduled_at: 'x' })).toBeNull();
  });
  it('202 export: tautan hanya https', () => {
    expect(parseExportAccepted({ request_id: 'e1', download_url: 'http://x/y' })).toEqual({
      request_id: 'e1',
      download_url: null,
      expires_at: null,
    });
    expect(parseExportAccepted({})).toBeNull();
  });
});

describe('validasi input', () => {
  it('OTP & challenge', () => {
    expect(isValidOtpCode('123456')).toBe(true);
    expect(isValidOtpCode('12345')).toBe(false);
    expect(isValidOtpCode('12345a')).toBe(false);
    expect(isValidChallengeId('3f2b8c1e-0000-4000-8000-000000000000')).toBe(true);
    expect(isValidChallengeId('../../etc')).toBe(false);
  });
  it('konfirmasi hapus wajib kata HAPUS + pengakuan poin', () => {
    expect(isDeleteConfirmed({ confirm: 'HAPUS', acknowledge_points: true })).toBe(true);
    expect(isDeleteConfirmed({ confirm: 'hapus', acknowledge_points: true })).toBe(false);
    expect(isDeleteConfirmed({ confirm: 'HAPUS' })).toBe(false);
    expect(isDeleteConfirmed(null)).toBe(false);
  });
});

describe('format & util', () => {
  it('label status/jenis; nilai asing apa adanya', () => {
    expect(dsarStatusLabel('scheduled')).toMatch(/Dijadwalkan/);
    expect(dsarStatusLabel('aneh')).toBe('aneh');
    expect(dsarStatusLabel('__proto__')).toBe('__proto__');
    expect(dsarTypeLabel('delete')).toBe('Hapus akun');
  });
  it('daysUntil', () => {
    expect(daysUntil('2026-10-13T00:00:00Z', NOW)).toBe(7);
    expect(daysUntil('2026-10-06T01:00:00Z', NOW)).toBe(1);
    expect(daysUntil('2026-10-01T00:00:00Z', NOW)).toBe(0);
    expect(daysUntil(null, NOW)).toBeNull();
    expect(daysUntil('bukan tanggal', NOW)).toBeNull();
  });
  it('exportFilename memakai tanggal Jakarta', () => {
    // 2026-10-05T20:00Z = 2026-10-06 03:00 WIB
    expect(exportFilename(new Date('2026-10-05T20:00:00Z'))).toBe('data-saya-efn-20261006.json');
  });
  it('parseGraceDays', () => {
    expect(parseGraceDays(undefined)).toBe(7);
    expect(parseGraceDays('')).toBe(7);
    expect(parseGraceDays('14')).toBe(14);
    expect(parseGraceDays('0')).toBe(0);
    expect(parseGraceDays('abc')).toBe(7);
    expect(parseGraceDays('999')).toBe(7);
  });
});
