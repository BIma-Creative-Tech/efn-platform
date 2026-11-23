import { afterEach, describe, expect, it, vi } from 'vitest';
import { GET } from '@/app/go/[dest]/route';
import { GET as healthz } from '@/app/healthz/route';

const ERP = 'https://erp.efn.local:9443/web/login';
const INSIGHT = 'https://insight.efn.local:9443/';

function call(dest: string, url = 'http://127.0.0.1:3005/go/x', init?: RequestInit) {
  return GET(new Request(url, init), { params: Promise.resolve({ dest }) });
}

afterEach(() => vi.unstubAllEnvs());

describe('/go/[dest]', () => {
  it('302 ke URL allowlist', async () => {
    vi.stubEnv('LOGIN_GATEWAY_ERP_URL', ERP);
    vi.stubEnv('LOGIN_GATEWAY_INSIGHT_URL', INSIGHT);
    const erp = await call('erp');
    expect(erp.status).toBe(302);
    expect(erp.headers.get('location')).toBe(ERP);
    const ins = await call('insight');
    expect(ins.headers.get('location')).toBe(INSIGHT);
    expect(ins.headers.get('cache-control')).toBe('no-store');
  });

  it.each(['admin', 'ERP', '..%2f', 'https://evil.example', ''])('404 untuk dest tidak dikenal: %s', async (d) => {
    vi.stubEnv('LOGIN_GATEWAY_ERP_URL', ERP);
    expect((await call(d)).status).toBe(404);
  });

  it('503 bila env tidak diisi atau tidak valid', async () => {
    vi.stubEnv('LOGIN_GATEWAY_ERP_URL', '');
    vi.stubEnv('LOGIN_GATEWAY_INSIGHT_URL', 'http://insecure.local/');
    expect((await call('erp')).status).toBe(503);
    const r = await call('insight');
    expect(r.status).toBe(503);
    expect(r.headers.get('location')).toBeNull();
  });

  it('tidak pernah memantulkan query/header/host ke Location', async () => {
    vi.stubEnv('LOGIN_GATEWAY_ERP_URL', ERP);
    const r = await call('erp', 'http://127.0.0.1:3005/go/erp?next=https://evil.example&url=//evil.example&redirect=/x', {
      headers: { 'x-forwarded-host': 'evil.example', host: 'evil.example', referer: 'https://evil.example/' },
    });
    expect(r.headers.get('location')).toBe(ERP);
    expect(r.headers.get('location')).not.toContain('evil');
  });
});

describe('/healthz', () => {
  it('200 {status:"ok"}', async () => {
    const r = healthz();
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ status: 'ok' });
  });
});
