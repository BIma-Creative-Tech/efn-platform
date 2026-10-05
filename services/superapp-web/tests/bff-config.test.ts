import { describe, expect, it } from 'vitest';
import { assertBffTransport, BffConfigError, DEFAULT_BFF_URL, type ReadFile } from '../lib/bff-config';

const PEM = '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n';
const readOk: ReadFile = () => PEM;
const readMissing: ReadFile = () => null;

describe('assertBffTransport — URL & guard plaintext', () => {
  it('default https://api-gateway:3000', () => {
    const t = assertBffTransport({});
    expect(t.baseUrl).toBe(DEFAULT_BFF_URL);
    expect(t.plaintext).toBe(false);
  });

  it('membuang slash di akhir', () => {
    expect(assertBffTransport({ BFF_INTERNAL_URL: 'https://api-gateway:3000/' }).baseUrl).toBe('https://api-gateway:3000');
    expect(assertBffTransport({ BFF_INTERNAL_URL: 'https://api-gateway:3000/bff//' }).baseUrl).toBe('https://api-gateway:3000/bff');
  });

  it('BFF_INTERNAL_URL diprioritaskan atas API_INTERNAL_URL', () => {
    const t = assertBffTransport({ BFF_INTERNAL_URL: 'https://a:1', API_INTERNAL_URL: 'http://b:2' });
    expect(t.baseUrl).toBe('https://a:1');
  });

  it.each([
    [{ BFF_INTERNAL_URL: 'http://api-gateway:3000' }],
    [{ API_INTERNAL_URL: 'http://api-gateway:3000' }],
    [{ BFF_INTERNAL_URL: 'http://api-gateway:3000', EFN_ALLOW_PLAINTEXT: 'TRUE', NODE_ENV: 'development' }],
    [{ BFF_INTERNAL_URL: 'http://api-gateway:3000', EFN_ALLOW_PLAINTEXT: '1', NODE_ENV: 'development' }],
    [{ BFF_INTERNAL_URL: 'http://api-gateway:3000', EFN_ALLOW_PLAINTEXT: 'true', NODE_ENV: 'production' }],
    [{ BFF_INTERNAL_URL: 'http://api-gateway:3000', EFN_ALLOW_PLAINTEXT: 'true', APP_ENV: 'production' }],
    [{ BFF_INTERNAL_URL: 'ftp://api-gateway:3000' }],
    [{ BFF_INTERNAL_URL: 'ws://api-gateway:3000' }],
    [{ BFF_INTERNAL_URL: 'api-gateway:3000' }],
    [{ BFF_INTERNAL_URL: 'bukan url' }],
    [{ BFF_INTERNAL_URL: 'https://user:pass@api-gateway:3000' }],
    [{ BFF_INTERNAL_URL: 'https://api-gateway:3000/?x=1' }],
  ])('menolak %j', (env) => {
    expect(() => assertBffTransport(env)).toThrow(BffConfigError);
  });

  it('http diizinkan hanya dengan EFN_ALLOW_PLAINTEXT=true di non-production, dengan peringatan', () => {
    const t = assertBffTransport({
      BFF_INTERNAL_URL: 'http://api-gateway:3000',
      EFN_ALLOW_PLAINTEXT: 'true',
      NODE_ENV: 'development',
    });
    expect(t.plaintext).toBe(true);
    expect(t.warnings.join(' ')).toMatch(/PLAINTEXT/);
  });

  it('NODE_TLS_REJECT_UNAUTHORIZED=0 ditolak di production, diperingatkan di dev', () => {
    expect(() => assertBffTransport({ NODE_ENV: 'production', NODE_TLS_REJECT_UNAUTHORIZED: '0' })).toThrow(BffConfigError);
    const t = assertBffTransport({ NODE_ENV: 'development', NODE_TLS_REJECT_UNAUTHORIZED: '0' });
    expect(t.warnings.length).toBeGreaterThan(0);
  });
});

describe('assertBffTransport — CA internal (NODE_EXTRA_CA_CERTS)', () => {
  it('production tanpa NODE_EXTRA_CA_CERTS -> gagal', () => {
    expect(() => assertBffTransport({ NODE_ENV: 'production' }, readOk)).toThrow(/NODE_EXTRA_CA_CERTS/);
  });

  it('file CA tidak terbaca -> gagal (Node sendiri hanya memberi warning)', () => {
    expect(() =>
      assertBffTransport({ NODE_ENV: 'production', NODE_EXTRA_CA_CERTS: '/run/secrets/efn_ca_crt' }, readMissing),
    ).toThrow(BffConfigError);
    expect(() =>
      assertBffTransport({ NODE_ENV: 'development', NODE_EXTRA_CA_CERTS: '/tidak/ada' }, readMissing),
    ).toThrow(BffConfigError);
  });

  it('file bukan PEM -> gagal', () => {
    expect(() =>
      assertBffTransport({ NODE_ENV: 'production', NODE_EXTRA_CA_CERTS: '/run/secrets/efn_ca_crt' }, () => 'sampah'),
    ).toThrow(BffConfigError);
  });

  it('production + CA PEM terbaca -> ok', () => {
    const t = assertBffTransport(
      { NODE_ENV: 'production', BFF_INTERNAL_URL: 'https://api-gateway:3000', NODE_EXTRA_CA_CERTS: '/run/secrets/efn_ca_crt' },
      readOk,
    );
    expect(t).toMatchObject({ baseUrl: 'https://api-gateway:3000', plaintext: false });
    expect(t.warnings).toEqual([]);
  });

  it('dev tanpa NODE_EXTRA_CA_CERTS -> peringatan saja', () => {
    const t = assertBffTransport({ NODE_ENV: 'development' }, readOk);
    expect(t.warnings.join(' ')).toMatch(/NODE_EXTRA_CA_CERTS/);
  });

  it('mode plaintext dev tidak memeriksa CA', () => {
    const t = assertBffTransport(
      { NODE_ENV: 'development', BFF_INTERNAL_URL: 'http://api-gateway:3000', EFN_ALLOW_PLAINTEXT: 'true' },
      readMissing,
    );
    expect(t.plaintext).toBe(true);
  });
});
