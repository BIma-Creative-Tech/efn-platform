import { describe, expect, it } from 'vitest';
import nextConfig, { securityHeaders } from '../next.config.mjs';
import { API_CSP, buildCsp } from '@/lib/csp';

describe('buildCsp (production)', () => {
  const csp = buildCsp('abc123', false);
  it('ketat: tanpa unsafe-inline/unsafe-eval, frame-ancestors none', () => {
    expect(csp).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(csp).not.toContain('unsafe-inline');
    expect(csp).not.toContain('unsafe-eval');
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("form-action 'none'");
    expect(csp).toContain("object-src 'none'");
  });
});

describe('API_CSP', () => {
  it('tidak memuat apa pun', () => {
    expect(API_CSP).toContain("default-src 'none'");
    expect(API_CSP).toContain("frame-ancestors 'none'");
  });
});

describe('next.config', () => {
  const get = (k: string) => securityHeaders.find((h) => h.key === k)?.value;
  it('header keamanan dasar', () => {
    expect(get('X-Frame-Options')).toBe('DENY');
    expect(get('Referrer-Policy')).toBe('no-referrer');
    expect(get('X-Content-Type-Options')).toBe('nosniff');
    expect(get('Permissions-Policy')).toContain('camera=()');
  });
  it('poweredByHeader off, standalone', () => {
    expect(nextConfig.poweredByHeader).toBe(false);
    expect(nextConfig.output).toBe('standalone');
  });
});
