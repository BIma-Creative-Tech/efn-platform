/**
 * Pemuat data halaman Production Order (server-only): semua data lewat API prodplan (lib/prodplan-client.ts), tidak pernah
 * ClickHouse/PostgreSQL/ESB. Setiap pemuat mengembalikan `PpLoad<T>`: data atau galat siap tampil (pesan Indonesia).
 * Parameter `sid` = isi cookie `__Host-epp` (§30.17); prodplan memakai token ESB milik pengguna itu.
 */
import 'server-only';
import { cache } from 'react';
import { BOM_LIMIT, UUID_RE } from './prodplan-actions';
import { ppQuery, ppRequest, type PpResult } from './prodplan-client';
import { mapPpError, type PpErrorBody } from './prodplan-errors';
import {
  parseBoms,
  parseMeta,
  parsePackRules,
  type BomItem,
  parsePlan,
  parsePlanList,
  parsePreview,
  type PackRule,
  type PlanSummary,
  type Plan,
  type PostPhase,
  type PostPreview,
  type PpMeta,
} from './prodplan-types';

export type PpLoad<T> = { ok: true; data: T } | { ok: false; error: PpErrorBody };

const badResponse = (): PpLoad<never> => ({ ok: false, error: mapPpError(502, { error: { code: 'bad_response' } }) });

function lift<T>(res: PpResult<unknown>, parse: (raw: unknown) => T | null): PpLoad<T> {
  if (!res.ok) return { ok: false, error: res.error };
  const v = parse(res.data);
  return v === null ? badResponse() : { ok: true, data: v };
}

/** Meta dimuat sekali per request render (layout + halaman memakai hasil yang sama). Kunci cache = sid. */
const metaCached = cache(async (sid: string): Promise<PpLoad<PpMeta>> => lift(await ppRequest(sid, 'GET', '/v1/meta'), parseMeta));

export function loadMeta(sid: string): Promise<PpLoad<PpMeta>> {
  return metaCached(sid);
}

export interface PlanListQuery {
  brand?: string;
  from?: string;
  to?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export async function loadPlans(sid: string, q: PlanListQuery): Promise<PpLoad<{ items: PlanSummary[]; count: number }>> {
  const qs = ppQuery({ brand: q.brand, from: q.from, to: q.to, status: q.status, limit: q.limit ?? 50, offset: q.offset ?? 0 });
  return lift(await ppRequest(sid, 'GET', `/v1/plans${qs}`), (raw) => parsePlanList(raw));
}

export async function loadPlan(sid: string, id: string): Promise<PpLoad<Plan>> {
  if (!UUID_RE.test(id)) return { ok: false, error: mapPpError(404, { error: { code: 'not_found' } }) };
  return lift(await ppRequest(sid, 'GET', `/v1/plans/${id}`), parsePlan);
}

export async function loadPreview(sid: string, id: string, phase: PostPhase): Promise<PpLoad<PostPreview>> {
  if (!UUID_RE.test(id)) return { ok: false, error: mapPpError(404, { error: { code: 'not_found' } }) };
  return lift(await ppRequest(sid, 'GET', `/v1/plans/${id}/post-preview${ppQuery({ phase })}`), parsePreview);
}

export async function loadBoms(sid: string, brand: string, q: string): Promise<PpLoad<BomItem[]>> {
  return lift(await ppRequest(sid, 'GET', `/v1/boms${ppQuery({ brand, q, limit: BOM_LIMIT })}`), (raw) => parseBoms(raw));
}

export async function loadPackRules(sid: string, brand: string): Promise<PpLoad<PackRule[]>> {
  return lift(await ppRequest(sid, 'GET', `/v1/pack-rules${ppQuery({ brand })}`), (raw) => parsePackRules(raw));
}
