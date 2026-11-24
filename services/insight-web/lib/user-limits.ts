/**
 * Batas per pengguna (kunci = `sub` dari sesi terverifikasi) dan pemisahan slot ekspor (SEC-189, R-S10).
 * Semua staf memakai SATU user ClickHouse `efn_insight` dengan kuota per jam bersama; tanpa batas ini satu staf
 * dapat menghabiskan kuota/semaphore untuk semua orang. Di memori, replika = 1 (L-124).
 *
 * - Halaman: maks 300 query ClickHouse per 5 menit per `sub` (halaman biasa memicu ~13 query -> ~23 tampilan).
 *   Melebihi -> kartu galat ramah "terlalu banyak permintaan" (bukan 500).
 * - Ekspor CSV: maks 5 per 10 menit per `sub` -> 429 + Retry-After.
 * - Semaphore ekspor TERPISAH (default 3 slot, env INSIGHT_CH_EXPORT_CONCURRENCY 1..8) agar ekspor tidak memakan slot
 *   halaman (semaphore halaman 12, INSIGHT_CH_MAX_CONCURRENCY). Total <= 24 = max_concurrent_queries_for_user.
 */
import { RateLimiter } from './rate-limit';

export const PAGE_QUERY_LIMIT = 300;
export const PAGE_QUERY_WINDOW_MS = 5 * 60_000;
export const EXPORT_LIMIT = 5;
export const EXPORT_WINDOW_MS = 10 * 60_000;
export const EXPORT_CONCURRENCY_DEFAULT = 3;

/**
 * G66 (docs/04 §30.8): prodplan membatasi 60 mutasi/menit per `sub`; sisi insight-web memakai batas yang sama agar penolakan
 * terjadi lebih awal. Pembacaan (polling job tiap 2 dtk, pratinjau): 150/menit per `sub`.
 */
export const PP_MUTATION_LIMIT = 60;
export const PP_READ_LIMIT = 150;
export const PP_WINDOW_MS = 60_000;
export const createPpMutationLimiter = (): RateLimiter => new RateLimiter(PP_MUTATION_LIMIT, PP_WINDOW_MS);
export const createPpReadLimiter = (): RateLimiter => new RateLimiter(PP_READ_LIMIT, PP_WINDOW_MS);

/** Login Produksi (§30.17.5): 10 percobaan / 15 menit per IP sumber (clientIp). */
export const PP_LOGIN_LIMIT = 10;
export const PP_LOGIN_WINDOW_MS = 15 * 60_000;
export const createPpLoginLimiter = (): RateLimiter => new RateLimiter(PP_LOGIN_LIMIT, PP_LOGIN_WINDOW_MS);

export const createPageQueryLimiter =(): RateLimiter => new RateLimiter(PAGE_QUERY_LIMIT, PAGE_QUERY_WINDOW_MS);
export const createExportLimiter = (): RateLimiter => new RateLimiter(EXPORT_LIMIT, EXPORT_WINDOW_MS);

/** `INSIGHT_CH_EXPORT_CONCURRENCY`: bilangan bulat 1..8, selain itu default 3. */
export function exportConcurrencyFromEnv(raw: string | undefined): number {
  const n = raw === undefined || raw === '' ? EXPORT_CONCURRENCY_DEFAULT : Number(raw);
  return Number.isInteger(n) && n >= 1 && n <= 8 ? n : EXPORT_CONCURRENCY_DEFAULT;
}
