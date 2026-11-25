/**
 * Konfigurasi insight-web dari env (docs/04 §26.10). Modul murni (tanpa I/O): pembacaan file disuntikkan.
 *
 * Aturan keamanan:
 * - `INSIGHT_ENABLED` hanya aktif bila tepat "true" (default false = kill switch).
 * - `CH_URL` dan `EFN_INSIGHT_ODOO_URL` wajib https://. http:// hanya bila EFN_ALLOW_PLAINTEXT=true DAN bukan
 *   production (APP_ENV/NODE_ENV=production). Tidak ada kredensial/query/fragment di URL.
 * - `NODE_TLS_REJECT_UNAUTHORIZED=0` ditolak di production. CA internal lewat NODE_EXTRA_CA_CERTS
 *   (dibaca Node sekali saat start; file diperiksa sendiri saat boot).
 * - Tiga kunci HMAC harus valid (hex >= 64) dan BERBEDA satu sama lain (docs/04 §26.10; ADR-024).
 */
import { parseDcCodes } from './fnb-forecast';
import { parseKey } from './hmac';

export type Env = Record<string, string | undefined>;
export type ReadFile = (path: string) => string | null;

export interface InsightConfig {
  enabled: boolean;
  /** INSIGHT_SM_ENABLED (docs/04 §28.13.12): aktif hanya string persis "true"; default false -> halaman supermarket "belum aktif". */
  smEnabled: boolean;
  /** INSIGHT_ACCEPT_EIT1 (§28.13.5): default true bila tidak diset; bila diset hanya "true" persis yang menerima eit1/eis1. */
  acceptEit1: boolean;
  /** INSIGHT_ACCEPT_EIT2 (§28.15.5): default true (rollout G52); diset false setelah Odoo 19.0.3.0.0 di semua env. Hanya "true" persis yang menerima bila diset. */
  acceptEit2: boolean;
  /** INSIGHT_FNB_PERSONA_ENABLED (§28.15.10): aktif hanya string persis "true"; default false. TIDAK bergantung INSIGHT_SM_ENABLED. */
  fnbEnabled: boolean;
  /** INSIGHT_FNB_DC_CODES: kode gudang pusat (agregat outlet) yang dikeluarkan dari total forecast F&B; dipisah koma, default kosong. */
  fnbDcCodes: string[];
  /**
   * INSIGHT_PRODPLAN_ENABLED (docs/04 §30.3, G66): aktif hanya string persis "true" DAN `PRODPLAN_URL` valid (selain
   * http://127.0.0.1:7443 = fitur nonaktif, fail closed). Default false -> halaman /produksi "belum aktif".
   */
  ppEnabled: boolean;
  /**
   * INSIGHT_G68_ENABLED (docs/04 §32.8, G68): aktif hanya string persis "true"; default false. `false` -> /pnl, /waste, /fill-rate
   * dialihkan (rewrite) ke halaman "Segera" dan item menu disembunyikan. Production tetap false sampai QA + security review lulus.
   */
  g68Enabled: boolean;
  /** Origin sidecar prodplan (selalu `http://127.0.0.1:7443` bila valid), null bila PRODPLAN_URL tidak sah. */
  ppUrl: string | null;
  ppAssertKeyFile: string;
  production: boolean;
  allowPlaintext: boolean;
  chUrl: string;
  chUser: string;
  chPasswordFile: string;
  scopeKeyFile: string;
  ssoKeyFile: string;
  sessionKeyFile: string;
  /** Origin Odoo (untuk cek Origin /sso, C1). null bila tidak diset/tidak valid. */
  odooOrigin: string | null;
  /** Masalah konfigurasi tingkat env (URL tidak aman, dll). */
  problems: string[];
}

