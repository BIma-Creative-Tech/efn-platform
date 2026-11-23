/**
 * KATALOG QUERY halaman "Pengunjung" lintas PT (V-01, 23 Nov 2026). Semua SQL konstanta; tanpa SQL/identifier dari pengguna.
 * Nilai filter hanya lewat placeholder bertipe `{nama:Tipe}` + parameter HTTP `param_nama`.
 *
 * Aturan katalog (dijaga tests/visitor.test.ts):
 *  - hanya view `efn_insight.v_vis_*` (init/23-v01-visitor-insight.sql + access/insight-visitor-access.sql);
 *  - setiap query memfilter `company_code IN {codes:Array(String)}` (lapis 1) di samping row policy bertanda tangan (lapis 2);
 *  - produk dan istilah pencarian hanya tampil bila >= K_ANON visitor (literal, sama dengan sm-defaults K_ANON);
 *  - alias agregat tidak sama dengan nama kolom view; query ber-GROUP BY/ORDER BY wajib LIMIT literal.
 * Satuan visitor = perangkat-hari (Σ per hari; funnel/produk juga Σ per outlet), dihitung dari SEMUA visitor (anonim + consented).
 *
 * STATUS: belum dijalankan terhadap ClickHouse 24.8 (tanpa shell saat ditulis); verifikasi lead.
 */
import 'server-only';
import type { ChType } from './queries';
import { K_ANON } from './sm-defaults';

export const VIS_PARAM_TYPES = {
  from: 'Date',
  to: 'Date',
  prev_from: 'Date',
  prev_to: 'Date',
  codes: 'Array(String)',
  channel: 'String',
} as const satisfies Record<string, ChType>;

export type VisParamName = keyof typeof VIS_PARAM_TYPES;

export interface VisQueryDef {
  sql: string;
  params: readonly VisParamName[];
}

const PLACEHOLDER = /\{(\w+):([A-Za-z0-9()]+)\}/g;

function q(sql: string): VisQueryDef {
  const found = new Set<string>();
  for (const m of sql.matchAll(PLACEHOLDER)) {
    const [, name, type] = m;
    const want = (VIS_PARAM_TYPES as Record<string, string>)[name];
    if (want === undefined || want !== type) throw new Error(`queries-visitor: placeholder ${name}:${type} tidak cocok katalog`);
    found.add(name);
  }
  return { sql, params: Array.from(found).sort() as VisParamName[] };
}

const CO = 'company_code IN {codes:Array(String)}';
const CH = "({channel:String} = '' OR channel = {channel:String})";
const SCOPE = `${CO} AND ${CH}`;
const D_CUR = '(day BETWEEN {from:Date} AND {to:Date})';
const D_PREV = '(day BETWEEN {prev_from:Date} AND {prev_to:Date})';
const V = 'efn_insight.v_vis_';

export const VIS_QUERIES = {
  // Baris page = '*' = semua visitor per hari x kanal (agg_visitor_page_daily).
  vis_kpi: q(`SELECT toUInt64(sumIf(visitors, ${D_CUR})) AS visitors_cur, toUInt64(sumIf(views, ${D_CUR})) AS views_cur,
  toUInt64(sumIf(visitors, ${D_PREV})) AS visitors_prev, toUInt64(sumIf(views, ${D_PREV})) AS views_prev, maxIf(day, ${D_CUR}) AS last_day
FROM ${V}page_daily
WHERE ${SCOPE} AND page = '*' AND (${D_CUR} OR ${D_PREV})`),
  vis_daily: q(`SELECT day, company_code, toUInt64(sum(visitors)) AS visitors_n, toUInt64(sum(views)) AS views_n
FROM ${V}page_daily
WHERE ${SCOPE} AND page = '*' AND ${D_CUR}
GROUP BY day, company_code
ORDER BY day
LIMIT 3000`),
  vis_pages: q(`SELECT page, toUInt64(sum(views)) AS views_n, toUInt64(sum(visitors)) AS visitors_n
FROM ${V}page_daily
WHERE ${SCOPE} AND page != '*' AND ${D_CUR}
GROUP BY page
ORDER BY views_n DESC
LIMIT 20`),
  vis_funnel: q(`SELECT toUInt64(sum(viewers)) AS view_n, toUInt64(sum(carters)) AS cart_n, toUInt64(sum(checkout_starters)) AS chk_n,
  toUInt64(sum(purchasers)) AS buy_n
FROM ${V}funnel_daily
WHERE ${SCOPE} AND ${D_CUR}`),
  vis_products: q(`SELECT product_id, any(product_name) AS name_text, any(company_code) AS code_text, toUInt64(sum(views)) AS views_n,
  toUInt64(sum(viewers)) AS viewers_n, toUInt64(sum(cart_adds)) AS carts_n, toUInt64(sum(purchases)) AS buys_n
FROM ${V}product_daily
WHERE ${SCOPE} AND ${D_CUR}
GROUP BY product_id
HAVING viewers_n >= ${K_ANON}
ORDER BY views_n DESC
LIMIT 20`),
  vis_search_top: q(`SELECT query, toUInt64(sum(searches)) AS searches_n, toUInt64(sum(no_result)) AS no_result_n, toUInt64(sum(subjects)) AS subjects_n
FROM ${V}search_daily
WHERE ${SCOPE} AND ${D_CUR}
GROUP BY query
HAVING subjects_n >= ${K_ANON}
ORDER BY searches_n DESC
LIMIT 20`),
  vis_search_none: q(`SELECT query, toUInt64(sum(no_result)) AS no_result_n, toUInt64(sum(searches)) AS searches_n, toUInt64(sum(subjects)) AS subjects_n
FROM ${V}search_daily
WHERE ${SCOPE} AND ${D_CUR}
GROUP BY query
HAVING subjects_n >= ${K_ANON} AND no_result_n > 0
ORDER BY no_result_n DESC
LIMIT 20`),
} satisfies Record<string, VisQueryDef>;

export type VisQueryName = keyof typeof VIS_QUERIES;
