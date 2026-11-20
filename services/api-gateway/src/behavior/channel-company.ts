import { Queryable } from '../common/db/db';
import { isCompanyCode } from '../loyalty/loyalty.service';

/**
 * Pemetaan kanal → PT untuk `behavior.event_raw.company_code` (revisi G50-A, selaras ETL `behavior_events`).
 *
 * Sumber = pemetaan yang sudah dipakai BFF untuk katalog online: blok `channel` Odoo yang disalin ke
 * `platform.online_catalog_sync.channel_data` (§10.8.2; `CatalogService.fromOdoo`). Aturan fail-closed yang sama
 * (L-11): `channel_data.code` = kode kanal, `active = true`, `company_code` ∈ `COMPANY_CODES`. Kanal yang tidak lolos
 * TIDAK ditebak (mis. dari aturan G20 "kode kanal = kode PT") → event ditolak `unknown_channel`.
 * Catatan: tidak dibatasi `ONLINE_CHANNELS` agar kanal QR/booking yang disalin (G22/G23) juga terpetakan.
 */
export async function resolveChannelCompanies(q: Queryable, channelCodes: readonly string[]): Promise<Map<string, string>> {
  const out = new Map<string, string>();
  const codes = [...new Set(channelCodes)];
  if (codes.length === 0) return out;
  const res = await q.query<{ channel_code: string; code: string | null; active: string | null; company_code: string | null }>(
    `SELECT channel_code, channel_data ->> 'code' AS code, channel_data ->> 'active' AS active,
            channel_data ->> 'company_code' AS company_code
       FROM platform.online_catalog_sync
      WHERE channel_code = ANY($1::text[]) AND last_ok_at IS NOT NULL AND channel_data IS NOT NULL`,
    [codes],
  );
  for (const r of res.rows) {
    if (r.code !== r.channel_code || r.active !== 'true' || !isCompanyCode(r.company_code)) continue;
    out.set(r.channel_code, r.company_code);
  }
  return out;
}
