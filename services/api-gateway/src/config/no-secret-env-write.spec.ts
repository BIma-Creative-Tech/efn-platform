import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { SECRET_ENV_NAMES, isSecretEnvName } from './secrets.loader';

/**
 * SEC-29 (gerbang statis): tidak ada kode — termasuk test — yang menulis secret ke `process.env`.
 * Gagal bila ditemukan:
 *   - `process.env.<SECRET> = …` atau `process.env['<SECRET>'] = …` (juga `??=`, `||=`);
 *   - di kode non-test: penulisan `process.env` apa pun (`process.env.X =`, `process.env[x] =`,
 *     `Object.assign(process.env, …)`, `process.env = …`, `delete process.env…` tidak dilarang).
 * Pemeriksaan runtime (`/proc/<pid>/environ`, child process, process.report) ada di laporan Gelombang 12.
 */
const SRC = join(__dirname, '..');
const SELF = __filename;

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (p.endsWith('.ts') && p !== SELF) out.push(p);
  }
  return out;
}

const ASSIGN = '\\s*(?:\\?\\?=|\\|\\|=|=(?!=))';
const DOT_WRITE = new RegExp(`process\\.env\\.([A-Za-z_][A-Za-z0-9_]*)${ASSIGN}`, 'g');
const BRACKET_WRITE = new RegExp(`process\\.env\\[\\s*['"\`]([A-Za-z_][A-Za-z0-9_]*)['"\`]\\s*\\]${ASSIGN}`, 'g');
const ANY_WRITE = new RegExp(`process\\.env(?:\\.[A-Za-z_][A-Za-z0-9_]*|\\[[^\\]]+\\])${ASSIGN}|Object\\.assign\\(\\s*process\\.env\\b|process\\.env${ASSIGN}`);

describe('SEC-29: tidak ada secret yang ditulis ke process.env', () => {
  const files = walk(SRC);

  it('daftar nama secret mencakup semua secret BFF', () => {
    for (const n of [
      'JWT_SECRET', 'OTP_PEPPER', 'DISCOUNT_TOKEN_SECRET', 'ODOO_BFF_API_KEY', 'WEBHOOK_PAYMENT_SECRET', 'ALTCHA_HMAC_KEY',
      'PG_PASSWORD', 'REDIS_PASSWORD', 'RABBITMQ_PASSWORD', 'MINIO_SECRET_KEY', 'MINIO_ACCESS_KEY', 'PAYMENT_PROVIDER_API_KEY',
      'WHATSAPP_TOKEN', 'PII_ENCRYPTION_KEY', 'EFN_BFF_API_KEY',
    ]) {
      expect(SECRET_ENV_NAMES).toContain(n);
    }
    expect(isSecretEnvName('PII_ENCRYPTION_KEY_V7')).toBe(true);
    expect(isSecretEnvName('JWT_SECRET_FILE')).toBe(false);
  });

  it('regex mendeteksi pola terlarang (kontrol positif)', () => {
    const samples = ["process.env.JWT_SECRET = 'x'", "process.env['OTP_PEPPER'] ??= v", 'process.env.PG_PASSWORD||=v'];
    for (const s of samples) {
      const hits = [...s.matchAll(DOT_WRITE), ...s.matchAll(BRACKET_WRITE)].map((m) => m[1]);
      expect(hits.some(isSecretEnvName)).toBe(true);
    }
    expect(ANY_WRITE.test('Object.assign(process.env, out)')).toBe(true);
    expect(ANY_WRITE.test('if (process.env.X === y)')).toBe(false);
  });

  it('tidak ada penulisan nama secret ke process.env di seluruh src (termasuk spec)', () => {
    const offenders: string[] = [];
    for (const f of files) {
      const text = readFileSync(f, 'utf8');
      for (const m of [...text.matchAll(DOT_WRITE), ...text.matchAll(BRACKET_WRITE)]) {
        if (isSecretEnvName(m[1])) offenders.push(`${relative(SRC, f)}: ${m[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('kode non-test tidak menulis process.env sama sekali', () => {
    const offenders = files
      .filter((f) => !f.endsWith('.spec.ts') && !f.includes(`${join('common', 'testing')}`))
      .filter((f) => ANY_WRITE.test(readFileSync(f, 'utf8')))
      .map((f) => relative(SRC, f));
    expect(offenders).toEqual([]);
  });
});
