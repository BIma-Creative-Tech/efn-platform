import { handleLogout } from '@/lib/prodplan-login';
import { getConfig, getKey } from '@/lib/runtime';

export const dynamic = 'force-dynamic';

/** `POST /api/prodplan/logout` (docs/04 §30.17.2): akhiri sesi ESB di prodplan, hapus cookie, kembali ke halaman masuk. */
export async function POST(req: Request): Promise<Response> {
  const cfg = getConfig();
  return handleLogout(req, { enabled: cfg.ppEnabled, pp: { baseUrl: cfg.ppEnabled ? cfg.ppUrl : null, key: getKey('ppassert') } });
}
