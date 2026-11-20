/**
 * Pintu masuk halaman persona (server-only): lapis kedua pemeriksaan akses (setelah proxy.ts), validasi filter, dan
 * pemuatan konfigurasi/kesegaran dasar. Penolakan = redirect ke halaman informasi ramah (bukan 404).
 */
import 'server-only';
import { redirect } from 'next/navigation';
import { requireSession } from './auth';
import { personaChip, DENIAL_PATH, PERSONA_ROUTES, smRouteDenial } from './sm-personas';
import type { Persona } from './claims';
import type { RawParams } from './filters';
import { getConfig } from './runtime';
import { loadSmBase, makeCtx, type SmCtx } from './sm-data';
import { parseSmFilters, storeWindow } from './sm-filters';

export type SmSearchParams = Promise<Record<string, string | string[] | undefined>>;

export async function smPage(
  persona: Persona,
  searchParams: SmSearchParams,
  opts: { defaultDays: number },
): Promise<{ ctx: SmCtx; adjusted: boolean; chip: string }> {
  const session = await requireSession();
  const denial = smRouteDenial(PERSONA_ROUTES[persona], session, getConfig().smEnabled);
  if (denial) redirect(DENIAL_PATH[denial]);

  const now = new Date();
  const raw = await searchParams;
  const parsed = parseSmFilters(raw as RawParams, session, now, opts);
  let filters = parsed.filters;
  if (persona === 'store') {
    // Halaman Kepala toko tanpa filter periode/kategori (§3): minggu berjalan vs minggu lalu; outlet hanya dari `wh` sah.
    const w = storeWindow(now);
    filters = { ...filters, from: w.from, to: w.to, prev: w.prev, cat: [], catDefaulted: false };
  }
  const base = await loadSmBase(session, now);
  const ctx = makeCtx(session, filters, base, now);
  const chip = personaChip(persona, session.wids, base.warehouses);
  return { ctx, adjusted: parsed.adjusted, chip };
}
