/** Konfigurasi server belanja online (env non-rahasia). SERVER-SIDE SAJA. */
import 'server-only';

/**
 * Simulator pembayaran provider `mock` (docs/04 §10.7) — dev/staging saja.
 * Aktif bila `EFN_PAYMENT_SIMULATOR=true` DAN `APP_ENV` diset ke selain `production`
 * (build standalone selalu `NODE_ENV=production`, jadi staging wajib `APP_ENV=staging`; dev `next dev` cukup NODE_ENV).
 * BFF tetap menolak (404) di production atau bila provider ≠ mock.
 */
export function paymentSimulatorEnabled(env: Record<string, string | undefined> = process.env): boolean {
  if (env.EFN_PAYMENT_SIMULATOR !== 'true') return false;
  if (env.APP_ENV === 'production') return false;
  if (env.NODE_ENV === 'production' && !env.APP_ENV) return false;
  return true;
}
