/**
 * Prefix global route (kontrak §1): `/v1`. Dikecualikan: health & metrics (tanpa prefix) dan endpoint migrasi internal
 * K-7 (docs/04 §9.6) yang memakai path kontrak `/internal/v1/migration/*`. Dipakai `main.ts` dan test route.
 */
export const GLOBAL_PREFIX = 'v1';
export const GLOBAL_PREFIX_EXCLUDE: string[] = [
  'health',
  'health/ready',
  'metrics',
  'internal/v1/migration/members',
  'internal/v1/migration/opening-balance',
];