function checkUrl(name: string, raw: string, allowPlaintext: boolean, problems: string[]): URL | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    problems.push(`${name} bukan URL valid`);
    return null;
  }
  if (url.username || url.password) problems.push(`${name} tidak boleh memuat kredensial`);
  if (url.search || url.hash) problems.push(`${name} tidak boleh memuat query/fragment`);
  if (url.protocol === 'http:') {
    if (!allowPlaintext) problems.push(`${name} wajib https:// (http:// hanya dev dengan EFN_ALLOW_PLAINTEXT=true)`);
  } else if (url.protocol !== 'https:') {
    problems.push(`${name} harus berskema https://`);
  }
  return url;
}

/** Satu-satunya tujuan sah: sidecar ghostunnel client di loopback namespace yang sama (§30.1). */
export const PRODPLAN_SIDECAR_URL = 'http://127.0.0.1:7443';

/** `PRODPLAN_URL` (default sidecar). Selain tepat http://127.0.0.1:7443 (tanpa kredensial/path/query) -> null. */
export function parsePpUrl(raw: string | undefined): string | null {
  const v = (raw ?? '').trim() === '' ? PRODPLAN_SIDECAR_URL : (raw ?? '').trim();
  try {
    const u = new URL(v);
    const okPath = u.pathname === '/' || u.pathname === '';
    if (u.protocol === 'http:' && u.hostname === '127.0.0.1' && u.port === '7443' && !u.username && !u.password && okPath && !u.search && !u.hash) {
      return PRODPLAN_SIDECAR_URL;
    }
  } catch {
    /* tidak sah */
  }
  return null;
}

export function parseConfig(env: Env): InsightConfig {
  const production = env.APP_ENV === 'production' || env.NODE_ENV === 'production';
  const allowPlaintext = env.EFN_ALLOW_PLAINTEXT === 'true' && !production;
  const problems: string[] = [];

  const ch = checkUrl('CH_URL', (env.CH_URL || 'https://clickhouse:8443').trim(), allowPlaintext, problems);

  let odooOrigin: string | null = null;
  const odooRaw = (env.EFN_INSIGHT_ODOO_URL ?? '').trim();
  if (odooRaw !== '') {
    const u = checkUrl('EFN_INSIGHT_ODOO_URL', odooRaw, allowPlaintext, problems);
    if (u) odooOrigin = u.origin;
  }

  if (env.NODE_TLS_REJECT_UNAUTHORIZED === '0' && production) {
    problems.push('NODE_TLS_REJECT_UNAUTHORIZED=0 ditolak di production');
  }

  const ppUrl = parsePpUrl(env.PRODPLAN_URL);

  return {
    enabled: env.INSIGHT_ENABLED === 'true',
    ppEnabled: env.INSIGHT_PRODPLAN_ENABLED === 'true' && ppUrl !== null,
    ppUrl,
    g68Enabled: env.INSIGHT_G68_ENABLED === 'true',
    ppAssertKeyFile: (env.PRODPLAN_ASSERT_KEY_FILE || '/run/secrets/prodplan_assert_key').trim(),
    smEnabled: env.INSIGHT_SM_ENABLED === 'true',
    acceptEit1: env.INSIGHT_ACCEPT_EIT1 === undefined || env.INSIGHT_ACCEPT_EIT1.trim() === '' ? true : env.INSIGHT_ACCEPT_EIT1 === 'true',
    acceptEit2: env.INSIGHT_ACCEPT_EIT2 === undefined || env.INSIGHT_ACCEPT_EIT2.trim() === '' ? true : env.INSIGHT_ACCEPT_EIT2 === 'true',
    fnbEnabled: env.INSIGHT_FNB_PERSONA_ENABLED === 'true',
    fnbDcCodes: parseDcCodes(env.INSIGHT_FNB_DC_CODES),
    production,
    allowPlaintext,
    chUrl: ch ? `${ch.origin}${ch.pathname.replace(/\/+$/, '')}` : '',
    chUser: (env.CH_USER || 'efn_insight').trim(),
    chPasswordFile: (env.CH_PASSWORD_FILE || '/run/secrets/clickhouse_password_insight').trim(),
    scopeKeyFile: (env.INSIGHT_SCOPE_KEY_FILE || '/run/secrets/insight_scope_key').trim(),
    ssoKeyFile: (env.INSIGHT_SSO_KEY_FILE || '/run/secrets/insight_sso_key').trim(),
    sessionKeyFile: (env.INSIGHT_SESSION_KEY_FILE || '/run/secrets/insight_session_key').trim(),
    odooOrigin,
    problems,
  };
}

