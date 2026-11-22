import { describe, expect, it } from 'vitest';
import { isInternalNavClick } from '@/lib/loading-store';

const CUR = 'https://insight.efn.local/sales?from=2026-11-01&to=2026-11-20';
const ev = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false };
const a = (href: string, extra: Partial<{ target: string; hasDownload: boolean }> = {}) => ({
  href,
  target: '',
  hasDownload: false,
  ...extra,
});

describe('isInternalNavClick', () => {
  it('menu ke halaman lain = navigasi (overlay aktif)', () => {
    expect(isInternalNavClick(a('/flow?from=2026-11-01&to=2026-11-20'), ev, CUR)).toBe(true);
  });
  it('URL sama atau hanya hash = bukan navigasi', () => {
    expect(isInternalNavClick(a(CUR), ev, CUR)).toBe(false);
    expect(isInternalNavClick(a(`${CUR}#konten`), ev, CUR)).toBe(false);
  });
  it('situs lain (Odoo), tab baru, unduhan, ekspor CSV, logout = bukan navigasi klien', () => {
    expect(isInternalNavClick(a('https://odoo.efn.local/odoo'), ev, CUR)).toBe(false);
    expect(isInternalNavClick(a('/flow', { target: '_blank' }), ev, CUR)).toBe(false);
    expect(isInternalNavClick(a('/flow', { hasDownload: true }), ev, CUR)).toBe(false);
    expect(isInternalNavClick(a('/api/export/sales_top_menu?from=x'), ev, CUR)).toBe(false);
    expect(isInternalNavClick(a('/logout'), ev, CUR)).toBe(false);
  });
  it('klik dengan tombol pengubah / tombol tengah / sudah dicegah = bukan navigasi', () => {
    expect(isInternalNavClick(a('/flow'), { ...ev, ctrlKey: true }, CUR)).toBe(false);
    expect(isInternalNavClick(a('/flow'), { ...ev, metaKey: true }, CUR)).toBe(false);
    expect(isInternalNavClick(a('/flow'), { ...ev, button: 1 }, CUR)).toBe(false);
    expect(isInternalNavClick(a('/flow'), { ...ev, defaultPrevented: true }, CUR)).toBe(false);
  });
});
