/**
 * Pintu masuk halaman G67 (`/pnl`, `/waste`, `/fill-rate`; server-only; docs/04 §31.8-§31.9): lapis kedua pemeriksaan akses
 * (setelah proxy.ts), validasi filter global dari URL (hanya ESB: `pt=SMD`/cabang `sm:` ditolak sebagai tidak valid), dan konteks
 * query. Penolakan = redirect ke halaman informasi ramah. Cakupan data selalu dari SESI (sig3), bukan dari URL (lapis 1: filter
 * `company_code`; lapis 2: row policy kelas PT di ClickHouse, §31.7).
 */
import 'server-only';
import { redirect } from 'next/navigation';
import { requireSession } from './auth';
import { esbCodesOf } from './claims';
import { FNB_DENIAL_PATH } from './fnb-personas';
import { G67_DISABLED_PATH, g67RouteDenial, type G67Path } from './g67-access';
import { parseFilters, type RawParams } from './filters';
import type { Ctx } from './loaders';
import { getConfig } from './runtime';

export type G67SearchParams = Promise<Record<string, string | string[] | undefined>>;

export interface G67Page {
  ctx: Ctx;
  /** true bila param filter global ditolak/diganti bawaan. */
  adjusted: boolean;
  raw: Record<string, string | string[] | undefined>;
}

export async function g67Page(path: G67Path, searchParams: G67SearchParams): Promise<G67Page> {
  const session = await requireSession();
  const cfg = getConfig();
  const denial = g67RouteDenial(path, session, { g67Enabled: cfg.g67Enabled, fnbEnabled: cfg.fnbEnabled });
  if (denial) redirect(denial === 'g67-belum-aktif' ? G67_DISABLED_PATH : FNB_DENIAL_PATH[denial]);
  const raw = await searchParams;
  const { filters, prev, adjusted } = parseFilters(raw as RawParams, esbCodesOf(session.codes), new Date());
  return { ctx: { session, filters, prev }, adjusted, raw };
}
