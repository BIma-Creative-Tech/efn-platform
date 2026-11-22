import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { escapeCsvCell, toCsv } from '@/lib/csv';
import { isOriginAllowed } from '@/lib/origin-guard';
import { scopeSettings } from '@/lib/scope';

const sha = (s: string) => createHash('sha256').update(s, 'utf8').digest('hex');

// Vektor sig3 kanonik G52 (§28.15.6): lihat juga tests/scope-sig3.test.ts.
describe('scopeSettings (sig3, docs/04 §28.15.6)', () => {
  it('kunci "ab"*16, cids [3,1,3], codes [PB,BCR], tanpa cabang', () => {
    const key = 'ab'.repeat(16);
    const s = scopeSettings([3, 1, 3], ['PB', 'BCR'], [], key);
    expect(s.SQL_efn_cids).toBe('1,3');
    expect(s.SQL_efn_codes).toBe('BCR,PB');
    expect(s.SQL_efn_brs).toBe('');
    expect(s.SQL_efn_sig3).toBe(sha(`${key}|1,3|BCR,PB|`));
    expect(s.SQL_efn_sig3).toMatch(/^[0-9a-f]{64}$/);
  });

  it('kunci = byte ASCII string hex (bukan hex-decode)', () => {
    const key = 'ab'.repeat(32);
    const asHexBytes = createHash('sha256').update(Buffer.concat([Buffer.from(key, 'hex'), Buffer.from('|1|PB|')])).digest('hex');
    const s = scopeSettings([1], ['PB'], [], key);
    expect(s.SQL_efn_sig3).toBe(sha(`${key}|1|PB|`));
    expect(s.SQL_efn_sig3).not.toBe(asHexBytes);
  });

  it('all_brands tanpa company: cids kosong, tetap ditandatangani', () => {
    const key = 'cd'.repeat(32);
    const s = scopeSettings([], ['BCF', 'BCR', 'CUP', 'IMD', 'PB', 'WTZ'], [], key);
    expect(s.SQL_efn_cids).toBe('');
    expect(s.SQL_efn_sig3).toBe(sha(`${key}||BCF,BCR,CUP,IMD,PB,WTZ|`));
  });

  it('urutan numerik untuk cids (10 > 9)', () => {
    expect(scopeSettings([10, 9, 2], ['PB'], [], 'a'.repeat(64)).SQL_efn_cids).toBe('2,9,10');
  });

  it('sig bergantung pada kunci dan cakupan', () => {
    const a = scopeSettings([1], ['PB'], [], 'a'.repeat(64)).SQL_efn_sig3;
    expect(a).not.toBe(scopeSettings([1, 2], ['PB'], [], 'a'.repeat(64)).SQL_efn_sig3);
    expect(a).not.toBe(scopeSettings([1], ['PB'], [], 'b'.repeat(64)).SQL_efn_sig3);
  });

  it('kode/id tidak valid ditolak', () => {
    expect(() => scopeSettings([1], ['XX'], [], 'a'.repeat(64))).toThrow();
    expect(() => scopeSettings([1], ["PB'; DROP"], [], 'a'.repeat(64))).toThrow();
    expect(() => scopeSettings([0], ['PB'], [], 'a'.repeat(64))).toThrow();
    expect(() => scopeSettings([1.5], ['PB'], [], 'a'.repeat(64))).toThrow();
  });
});

describe('CSV (C3)', () => {
  it.each(['=1+1', '+62', '-5', '@SUM(A1)', '\tx', '\rx'])('awalan formula %j diberi \'', (v) => {
    const out = escapeCsvCell(v);
    expect(out.replace(/^"/, '')[0]).toBe("'");
  });

  it('sel aman tidak diubah; angka mentah; null kosong', () => {
    expect(escapeCsvCell('Roti Tawar')).toBe('Roti Tawar');
    expect(escapeCsvCell(-5)).toBe('-5');
    expect(escapeCsvCell(1234.5)).toBe('1234.5');
    expect(escapeCsvCell(null)).toBe('');
    expect(escapeCsvCell(Number.NaN)).toBe('');
  });

  it('kutip, koma, dan baris baru di-quote', () => {
    expect(escapeCsvCell('a,b')).toBe('"a,b"');
    expect(escapeCsvCell('say "hi"')).toBe('"say ""hi"""');
    expect(escapeCsvCell('a\nb')).toBe('"a\nb"');
  });

  it('toCsv: BOM, header, CRLF, escape per sel', () => {
    const csv = toCsv(
      [{ key: 'n', header: 'Nama' }, { key: 'v', header: 'Nilai' }],
      [{ n: '=cmd|x', v: 10 }, { n: 'Kopi, susu', v: null }],
    );
    expect(csv.startsWith('﻿Nama,Nilai\r\n')).toBe(true);
    expect(csv).toContain("'=cmd|x,10\r\n");
    expect(csv).toContain('"Kopi, susu",\r\n');
  });
});

describe('origin guard (C1)', () => {
  const odoo = 'https://erp.test:9443';
  it('cocok persis diterima; lain ditolak', () => {
    expect(isOriginAllowed(odoo, odoo)).toBe(true);
    expect(isOriginAllowed('https://erp.test', odoo)).toBe(false);
    expect(isOriginAllowed('null', odoo)).toBe(false);
    expect(isOriginAllowed('https://erp.test:9443.evil.test', odoo)).toBe(false);
  });
  it('tanpa Origin: selalu ditolak; tanpa origin Odoo = ditolak', () => {
    expect(isOriginAllowed(null, odoo)).toBe(false);
    expect(isOriginAllowed(undefined, odoo)).toBe(false);
    expect(isOriginAllowed('', odoo)).toBe(false);
    expect(isOriginAllowed(odoo, null)).toBe(false);
  });
});
