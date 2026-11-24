/**
 * C1 (docs/04 §26.13): login CSRF / tukar sesi pada `POST /sso`.
 * Tolak bila header `Origin` ADA dan != origin `EFN_INSIGHT_ODOO_URL`. Origin `null` (mis. halaman Odoo
 * ber-`Referrer-Policy: no-referrer` -> browser mengirim `Origin: null` pada POST lintas-origin) juga ditolak.
 * Tanpa `Origin`: DITOLAK (keputusan efn-lead 18 Nov 2026, uji E2E G42). Browser modern selalu mengirim Origin pada
 * POST lintas-origin dari halaman Odoo ber-`Referrer-Policy: strict-origin`; permintaan tanpa Origin hanya datang dari
 * klien non-browser yang tidak punya alasan sah memakai /sso. `Sec-Fetch-Site` sengaja TIDAK dipakai (QA risiko 5):
 * keputusan hanya bergantung pada Origin.
 */
export function isOriginAllowed(origin: string | null | undefined, odooOrigin: string | null): boolean {
  if (!odooOrigin) return false; // tanpa origin Odoo terkonfigurasi: fail closed
  if (origin === null || origin === undefined || origin === '') return false;
  return origin === odooOrigin;
}

/**
 * Untuk mutasi JSON `POST /api/prodplan/*` (G66): LEBIH KETAT dari /logout karena mengubah data.
 *  - `Sec-Fetch-Site` ada -> hanya `same-origin` (bukan `same-site`/`none`/`cross-site`).
 *  - tanpa `Sec-Fetch-Site` (klien lama) -> `Origin` WAJIB ada dan host-nya sama dengan header `Host`.
 */
export function isSameOriginMutation(secFetchSite: string | null | undefined, origin: string | null | undefined, host: string | null | undefined): boolean {
  if (secFetchSite !== null && secFetchSite !== undefined && secFetchSite !== '') return secFetchSite === 'same-origin';
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

/** Untuk POST /logout: hanya permintaan sama-origin (atau tanpa metadata). */
export function isSameOriginRequest(secFetchSite: string | null | undefined): boolean {
  return secFetchSite === null || secFetchSite === undefined || secFetchSite === 'same-origin' || secFetchSite === 'none';
}
