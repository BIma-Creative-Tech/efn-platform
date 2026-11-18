import { odooOpenUrl } from '@/lib/config';
import { getConfig, getJtiCache, getKey, getSsoLimiter } from '@/lib/runtime';
import { handleSso } from '@/lib/sso-handler';

export const dynamic = 'force-dynamic';

/** POST /sso (docs/04 §26.4). Method lain -> 405 otomatis oleh Next.js. */
export async function POST(req: Request): Promise<Response> {
  const cfg = getConfig();
  return handleSso(req, {
    enabled: cfg.enabled,
    odooOrigin: cfg.odooOrigin,
    odooOpenUrl: odooOpenUrl(cfg),
    ssoKey: getKey('sso'),
    sessionKey: getKey('session'),
    nowMs: () => Date.now(),
    jti: getJtiCache(),
    limiter: getSsoLimiter(),
  });
}
