import { handleLogin } from '@/lib/prodplan-login';
import { getConfig, getKey, getPpLoginLimiter } from '@/lib/runtime';

export const dynamic = 'force-dynamic';

/** `POST /api/prodplan/login` (docs/04 §30.17.2): form username + password ESB -> sesi `__Host-epp`. Method lain -> 405. */
export async function POST(req: Request): Promise<Response> {
  const cfg = getConfig();
  return handleLogin(req, {
    enabled: cfg.ppEnabled,
    pp: { baseUrl: cfg.ppEnabled ? cfg.ppUrl : null, key: getKey('ppassert') },
    limiter: getPpLoginLimiter(),
    nowMs: () => Date.now(),
  });
}
