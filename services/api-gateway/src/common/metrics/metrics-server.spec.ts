import { testConfig } from '../testing/fakes';
import { clientNameAllowed } from './metrics-server';

describe('Metrics mTLS (§8.10)', () => {
  it('hanya klien dengan CN/SAN DNS di allowlist', () => {
    const allowed = ['prometheus'];
    expect(clientNameAllowed({ subject: { CN: 'prometheus' } as never, subjectaltname: '' }, allowed)).toBe(true);
    expect(clientNameAllowed({ subject: { CN: 'x' } as never, subjectaltname: 'DNS:localhost, DNS:prometheus' }, allowed)).toBe(true);
    expect(clientNameAllowed({ subject: { CN: 'superapp-web' } as never, subjectaltname: 'DNS:superapp-web' }, allowed)).toBe(false);
    expect(clientNameAllowed({ subject: { CN: 'evil-prometheus' } as never, subjectaltname: 'DNS:prometheus.evil' }, allowed)).toBe(false);
    expect(clientNameAllowed(null, allowed)).toBe(false);
  });

  it('config: port default 9464, allowlist default prometheus, CA klien wajib terbaca', () => {
    const cfg = testConfig();
    expect(cfg.metricsPort).toBe(9464);
    expect(cfg.metricsAllowedClientNames).toEqual(['prometheus']);
    expect(() => cfg.metricsClientCa).toThrow('METRICS_CLIENT_CA_FILE');
    expect(testConfig({ METRICS_TLS_CLIENT_ALLOWED_CN: 'prometheus,grafana' }).metricsAllowedClientNames).toEqual(['prometheus', 'grafana']);
  });
});
