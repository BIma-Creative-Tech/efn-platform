'use strict';
/**
 * Healthcheck container BFF (docs/04 §7.1). Bila TLS_CERT_FILE diset → HTTPS ke 127.0.0.1 dengan verifikasi
 * sertifikat terhadap CA internal (HEALTHCHECK_CA_FILE / EFN_CA_FILE, default /run/secrets/efn_ca_crt) dan
 * hostname = HEALTHCHECK_SERVERNAME (default "api-gateway", nama di SAN sertifikat). Tanpa TLS_CERT_FILE
 * (hanya mode EFN_ALLOW_PLAINTEXT non-production) → HTTP. Tidak pernah rejectUnauthorized=false.
 */
const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');

const port = Number(process.env.PORT || 3000);
const useTls = Boolean(process.env.TLS_CERT_FILE);
const done = (ok) => process.exit(ok ? 0 : 1);

try {
  const opts = { host: '127.0.0.1', port, path: '/health', method: 'GET', timeout: 4000 };
  let mod = http;
  if (useTls) {
    mod = https;
    opts.ca = fs.readFileSync(process.env.HEALTHCHECK_CA_FILE || process.env.EFN_CA_FILE || '/run/secrets/efn_ca_crt');
    opts.servername = process.env.HEALTHCHECK_SERVERNAME || 'api-gateway';
    opts.rejectUnauthorized = true;
    opts.minVersion = 'TLSv1.2';
  }
  const req = mod.request(opts, (res) => {
    res.resume();
    done(res.statusCode >= 200 && res.statusCode < 300);
  });
  req.on('timeout', () => req.destroy(new Error('timeout')));
  req.on('error', () => done(false));
  req.end();
} catch {
  done(false);
}
