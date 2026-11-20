import { readFileSync } from 'node:fs';
import { request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';

/**
 * Healthcheck Docker: `GET /health` ke 127.0.0.1 lewat HTTPS dengan verifikasi CA internal + servername
 * (`HEALTHCHECK_SERVERNAME`, default `wa-unofficial`). HTTP polos hanya bila TLS_CERT_FILE tidak diisi DAN
 * EFN_ALLOW_PLAINTEXT=true (dev/test). Exit 0 = sehat.
 */
const port = Number(process.env.PORT ?? '8443');
const plaintext = process.env.EFN_ALLOW_PLAINTEXT === 'true' && !process.env.TLS_CERT_FILE;
const done = (ok: boolean): never => process.exit(ok ? 0 : 1);

try {
  const opts = { host: '127.0.0.1', port, path: '/health', method: 'GET', timeout: 4000 };
  const req = plaintext
    ? httpRequest(opts)
    : httpsRequest({
        ...opts,
        ca: readFileSync(process.env.HEALTHCHECK_CA_FILE ?? process.env.AMQP_TLS_CA_FILE ?? '/run/secrets/efn_ca_crt'),
        servername: process.env.HEALTHCHECK_SERVERNAME ?? 'wa-unofficial',
        rejectUnauthorized: true,
      });
  req.on('response', (res) => {
    res.resume();
    done(res.statusCode === 200);
  });
  req.on('timeout', () => {
    req.destroy();
    done(false);
  });
  req.on('error', () => done(false));
  req.end();
} catch {
  done(false);
}
