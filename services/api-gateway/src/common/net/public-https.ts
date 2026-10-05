import { Agent, request } from 'node:https';
import { rootCertificates } from 'node:tls';

/**
 * SEC-23: klien HTTPS untuk egress ke internet publik (WhatsApp Graph API, payment provider).
 *
 * Trust store = HANYA bundel Mozilla bawaan Node (`tls.rootCertificates`) lewat opsi `ca` eksplisit pada https.Agent
 * tersendiri. Opsi `ca` menggantikan trust store default, sehingga CA internal EFN — walaupun ditambahkan ke proses
 * lewat `NODE_EXTRA_CA_CERTS` — TIDAK dipercaya untuk host publik: pemegang kunci CA internal tidak bisa
 * menerbitkan sertifikat `graph.facebook.com`/`api.xendit.co` yang diterima BFF. Koneksi internal (PG, Redis,
 * RabbitMQ, MinIO) tetap memakai CA internal eksplisit per klien (AppConfig.tlsClientOptions).
 *
 * Bentuk respons meniru subset `fetch` (ok/status/json/text) agar adapter mudah diuji dengan fake.
 */
export interface HttpRequestInit {
  method: string;
  headers: Record<string, string>;
  body?: string;
  timeoutMs: number;
}

export interface HttpResponseLike {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
  text(): Promise<string>;
}

export type PublicHttp = (url: string, init: HttpRequestInit) => Promise<HttpResponseLike>;

const MAX_RESPONSE_BYTES = 1024 * 1024;

let sharedAgent: Agent | null = null;

/** Agent bersama (keep-alive) dengan root store publik saja, TLS ≥ 1.2, verifikasi hostname aktif. */
export function publicHttpsAgent(): Agent {
  sharedAgent ??= new Agent({
    keepAlive: true,
    maxSockets: 50,
    ca: [...rootCertificates],
    rejectUnauthorized: true,
    minVersion: 'TLSv1.2',
  });
  return sharedAgent;
}

export function createPublicHttp(agent: Agent = publicHttpsAgent(), maxBytes = MAX_RESPONSE_BYTES): PublicHttp {
  return (url, init) =>
    new Promise<HttpResponseLike>((resolve, reject) => {
      let u: URL;
      try {
        u = new URL(url);
      } catch {
        reject(new Error('URL egress tidak valid'));
        return;
      }
      if (u.protocol !== 'https:') {
        reject(new Error('egress publik wajib https'));
        return;
      }
      const headers: Record<string, string | number> = { ...init.headers };
      if (init.body !== undefined) headers['Content-Length'] = Buffer.byteLength(init.body, 'utf8');
      const req = request(u, { method: init.method, headers, agent }, (res) => {
        const chunks: Buffer[] = [];
        let size = 0;
        res.on('data', (chunk: Buffer) => {
          size += chunk.length;
          if (size > maxBytes) {
            req.destroy(new Error('respons egress terlalu besar'));
            return;
          }
          chunks.push(chunk);
        });
        res.on('end', () => {
          clearTimeout(timer);
          const body = Buffer.concat(chunks).toString('utf8');
          const status = res.statusCode ?? 0;
          resolve({
            ok: status >= 200 && status < 300,
            status,
            text: async () => body,
            json: async () => JSON.parse(body) as unknown,
          });
        });
        res.on('error', (err) => {
          clearTimeout(timer);
          reject(err);
        });
      });
      const timer = setTimeout(() => req.destroy(Object.assign(new Error('timeout egress'), { name: 'TimeoutError' })), init.timeoutMs);
      timer.unref();
      req.on('error', (err) => {
        clearTimeout(timer);
        reject(err);
      });
      if (init.body !== undefined) req.write(init.body, 'utf8');
      req.end();
    });
}
