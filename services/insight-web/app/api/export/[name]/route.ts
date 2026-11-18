import { API_CSP } from '@/lib/csp';
import { authFromRequest } from '@/lib/auth';
import { toCsv, type CsvCell } from '@/lib/csv';
import { parseFilters } from '@/lib/filters';
import { TABLE_LOADERS, TABLE_QUERIES, settle, type Ctx } from '@/lib/loaders';
import { log } from '@/lib/log';
import { getExportLimiter } from '@/lib/runtime';
import { STATUS_LABEL, type Status } from '@/lib/thresholds';
import { isTableName, TABLES } from '@/lib/table-defs';

export const dynamic = 'force-dynamic';

const BASE: Record<string, string> = {
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': API_CSP,
};

const empty = (status: number, extra: Record<string, string> = {}): Response => new Response(null, { status, headers: { ...BASE, ...extra } });

/**
 * Ekspor CSV satu tabel (docs/04 §26.6). Cakupan = sesi (cookie), filter = query string yang sama dengan halaman,
 * data = pemuat tabel yang sama dengan halaman. Galat/otorisasi gagal -> status saja, tanpa badan rinci.
 * SEC-189: maks 5 ekspor / 10 menit per pengguna (`sub`) -> 429; query memakai semaphore ekspor terpisah (pool 'export').
 * SEC-194: log audit memuat PT (kode sesi yang dipakai filter), rentang, nama query, jumlah baris; tanpa nama/token.
 */
export async function GET(req: Request, ctx: { params: Promise<{ name: string }> }): Promise<Response> {
  const auth = authFromRequest(req);
  if (auth.status === 'disabled' || auth.status === 'misconfigured') return empty(503);
  if (auth.status !== 'ok') return empty(401);

  const { name } = await ctx.params;
  if (!isTableName(name)) return empty(404);

  const rl = getExportLimiter().hit(String(auth.session.sub), Date.now());
  if (!rl.allowed) {
    log('warn', 'export', { table: name, sub: auth.session.sub, code: 'rate_limited', retry_after: rl.retryAfterSec });
    return empty(429, { 'Retry-After': String(rl.retryAfterSec) });
  }

  const { filters, prev } = parseFilters(new URL(req.url).searchParams, auth.session.codes, new Date());
  const c: Ctx = { session: auth.session, filters, prev, pool: 'export' };
  const audit = { table: name, query: TABLE_QUERIES[name], pt: filters.pt.join(','), from: filters.from, to: filters.to, sub: auth.session.sub };
  const res = await settle(TABLE_LOADERS[name](c));
  if (!res.ok) {
    log('error', 'export', { ...audit, ref: res.ref });
    return empty(res.reason === 'rate_limited' ? 429 : 500);
  }

  const def = TABLES[name];
  const rows = res.data.map((r) => {
    const out: Record<string, CsvCell> = {};
    for (const col of def.columns) {
      const v = r[col.key];
      out[col.key] = col.fmt === 'status' && typeof v === 'string' ? STATUS_LABEL[v as Status] ?? v : v;
    }
    return out;
  });
  const body = toCsv(def.columns, rows);
  log('info', 'export', { ...audit, rows: rows.length });
  return new Response(body, {
    status: 200,
    headers: {
      ...BASE,
      'Content-Type': 'text/csv; charset=utf-8',
      'Content-Disposition': `attachment; filename="efn-insight-${def.filename}-${filters.from}_${filters.to}.csv"`,
    },
  });
}
