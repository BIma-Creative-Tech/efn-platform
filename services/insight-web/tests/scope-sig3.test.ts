/**
 * Setting cakupan view ESB G52 (docs/04 §28.15.6): SQL_efn_brs dan SQL_efn_sig3 = SHA256(KEY|cids|codes|brs).
 * Vektor scope kanonik kontrak: `KEY + "|5|PB|pb:12,pb:7"` (D) dan `KEY + "|3,5,11|BCF,PB|"` (E), KEY = "ab" * 32.
 * Python (Odoo/ClickHouse harness) wajib menghasilkan nilai identik; hash dihitung independen dengan Node crypto di sini.
 * Bila `services/odoo/addons/efn_insight/tests/vectors_g52.json` muncul, tambahkan pembandingan nilai hex di test ini.
 */
import { describe, expect, it } from 'vitest';
import { buildChSearchParams } from '@/lib/ch-params';
import { scopeSettings } from '@/lib/scope';
import { KEY, sha256Of } from './helpers-v3';

describe('scopeSettings sig3 (vektor kanonik)', () => {
  it('D: store F&B PB, cabang pb:12 dan pb:7', () => {
    const s = scopeSettings([5], ['PB'], ['pb:12', 'pb:7'], KEY);
    expect(s).toEqual({
      SQL_efn_cids: '5',
      SQL_efn_codes: 'PB',
      SQL_efn_brs: 'pb:12,pb:7',
      SQL_efn_sig3: sha256Of(`${KEY}|5|PB|pb:12,pb:7`),
    });
    expect(s.SQL_efn_sig3).toMatch(/^[0-9a-f]{64}$/);
  });

  it('E: exec F&B + SMD; codes hanya ESB (SMD dibuang pemanggil), brs kosong -> string kosong di ujung', () => {
    const s = scopeSettings([3, 5, 11], ['BCF', 'PB'], [], KEY);
    expect(s.SQL_efn_cids).toBe('3,5,11');
    expect(s.SQL_efn_codes).toBe('BCF,PB');
    expect(s.SQL_efn_brs).toBe('');
    expect(s.SQL_efn_sig3).toBe(sha256Of(`${KEY}|3,5,11|BCF,PB|`));
  });

  it('pemisahan domain: sig3 (3 pemisah) tidak sama dengan sig v1 (2 pemisah) walau brs kosong', () => {
    const v1 = sha256Of(`${KEY}|3,5,11|BCF,PB`);
    expect(scopeSettings([3, 5, 11], ['BCF', 'PB'], [], KEY).SQL_efn_sig3).not.toBe(v1);
  });

  it('kunci = byte ASCII string hex (bukan hex-decode)', () => {
    const asBytes = Buffer.from(KEY, 'hex');
    expect(asBytes.length).toBe(32);
    expect(scopeSettings([5], ['PB'], ['pb:7'], KEY).SQL_efn_sig3).toBe(sha256Of(`${KEY}|5|PB|pb:7`));
  });

  it('sig berbeda untuk brs lain (E3), kunci lain, atau PT lain', () => {
    const a = scopeSettings([5], ['PB'], ['pb:7'], KEY).SQL_efn_sig3;
    expect(a).not.toBe(scopeSettings([5], ['PB'], ['pb:12'], KEY).SQL_efn_sig3);
    expect(a).not.toBe(scopeSettings([5], ['PB'], [], KEY).SQL_efn_sig3);
    expect(a).not.toBe(scopeSettings([5], ['PB'], ['pb:7'], 'cd'.repeat(32)).SQL_efn_sig3);
    expect(a).not.toBe(scopeSettings([5], ['WTZ'], ['pb:7'], KEY).SQL_efn_sig3);
  });

  it('cakupan tidak valid ditolak (fail closed): brs format/urutan/duplikat/terlalu banyak, kode, company id', () => {
    for (const brs of [['pb:07'], ['PB:7'], ['pb:7', 'pb:12'], ['pb:7', 'pb:7'], ['xx:1'], ['pb:7,pb:8'], ['pb:7;DROP'], ['pb:4294967296']]) {
      expect(() => scopeSettings([5], ['PB'], brs, KEY), brs.join('|')).toThrow();
    }
    const many = Array.from({ length: 51 }, (_, i) => `pb:${i + 100}`).sort();
    expect(() => scopeSettings([5], ['PB'], many, KEY)).toThrow();
    expect(() => scopeSettings([5], ['SMD'], [], KEY)).toThrow();
    expect(() => scopeSettings([0], ['PB'], [], KEY)).toThrow();
  });

  it('tidak ada karakter "|" atau non-ASCII di nilai setting (SEC-190)', () => {
    const s = scopeSettings([5], ['PB'], ['pb:12', 'pb:7'], KEY);
    for (const v of [s.SQL_efn_cids, s.SQL_efn_codes, s.SQL_efn_brs]) {
      expect(v).not.toContain('|');
      expect(/^[\x20-\x7e]*$/.test(v)).toBe(true);
    }
  });
});

describe('buildChSearchParams membawa SQL_efn_brs + SQL_efn_sig3 (tanpa SQL_efn_sig)', () => {
  it('sesi ber-cabang', () => {
    const scope = scopeSettings([5], ['PB'], ['pb:12', 'pb:7'], KEY);
    const sp = buildChSearchParams('fstore_branch_list', scope, { pt: ['PB'] });
    expect(sp.get('SQL_efn_cids')).toBe('5');
    expect(sp.get('SQL_efn_codes')).toBe('PB');
    expect(sp.get('SQL_efn_brs')).toBe('pb:12,pb:7');
    expect(sp.get('SQL_efn_sig3')).toBe(scope.SQL_efn_sig3);
    expect(sp.has('SQL_efn_sig')).toBe(false);
  });
  it('SQL_efn_brs selalu dikirim walau kosong (policy gagal tanpa setting, E6)', () => {
    const sp = buildChSearchParams('fstore_branch_list', scopeSettings([5], ['PB'], [], KEY), { pt: ['PB'] });
    expect(sp.has('SQL_efn_brs')).toBe(true);
    expect(sp.get('SQL_efn_brs')).toBe('');
  });
});
