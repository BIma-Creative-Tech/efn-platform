/**
 * Konteks halaman ESB: sesi terverifikasi + filter dari URL yang divalidasi di server. Dipakai semua halaman data F&B.
 * Sesi tanpa kode ESB (SMD-saja) dialihkan ke beranda persona; halaman ESB tidak pernah mengirim query untuk sesi itu.
 */
import 'server-only';
import { redirect } from 'next/navigation';
import { requireSession } from './auth';
import { esbCodesOf } from './claims';
import { parseFilters, type RawParams } from './filters';
import type { Ctx } from './loaders';
import { homePath } from './sm-personas';

export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export async function pageCtx(searchParams: SearchParams): Promise<{ ctx: Ctx; adjusted: boolean; raw: Record<string, string | string[] | undefined> }> {
  const session = await requireSession();
  const esb = esbCodesOf(session.codes);
  if (esb.length === 0) redirect(homePath(session));
  const raw = await searchParams;
  const { filters, prev, adjusted } = parseFilters(raw as RawParams, esb, new Date());
  return { ctx: { session, filters, prev }, adjusted, raw };
}
