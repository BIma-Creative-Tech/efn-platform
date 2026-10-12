import { describe, expect, it } from 'vitest';
import {
  DSAR_RECEIPT_MAX_AGE_S,
  canCancelDeletion,
  deletionStatusLabel,
  isDsarRequestId,
  isPlausibleReceipt,
  parseDeletionStatus,
  receiptMaxAge,
} from '@/lib/dsar-receipt';

const ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';
const MAC = 'A'.repeat(43);
const NOW_MS = 1_790_000_000_000; // detik = 1 790 000 000

describe('isPlausibleReceipt', () => {
  it('menerima format v1.<exp>.<b64url>', () => {
    expect(isPlausibleReceipt(`v1.1792592000.${MAC}`)).toBe(true);
    expect(isPlausibleReceipt(`v1.1792592000.abc-_DEF0123456789`)).toBe(true);
  });
  it('menolak bentuk lain', () => {
    for (const v of [undefined, null, 1, '', 'v2.1.' + MAC, 'v1.x.' + MAC, 'v1.1.short', `v1.1.${MAC}=`, `v1.1.${'a'.repeat(200)}`, `v1.1.${MAC}\n`]) {
      expect(isPlausibleReceipt(v)).toBe(false);
    }
  });
});

describe('receiptMaxAge', () => {
  it('dibatasi 30 hari', () => {
    const exp = NOW_MS / 1000 + 90 * 86_400;
    expect(receiptMaxAge(`v1.${exp}.${MAC}`, NOW_MS)).toBe(DSAR_RECEIPT_MAX_AGE_S);
  });
  it('mengikuti sisa umur token', () => {
    const exp = NOW_MS / 1000 + 3600;
    expect(receiptMaxAge(`v1.${exp}.${MAC}`, NOW_MS)).toBe(3600);
  });
  it('null bila kedaluwarsa', () => {
    expect(receiptMaxAge(`v1.${NOW_MS / 1000}.${MAC}`, NOW_MS)).toBeNull();
    expect(receiptMaxAge(`v1.1.${MAC}`, NOW_MS)).toBeNull();
  });
  it('exp tidak terbaca -> 30 hari', () => {
    expect(receiptMaxAge('opaque', NOW_MS)).toBe(DSAR_RECEIPT_MAX_AGE_S);
  });
});

describe('isDsarRequestId', () => {
  it('hanya UUID', () => {
    expect(isDsarRequestId(ID)).toBe(true);
    expect(isDsarRequestId(ID.toUpperCase())).toBe(true);
    expect(isDsarRequestId('../me')).toBe(false);
    expect(isDsarRequestId(`${ID}x`)).toBe(false);
  });
});

describe('parseDeletionStatus', () => {
  const base = {
    id: ID,
    type: 'delete',
    status: 'scheduled',
    requested_at: '2026-10-06T03:00:00.000Z',
    scheduled_at: '2026-10-13T03:00:00.000Z',
    completed_at: null,
  };
  it('mengambil field allowlist saja', () => {
    const v = parseDeletionStatus({ ...base, member_hash: 'x', receipt_token: 'y' }, ID);
    expect(v).toEqual({
      id: ID,
      status: 'scheduled',
      requested_at: base.requested_at,
      scheduled_at: base.scheduled_at,
      completed_at: null,
      open_online_orders: 0,
    });
    expect(Object.keys(v!)).not.toContain('member_hash');
    // BFF lama tanpa field L-22: erasure_deadline tidak ada (undefined), bukan null.
    expect(Object.keys(v!)).not.toContain('erasure_deadline');
  });
  it('id harus sama (tanpa peduli huruf besar) dan type delete', () => {
    expect(parseDeletionStatus(base, ID.toUpperCase())).not.toBeNull();
    expect(parseDeletionStatus({ ...base, id: '00000000-0000-4000-8000-000000000000' }, ID)).toBeNull();
    expect(parseDeletionStatus({ ...base, type: 'access' }, ID)).toBeNull();
    expect(parseDeletionStatus(null, ID)).toBeNull();
    expect(parseDeletionStatus({ ...base, status: 5 }, ID)).toBeNull();
  });
  it('status tak dikenal -> unknown; tanggal rusak -> null', () => {
    const v = parseDeletionStatus({ ...base, status: 'weird', scheduled_at: 'bukan tanggal' }, ID)!;
    expect(v.status).toBe('unknown');
    expect(v.scheduled_at).toBeNull();
    expect(deletionStatusLabel('unknown')).toBe('Sedang diperbarui');
  });
  it('label empat status kontrak', () => {
    expect(deletionStatusLabel('scheduled')).toMatch(/Dijadwalkan/);
    expect(deletionStatusLabel('processing')).toMatch(/dihapus/);
    expect(deletionStatusLabel('completed')).toMatch(/Selesai/);
    expect(deletionStatusLabel('cancelled')).toBe('Dibatalkan');
  });
});

describe('canCancelDeletion', () => {
  it('hanya scheduled dan sesi valid', () => {
    expect(canCancelDeletion({ status: 'scheduled' }, true)).toBe(true);
    expect(canCancelDeletion({ status: 'scheduled' }, false)).toBe(false);
    for (const s of ['processing', 'completed', 'cancelled', 'unknown'] as const) {
      expect(canCancelDeletion({ status: s }, true)).toBe(false);
    }
  });
});
