/**
 * Konteks halaman: sesi terverifikasi + filter dari URL yang divalidasi di server. Dipakai semua halaman data.
 */
import 'server-only';
import { requireSession } from './auth';
import { parseFilters, type RawParams } from './filters';
import type { Ctx } from './loaders';

export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export async function pageCtx(searchParams: SearchParams): Promise<{ ctx: Ctx; adjusted: boolean; raw: Record<string, string | string[] | undefined> }> {
  const session = await requireSession();
  const raw = await searchParams;
  const { filters, prev, adjusted } = parseFilters(raw as RawParams, session.codes, new Date());
  return { ctx: { session, filters, prev }, adjusted, raw };
}