/**
 * Tautan masuk ulang ke Insight lewat Odoo (docs/04 §26.2): `{origin}/efn/insight/open` menerbitkan token SSO baru
 * lalu kembali ke Insight. Hanya untuk halaman sesi berakhir/error SSO. null bila belum dikonfigurasi.
 */
export function odooOpenUrl(cfg: Pick<InsightConfig, 'odooOrigin'>): string | null {
  return cfg.odooOrigin ? `${cfg.odooOrigin}/efn/insight/open` : null;
}

/** Tautan "Kembali ke Odoo" (docs/04 §26.2): web client `{origin}/odoo`. null bila belum dikonfigurasi. */
export function odooHomeUrl(cfg: Pick<InsightConfig, 'odooOrigin'>): string | null {
  return cfg.odooOrigin ? `${cfg.odooOrigin}/odoo` : null;
}

/**
 * Kunci SSO/sesi/cakupan: tiap kunci hex >= 64 dan ketiganya saling BERBEDA. `requirePresent=false` (INSIGHT_ENABLED
 * != true): file yang tidak ada dilewati, tetapi file yang ada namun lemah/kembar tetap dilaporkan.
 */
export function collectKeyProblems(cfg: InsightConfig, readFile: ReadFile, requirePresent: boolean): string[] {
  const out: string[] = [];
  const keys: [string, string][] = [
    ['insight_sso_key', cfg.ssoKeyFile],
    ['insight_session_key', cfg.sessionKeyFile],
    ['insight_scope_key', cfg.scopeKeyFile],
  ];
  // G66 (§30.2): prodplan_assert_key wajib ada, valid, dan berbeda dari kunci lain bila modul produksi aktif.
  if (cfg.ppEnabled) keys.push(['prodplan_assert_key', cfg.ppAssertKeyFile]);
  const seen = new Map<string, string>();
  for (const [label, path] of keys) {
    const raw = readFile(path);
    if (raw === null && !requirePresent) continue;
    const k = parseKey(raw);
    if (!k) {
      out.push(`${label} tidak terbaca atau tidak valid (hex >= 64 karakter)`);
      continue;
    }
    const dup = seen.get(k);
    if (dup) out.push(`${label} sama dengan ${dup} (kunci harus berbeda)`);
    else seen.set(k, label);
  }
  return out;
}

/**
 * Pemeriksaan boot saat INSIGHT_ENABLED=true. Daftar kosong = aman. Pesan tidak memuat isi secret.
 */
export function collectBootProblems(cfg: InsightConfig, env: Env, readFile: ReadFile): string[] {
  const out = [...cfg.problems];
  if (!cfg.odooOrigin) out.push('EFN_INSIGHT_ODOO_URL wajib diisi (cek Origin /sso dan tautan kembali)');

  out.push(...collectKeyProblems(cfg, readFile, true));

  const pw = readFile(cfg.chPasswordFile);
  if (!pw || pw.trim() === '') out.push('clickhouse_password_insight tidak terbaca/kosong');

  if (cfg.chUrl.startsWith('https:')) {
    const caPath = env.NODE_EXTRA_CA_CERTS;
    if (!caPath) {
      if (cfg.production) out.push('NODE_EXTRA_CA_CERTS wajib di-set ke CA internal (mis. /run/secrets/efn_ca_crt)');
    } else {
      const pem = readFile(caPath);
      if (!pem || !pem.includes('-----BEGIN CERTIFICATE-----')) {
        out.push('NODE_EXTRA_CA_CERTS tidak terbaca atau bukan sertifikat PEM');
      }
    }
  }
  return out;
}
