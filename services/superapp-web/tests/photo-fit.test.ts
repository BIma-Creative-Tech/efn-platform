/**
 * G25: bingkai foto mengikuti rasio foto katalog per PT, jadi foto tampil utuh (tidak terpotong).
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { photoVars } from '../lib/catalog';

describe('photoVars (rasio foto katalog per PT)', () => {
  it('PB lanskap 497:347, Bacha potret 4:5, GL & Wetzel\'s persegi; kode tak dikenal -> persegi', () => {
    expect(photoVars('PB')).toEqual({ '--photo-ratio': '497 / 347', '--photo-hw': '0.698' });
    expect(photoVars('BCR')).toEqual({ '--photo-ratio': '4 / 5', '--photo-hw': '1.250' });
    expect(photoVars('BCF')['--photo-ratio']).toBe('4 / 5');
    expect(photoVars('GL')).toEqual({ '--photo-ratio': '1 / 1', '--photo-hw': '1.000' });
    expect(photoVars('WTZ')['--photo-ratio']).toBe('1 / 1');
    expect(photoVars('XX')['--photo-ratio']).toBe('1 / 1');
    expect(photoVars(null)['--photo-ratio']).toBe('1 / 1');
    expect(photoVars('__proto__')['--photo-ratio']).toBe('1 / 1');
  });

  it('CSS foto produk tidak memotong: tidak ada object-fit: cover pada gambar katalog', () => {
    for (const f of ['components/landing/landing.module.css', 'components/landing/superapp.module.css', 'components/online/online.module.css']) {
      const css = readFileSync(join(__dirname, '..', f), 'utf8');
      expect(css, f).not.toMatch(/object-fit:\s*cover/);
    }
  });
});
