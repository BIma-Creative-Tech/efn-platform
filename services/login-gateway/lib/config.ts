/**
 * Konfigurasi tujuan redirect (ADR-050). SATU-SATUNYA sumber URL tujuan: env server, divalidasi di sini.
 * Tidak pernah dari query/body/header. Tidak ada NEXT_PUBLIC_*: URL tidak dikirim ke browser sebagai konfigurasi.
 */
export const DESTINATIONS = ['erp', 'insight'] as const;
export type Destination = (typeof DESTINATIONS)[number];

const ENV_KEY: Record<Destination, string> = {
  erp: 'LOGIN_GATEWAY_ERP_URL',
  insight: 'LOGIN_GATEWAY_INSIGHT_URL',
};

export type DestinationState = { enabled: true; url: string } | { enabled: false; reason: 'unset' | 'invalid' };
export type GatewayConfig = Record<Destination, DestinationState>;

export function isDestination(value: string): value is Destination {
  return (DESTINATIONS as readonly string[]).includes(value);
}

/** URL absolut https tanpa kredensial. Mengembalikan bentuk ternormalisasi (href) atau null. */
export function parseHttpsUrl(raw: string | undefined): string | null {
  const value = (raw ?? '').trim();
  if (!value) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== 'https:' || !url.hostname) return null;
  if (url.username || url.password) return null;
  return url.href;
}

export function loadConfig(env: Record<string, string | undefined> = process.env): GatewayConfig {
  const out = {} as GatewayConfig;
  for (const dest of DESTINATIONS) {
    const raw = env[ENV_KEY[dest]];
    if (raw === undefined || raw.trim() === '') {
      out[dest] = { enabled: false, reason: 'unset' };
      continue;
    }
    const url = parseHttpsUrl(raw);
    out[dest] = url ? { enabled: true, url } : { enabled: false, reason: 'invalid' };
  }
  return out;
}

/** Untuk log startup: nama variabel saja, tidak pernah nilai URL. */
export function describeProblems(config: GatewayConfig): string[] {
  return DESTINATIONS.filter((d) => !config[d].enabled).map((d) => {
    const state = config[d];
    return `${ENV_KEY[d]}: ${state.enabled ? '' : state.reason === 'unset' ? 'belum diisi' : 'bukan URL https absolut'}`;
  });
}
