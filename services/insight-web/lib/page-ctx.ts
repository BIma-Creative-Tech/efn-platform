/**
 * Konteks halaman ESB: sesi terverifikasi + filter dari URL yang divalidasi di server. Dipakai semua halaman data F&B.
 * Sesi tanpa kode ESB (SMD-saja) dialihkan ke beranda persona; halaman ESB tidak pernah mengirim query untuk sesi itu.
 *
 * G51 B1: untuk sesi exec, `pt=SMD` dan `branch=sm:<warehouse_id>` valid; hasilnya di `sel` (lib/filters.ts PtSelection).
 * `ctx.filters.pt/branch` tetap hanya ESB, jadi query ESB tidak berubah.
 */
import 'server-only';
import { redirect } from 'next/navigation';
import { requireSession } from './auth';
import { esbCodesOf } from './claims';
import { parseFilters, type PtSelection, type RawParams } from './filters';
import type { Ctx } from './loaders';
import { getConfig } from './runtime';
import { homePath, smdAllowed } from './sm-personas';

export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export async function pageCtx(
  searchParams: SearchParams,
  opts: { home?: boolean } = {},
): Promise<{ ctx: Ctx; adjusted: boolean; raw: Record<string, string | string[] | undefined>; sel: PtSelection }> {
  const session = await requireSession();
  // G52 (§28.15.9): sesi ber-cabang (kepala toko F&B) tidak boleh memakai halaman F&B tingkat PT. Beranda `/` (home) dialihkan ke
  // /fnb/store; halaman lain -> tidak-berwenang (lapis kedua setelah proxy.ts; lapis ketiga = row policy kelas PT).
  if (session.brs.length > 0) redirect(opts.home ? '/fnb/store' : '/tidak-berwenang');
  const esb = esbCodesOf(session.codes);
  const allowed = smdAllowed(session, getConfig().smEnabled);
  // Sesi tanpa PT F&B dialihkan, kecuali exec ber-SMD (docs/04 §28.14.1): SMD terpilih bawaan, tanpa query ESB.
  if (esb.length === 0 && !allowed) redirect(homePath(session));
  const raw = await searchParams;
  const { filters, prev, adjusted, sel } = parseFilters(raw as RawParams, esb, new Date(), {
    smd: allowed,
    smWids: session.wids.filter((w) => w > 0),
  });
  return { ctx: { session, filters, prev }, adjusted, raw, sel };
}
