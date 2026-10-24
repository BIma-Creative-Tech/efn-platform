/**
 * Proxy multi-tenant (proxy.ts): host -> brand; override dev `?brand=`/cookie `efn_dev_brand` hanya menempel di
 * localhost/IP. Regresi: cookie `parisbaguette` tertinggal membuat app.efn.local tampil sebagai situs PB.
 */
import { NextRequest } from 'next/server';
import { describe, expect, it } from 'vitest';
import { isDevOverrideHost } from '../lib/brands';
import { proxy } from '../proxy';

function run(url: string, cookie?: string) {
  const req = new NextRequest(url, { headers: { host: new URL(url).host, ...(cookie ? { cookie } : {}) } });
  const res = proxy(req);
  return { brand: res.headers.get('x-middleware-request-x-efn-brand'), setCookie: res.headers.get('set-cookie') ?? '' };
}

describe('proxy brand override (dev)', () => {
  it('isDevOverrideHost: localhost/IP/tanpa titik ya; domain bernama tidak', () => {
    for (const h of ['localhost:3001', '127.0.0.1:3001', '[::1]:3001', 'superapp-web:3000', '']) expect(isDevOverrideHost(h), h).toBe(true);
    for (const h of ['app.efn.local', 'bacha.efn.local', 'grandlucky.contoh.co.id']) expect(isDevOverrideHost(h), h).toBe(false);
  });

  it('app.efn.local + cookie PB tertinggal -> tetap efn (Super-App) dan cookie dihapus', () => {
    const r = run('https://app.efn.local/', 'efn_dev_brand=parisbaguette');
    expect(r.brand).toBe('efn');
    expect(r.setCookie).toMatch(/efn_dev_brand=;/);
  });

  it('domain bernama: ?brand= hanya satu permintaan, tidak menyimpan cookie', () => {
    const r = run('https://app.efn.local/?brand=wetzels');
    expect(r.brand).toBe('wetzels');
    expect(r.setCookie).toBe('');
  });

  it('domain brand menampilkan brand-nya sendiri walau ada cookie', () => {
    expect(run('https://bacha.efn.local/', 'efn_dev_brand=parisbaguette').brand).toBe('bacha');
  });

  it('localhost: ?brand= menyimpan cookie dan cookie dipakai di permintaan berikutnya', () => {
    const a = run('http://localhost:3001/?brand=grandlucky');
    expect(a.brand).toBe('grandlucky');
    expect(a.setCookie).toMatch(/efn_dev_brand=grandlucky/);
    expect(run('http://localhost:3001/belanja', 'efn_dev_brand=grandlucky').brand).toBe('grandlucky');
    expect(run('http://localhost:3001/', 'efn_dev_brand=bukan-brand').brand).toBe('efn');
  });
});
