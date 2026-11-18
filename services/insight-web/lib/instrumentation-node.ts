/**
 * Pemeriksaan boot (runtime Node), fail closed:
 * - Kunci insight_sso_key / insight_session_key / insight_scope_key: hex >= 64 dan saling BERBEDA. Dilanggar -> exit 1
 *   SELALU (juga saat INSIGHT_ENABLED != true, untuk file yang ada).
 * - INSIGHT_ENABLED=true: seluruh konfigurasi (URL https, secret terbaca, CA internal, EFN_INSIGHT_ODOO_URL) wajib
 *   valid, kalau tidak exit 1 supaya container gagal healthcheck (docs/04 §26.10).
 * - INSIGHT_ENABLED != true dan tanpa masalah kunci: hanya log (halaman "belum diaktifkan", /sso 404).
 */
import { collectBootProblems, collectKeyProblems, parseConfig } from './config';
import { log } from './log';
import { readFileOrNull } from './runtime';

export function verifyConfigAtBoot(): void {
  const cfg = parseConfig(process.env);
  const problems = cfg.enabled
    ? collectBootProblems(cfg, process.env, readFileOrNull)
    : collectKeyProblems(cfg, readFileOrNull, false);
  if (problems.length > 0) {
    for (const p of problems) log('error', 'boot_config', { problem: p });
    process.exit(1);
  }
  log('info', 'boot', { enabled: cfg.enabled, production: cfg.production });
}
