import { handleExpired } from '@/lib/prodplan-login';
import { getConfig, getKey } from '@/lib/runtime';

export const dynamic = 'force-dynamic';

/**
 * `GET /api/prodplan/expired`: tujuan redirect dari halaman Produksi bila prodplan membalas 401 `session_expired`. Halaman
 * (render) tidak bisa menulis cookie, jadi route ini mengakhiri sesi di prodplan (best-effort, SEC-236), menghapus
 * `__Host-epp`, lalu mengalihkan ke `/produksi/masuk?e=expired`. Hanya menyentuh cookie milik sendiri.
 */
export async function GET(req: Request): Promise<Response> {
  const cfg = getConfig();
  return handleExpired(req, { enabled: cfg.ppEnabled, pp: { baseUrl: cfg.ppEnabled ? cfg.ppUrl : null, key: getKey('ppassert') } });
}
