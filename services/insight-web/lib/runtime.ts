/**
 * Akses runtime (Node): konfigurasi, kunci dari file secret, state memori (cache jti + rate limit).
 * TIDAK memakai `server-only` karena juga dipakai proxy.ts. Jangan diimpor dari komponen klien.
 * Kunci hanya dicache bila valid; yang gagal dicoba lagi di panggilan berikutnya (fail closed per panggilan).
 */
import { readFileSync } from 'node:fs';
import { parseConfig, type InsightConfig } from './config';
import { parseKey } from './hmac';
import { JtiCache } from './jti-cache';
import { RateLimiter } from './rate-limit';
import { createExportLimiter, createPageQueryLimiter } from './user-limits';

export function readFileOrNull(path: string): string | null {
  try {
    return readFileSync(path, 'utf8');
  } catch {
    return null;
  }
}

interface RuntimeState {
  config?: InsightConfig;
  keys: Map<string, string>;
  password?: string;
  jti: JtiCache;
  ssoLimiter: RateLimiter;
  pageQueryLimiter: RateLimiter;
  exportLimiter: RateLimiter;
}

const g = globalThis as unknown as { __insightRuntime?: RuntimeState };

function state(): RuntimeState {
  if (!g.__insightRuntime) {
    g.__insightRuntime = {
      keys: new Map(),
      jti: new JtiCache(),
      // docs/04 §26.4: usulan 10 permintaan/menit per IP sumber.
      ssoLimiter: new RateLimiter(10, 60_000),
      // SEC-189: batas per pengguna (kunci = sub), lihat lib/user-limits.ts.
      pageQueryLimiter: createPageQueryLimiter(),
      exportLimiter: createExportLimiter(),
    };
  }
  return g.__insightRuntime;
}

export function getConfig(): InsightConfig {
  const s = state();
  if (!s.config) s.config = parseConfig(process.env);
  return s.config;
}

export type KeyName = 'sso' | 'session' | 'scope';

export function getKey(name: KeyName): string | null {
  const s = state();
  const cached = s.keys.get(name);
  if (cached) return cached;
  const cfg = getConfig();
  const path = name === 'sso' ? cfg.ssoKeyFile : name === 'session' ? cfg.sessionKeyFile : cfg.scopeKeyFile;
  const k = parseKey(readFileOrNull(path));
  if (k) s.keys.set(name, k);
  return k;
}

export function getChPassword(): string | null {
  const s = state();
  if (s.password) return s.password;
  const raw = readFileOrNull(getConfig().chPasswordFile);
  const pw = raw === null ? '' : raw.trim();
  if (pw === '') return null;
  s.password = pw;
  return pw;
}

export function getJtiCache(): JtiCache {
  return state().jti;
}

export function getSsoLimiter(): RateLimiter {
  return state().ssoLimiter;
}

export function getPageQueryLimiter(): RateLimiter {
  return state().pageQueryLimiter;
}

export function getExportLimiter(): RateLimiter {
  return state().exportLimiter;
}
