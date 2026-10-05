import { detectImageKind } from './magic-bytes';
import { dHashFromGrey9x8 } from './dhash';

const bytes = (...b: number[]): Buffer => Buffer.from(b);

describe('detectImageKind', () => {
  it('JPEG', () => {
    expect(detectImageKind(bytes(0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10))).toBe('jpeg');
  });

  it('PNG', () => {
    expect(detectImageKind(bytes(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00))).toBe('png');
  });

  it('WebP (RIFF....WEBP)', () => {
    const buf = Buffer.concat([Buffer.from('RIFF'), bytes(0x24, 0x00, 0x00, 0x00), Buffer.from('WEBPVP8 ')]);
    expect(detectImageKind(buf)).toBe('webp');
  });

  it.each([
    ['GIF', Buffer.from('GIF89a......')],
    ['PDF', Buffer.from('%PDF-1.7')],
    ['SVG/HTML', Buffer.from('<svg xmlns="http://www.w3.org/2000/svg">')],
    ['ZIP', bytes(0x50, 0x4b, 0x03, 0x04)],
    ['RIFF bukan WebP (WAV)', Buffer.concat([Buffer.from('RIFF'), bytes(0, 0, 0, 0), Buffer.from('WAVE')])],
    ['kosong', Buffer.alloc(0)],
    ['JPEG terpotong', bytes(0xff, 0xd8)],
    ['PNG terpotong', bytes(0x89, 0x50, 0x4e, 0x47)],
  ])('menolak %s', (_name, buf) => {
    expect(detectImageKind(buf)).toBeNull();
  });
});

describe('dHashFromGrey9x8', () => {
  it('gradien menurun → semua bit 1; menaik → semua bit 0', () => {
    const desc = Buffer.alloc(72);
    const asc = Buffer.alloc(72);
    for (let y = 0; y < 8; y += 1) {
      for (let x = 0; x < 9; x += 1) {
        desc[y * 9 + x] = 255 - x * 20;
        asc[y * 9 + x] = x * 20;
      }
    }
    expect(dHashFromGrey9x8(desc)).toBe('ffffffffffffffff');
    expect(dHashFromGrey9x8(asc)).toBe('0000000000000000');
  });

  it('input kurang dari 72 piksel ditolak', () => {
    expect(() => dHashFromGrey9x8(Buffer.alloc(10))).toThrow();
  });
});
