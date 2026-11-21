import { describe, expect, it } from 'vitest';
import { GLOSSARY, getTerm, termsIn, termSummary } from '@/lib/glossary';

describe('glossary', () => {
  it('semua istilah wajib punya definisi dan id unik', () => {
    expect(new Set(GLOSSARY.map((e) => e.id)).size).toBe(GLOSSARY.length);
    for (const e of GLOSSARY) {
      expect(e.def.length).toBeGreaterThan(20);
      expect(() => new RegExp(e.match)).not.toThrow();
    }
  });
  it('istilah yang diminta pemilik proyek tersedia', () => {
    for (const id of ['wape', 'p10-p90', 'bias', 'mdr', 'hpp', 'sell-through', 'oos', 'sku', 'dc', 'fefo', 'yoy', 'mtd', 'fin', 'wip', 'ck']) {
      expect(getTerm(id), id).toBeDefined();
    }
  });
  it('WAPE dan p10-p90 dijelaskan dengan cara membaca', () => {
    expect(getTerm('wape')?.read).toMatch(/20%/);
    expect(getTerm('p10-p90')?.read).toMatch(/8 dari 10/);
  });
  it('termsIn mendeteksi istilah di label', () => {
    expect(termsIn('Galat forecast (WAPE), 8 minggu')).toEqual(['wape']);
    expect(termsIn('Batas bawah (p10)')).toEqual(['p10-p90']);
    expect(termsIn('Produksi CK')).toEqual(['ck']);
    expect(termsIn('Sell-through per cabang')).toEqual(['sell-through']);
    expect(termsIn('Fill rate qty')).toEqual(['fill-rate']);
    expect(termsIn('Near-expiry (nilai HPP)')).toEqual(['hpp']);
  });
  it('singkatan peka huruf besar-kecil: tidak cocok di tengah kata', () => {
    expect(termsIn('Final produk')).toEqual([]);
    expect(termsIn('Dikirim')).toEqual([]);
    expect(termsIn('Hanya produk FIN (satuan EA)')).toEqual(['fin']);
    expect(termsIn('')).toEqual([]);
  });
  it('termSummary memuat nama dan definisi', () => {
    const s = termSummary(getTerm('mdr')!);
    expect(s).toContain('MDR');
    expect(s).toContain('Merchant Discount Rate');
  });
});
