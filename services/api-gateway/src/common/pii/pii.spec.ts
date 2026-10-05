import { createHash } from 'node:crypto';
import { isMemberHash, isMemberRef, maskMsisdn, memberHash, normalizeMsisdn } from './pii';

describe('pii', () => {
  it('mask sesuai kontrak: 62812****890', () => {
    expect(maskMsisdn('6281234567890')).toBe('62812****890');
    expect(maskMsisdn('123')).toBe('****');
  });

  it.each([
    ['6281234567890', '6281234567890'],
    ['+62 812-3456-7890', '6281234567890'],
    ['081234567890', '6281234567890'],
    ['0062 81234567890', '6281234567890'],
  ])('normalisasi %s → %s', (input, out) => {
    expect(normalizeMsisdn(input)).toBe(out);
  });

  it.each([['abc'], ['12'], [''], [12345678901], [null], ['+62812345678901234567']])('menolak %j', (input) => {
    expect(normalizeMsisdn(input)).toBeNull();
  });

  it.each([
    ['+1 415 555 0100'],
    ['+44 7700 900123'],
    ['0065 81234567'],
    ['6212345'], // terlalu pendek (< 62 + 8 digit)
    ['62123456789012345'], // terlalu panjang (> 62 + 13 digit)
  ])('SEC-06: nomor non-Indonesia / di luar ^62[0-9]{8,13}$ ditolak: %s', (input) => {
    expect(normalizeMsisdn(input)).toBeNull();
  });

  it('isMemberRef hanya menerima ^62[0-9]{8,13}$', () => {
    expect(isMemberRef('6281234567890')).toBe(true);
    expect(isMemberRef('+6281234567890')).toBe(false);
    expect(isMemberRef('14155550100')).toBe(false);
  });

  it('isMemberHash: 64 hex lowercase', () => {
    expect(isMemberHash('a'.repeat(64))).toBe(true);
    expect(isMemberHash('A'.repeat(64))).toBe(false);
    expect(isMemberHash('a'.repeat(63))).toBe(false);
  });

  it('memberHash = sha256(pepper + member_ref) hex (kontrak §1)', () => {
    const pepper = 'p'.repeat(64);
    expect(memberHash(pepper, '6281234567890')).toBe(
      createHash('sha256').update(`${pepper}6281234567890`).digest('hex'),
    );
  });
});
