import { describe, expect, it } from 'vitest';
import { maskPhone, normalizeIndonesianPhone } from '../lib/phone';

describe('normalizeIndonesianPhone', () => {
  it.each([
    ['081234567890', '6281234567890'],
    ['+62 812-3456-7890', '6281234567890'],
    ['(0812) 3456.7890', '6281234567890'],
    ['81234567890', '6281234567890'],
    ['6281234567890', '6281234567890'],
    ['0812345678', '62812345678'], // batas bawah: 8 digit setelah 628
    ['0812345678901', '62812345678901'], // batas atas: 11 digit setelah 628
  ])('%j -> %j', (input, expected) => {
    expect(normalizeIndonesianPhone(input)).toBe(expected);
  });

  it.each([
    '',
    'abc',
    '0812',
    '081234567',
    '08123456789012',
    '0212345678', // telepon rumah, bukan seluler
    '+6581234567', // nomor Singapura
    '0812345678a',
    '++6281234567890',
  ])('menolak %j', (input) => {
    expect(normalizeIndonesianPhone(input)).toBeNull();
  });

  it('menolak non-string', () => {
    expect(normalizeIndonesianPhone(undefined as unknown as string)).toBeNull();
  });
});

describe('maskPhone', () => {
  it('menyamarkan bagian tengah', () => {
    expect(maskPhone('6281234567890')).toBe('62812****890');
  });
  it('string pendek -> mask penuh', () => {
    expect(maskPhone('123')).toBe('****');
  });
});
