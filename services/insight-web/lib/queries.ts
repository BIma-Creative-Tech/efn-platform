/**
 * KATALOG QUERY insight-web (docs/04 §26.8; SQL & nama view = docs/design/insight-web-queries.md, yang menang
 * bila berbeda dari §26). Semua SQL terdefinisi di sini sebagai konstanta; TIDAK ada SQL/identifier dari pengguna.
 * Nilai filter hanya lewat placeholder bertipe ClickHouse `{nama:Tipe}` + parameter HTTP `param_nama`.
 * Fragmen filter dirakit dari konstanta di bawah (bukan dari input). `LIMIT` ditulis literal.
 *
 * Query bertanda "(tambahan)" di luar daftar kanonik §26.8 dicatat di README (kebutuhan komponen UI).
 * Setiap query tetap memfilter `has({pt:Array(String)}, company_code)` (lapis 1) di samping row policy (lapis 2).
 *
 * STATUS: ke-36 query dijalankan terhadap ClickHouse 24.8 oleh lead (G42, 18 Nov 2026); uji row policy per PT lulus.
 */
import 'server-only';
import { plKeySql } from './pnl';

export type ParamName =
  | 'from'
  | 'to'
  | 'prev_from'
  | 'prev_to'
  | 'pt'
  | 'branch'
  | 'cat'
  | 'product'
  | 'menus'
  | 'sort'
  | 'product_key'
  | 'weekly'
  | 'by_branch'
  // G52 (§28.15.9)
  | 'yoy_from'
  | 'yoy_to'
  | 'm_from'
  | 'ref_day'
  // G68 (§32.9): akhir rentang bulan P&L (tanggal 1), kelas pemakaian item jurnal, pemasok fill rate (`brand:branch_id`)
  | 'm_to'
  | 'uclass'
  | 'supplier'
  // Forecast F&B
  | 'today'
  | 'until'
  | 'dc_codes';

export type ChType = 'Date' | 'Array(String)' | 'String' | 'UInt8';

export const PARAM_TYPES: Record<ParamName, ChType> = {
  from: 'Date',
  to: 'Date',
  prev_from: 'Date',
  prev_to: 'Date',
  pt: 'Array(String)',
  branch: 'Array(String)',
  cat: 'Array(String)',
  product: 'Array(String)',
  menus: 'Array(String)',
  sort: 'String',
  product_key: 'String',
  weekly: 'UInt8',
  by_branch: 'UInt8',
  yoy_from: 'Date',
  yoy_to: 'Date',
  m_from: 'Date',
  ref_day: 'Date',
  m_to: 'Date',
  uclass: 'Array(String)',
  supplier: 'Array(String)',
  today: 'Date',
  until: 'Date',
  dc_codes: 'Array(String)',
};

export interface QueryDef {
  sql: string;
  params: readonly ParamName[];
}

// --- fragmen filter standar ---
const PT = 'has({pt:Array(String)}, company_code)';
const BR = "(empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))";
const PR = "(empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)))";
const CAT = '(empty({cat:Array(String)}) OR has({cat:Array(String)}, menu_category))';
const DAY_RANGE = 'day BETWEEN {from:Date} AND {to:Date}';
const DAY_RANGE_PREV = 'day BETWEEN {prev_from:Date} AND {to:Date}';
const SALES_RANGE = 'sales_date BETWEEN {from:Date} AND {to:Date}';
const SALES_RANGE_PREV = 'sales_date BETWEEN {prev_from:Date} AND {to:Date}';

const SUPPLY = 'in_ck + in_other + store_prod + st_in - st_out';

// G52 (§28.15.9): periode sekarang vs tahun lalu (sejajar kalender), dan hari acuan halaman Toko.
const CUR = 'sales_date BETWEEN {from:Date} AND {to:Date}';
const YOY = 'sales_date BETWEEN {yoy_from:Date} AND {yoy_to:Date}';
const CUR_OR_YOY = `(${CUR} OR ${YOY})`;
/** `gm` = net - cogs; persen terhadap net (net <= 0 -> NULL). Hanya dipakai di query Eksekutif (view kelas PT, memuat cogs). */
const GM_PCT = (cond: string): string =>
  `if(sumIf(net, ${cond}) <= 0, NULL, toFloat64(sumIf(net - cogs, ${cond})) / toFloat64(sumIf(net, ${cond})) * 100)`;
const REF_WEEK_AGO = 'subtractDays({ref_day:Date}, 7)';

// Cabang yang benar-benar menjual atau memproduksi (toko atau CK). HEAD OFFICE, gudang, KACS Damage/Expired, Temporary Variance,
// dst. (`is_store = 0 AND is_ck = 0`) tidak menjual: dikeluarkan dari opsi filter Cabang dan dari peringkat/sell-through/outlier
// per cabang (keputusan pemilik proyek 21 Nov 2026). Subquery membaca view yang sama yang terkena row policy PT.
const SALES_BRANCH_SET = 'SELECT brand, branch_id FROM efn_insight.v_esb_branch_list WHERE is_store = 1 OR is_ck = 1';
const SALES_BRANCH = `((brand, branch_id) IN (${SALES_BRANCH_SET}))`;
/** Pembayaran: branch_id 0 = branchCode tak dikenal di master cabang; tetap ditampilkan sebagai "Tidak diketahui". */
const SALES_BRANCH_OR_UNKNOWN = `(branch_id = 0 OR ${SALES_BRANCH})`;
/** Pergerakan: cabang yang dipilih cocok dengan asal ATAU tujuan kiriman. */
const MBR =
  "(empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(origin_branch_id))) OR has({branch:Array(String)}, concat(brand, ':', toString(dest_branch_id))))";

// YoY sebanding (/yoy, /fnb/exec): periode pembanding yoy_from/yoy_to = 364 hari sebelumnya (diisi server, bukan input pengguna).
// Pasangan cabang-hari yang slice penjualannya selesai di KEDUA sisi; baris tahun lalu dipetakan ke hari padanannya (+364).
const YOY_DONE_PAIRS = `SELECT brand, branch_id, day FROM efn_insight.v_esb_sales_done_day
      WHERE ${PT} AND day BETWEEN {from:Date} AND {to:Date}
        AND (brand, branch_id, day - 364) IN (SELECT brand, branch_id, day FROM efn_insight.v_esb_sales_done_day WHERE ${PT} AND day BETWEEN {yoy_from:Date} AND {yoy_to:Date})`;
const YOY_PAIR = `((brand, branch_id, if(sales_date >= {from:Date}, sales_date, sales_date + 364)) IN (${YOY_DONE_PAIRS}))`;

// ---------- G68 (docs/04 §32.6/§32.10): Laba Rugi Outlet, Waste & Pemakaian Internal, Fill Rate Outlet -> CK ----------
// P&L: baris `summary` ESB diberi kunci tetap lewat nama pos (dinormalkan: huruf kecil, hanya a-z0-9). Daftar sinonim = konstanta
// (nama pos summary ESB belum terverifikasi [B], §32.13); pos yang tak dikenal tetap tampil di pnl_statement dengan nama aslinya.
// Daftar sinonim satu sumber di lib/pnl.ts (PL_SYNONYMS), dipakai juga TS untuk urutan/label.
const PL_KEY = plKeySql();
const PL_CUR = 'period_month BETWEEN {m_from:Date} AND {m_to:Date}';
const PL_LY = 'period_month BETWEEN {yoy_from:Date} AND {yoy_to:Date}';
/** Jumlah `amount` pos ringkasan berkunci `key` (opsional dibatasi `cond`). */
const PL_SUM = (key: string, cond = '1'): string => `toFloat64(sumIf(amount, ${PL_KEY} = '${key}' AND (${cond})))`;
const PL_SUMMARY_BRANCH = "line_type = 'summary' AND scope = 'branch'";

const U_RANGE = 'journal_date BETWEEN {from:Date} AND {to:Date}';
const U_RANGE_PREV = 'journal_date BETWEEN {prev_from:Date} AND {to:Date}';
const U_CLASS = 'has({uclass:Array(String)}, usage_class)';

const F_RANGE = 'transfer_date BETWEEN {from:Date} AND {to:Date}';
const F_RANGE_PREV = 'transfer_date BETWEEN {prev_from:Date} AND {to:Date}';
const F_CUR = 'transfer_date >= {from:Date}';
const F_PREV = 'transfer_date <= {prev_to:Date}';
const F_SUPPLIER = "(empty({supplier:Array(String)}) OR has({supplier:Array(String)}, concat(brand, ':', toString(supplier_branch_id))))";
/** Persen dari rasio dua kolom aditif (NULL bila penyebut 0). */
const F_PCT = (num: string, den: string, cond: string): string =>
  `if(sumIf(${den}, ${cond}) = 0, NULL, toFloat64(sumIf(${num}, ${cond})) / toFloat64(sumIf(${den}, ${cond})) * 100)`;
const F_AVG_LEAD = (cond: string): string =>
  `if(sumIf(lead_days_n, ${cond}) = 0, NULL, toFloat64(sumIf(lead_days_sum, ${cond})) / toFloat64(sumIf(lead_days_n, ${cond})))`;

export const QUERIES = {
  // ---------- filter global ----------
  list_branches: {
    params: ['pt'],
    sql: `SELECT brand, company_code, branch_id, branch_name, is_ck, is_store, active
FROM efn_insight.v_esb_branch_list
WHERE ${PT} AND (is_store = 1 OR is_ck = 1)
ORDER BY brand, branch_name
LIMIT 500`,
  },
  product_categories: {
    // (tambahan, review 21 Nov 2026) kategori produk ESB untuk kolom Kategori di tabel per produk (tanpa filter FIN/aktif).
    params: ['pt'],
    // Alias sengaja beda dari nama kolom (any(category) AS category memicu alias siklik di ClickHouse).
    sql: `SELECT brand, product_code, any(category) AS category_label
FROM efn_insight.v_esb_product_list
WHERE ${PT}
GROUP BY brand, product_code
LIMIT 5000`,
  },
  esb_coverage: {
    // (tambahan, review 21 Nov 2026) cakupan data per entitas (rentang hari yang ada), untuk banner "data tersedia ..".
    params: ['pt'],
    sql: `SELECT brand, company_code, entity, first_day, last_day, days
FROM efn_insight.v_esb_coverage
WHERE ${PT}
ORDER BY brand, company_code, entity
LIMIT 500`,
  },
  list_categories: {
    params: ['from', 'to', 'pt'],
    sql: `SELECT DISTINCT menu_category
FROM efn_insight.v_esb_sales_daily
WHERE ${SALES_RANGE} AND ${PT}
ORDER BY menu_category
LIMIT 500`,
  },
  list_products: {
    params: ['pt'],
    sql: `SELECT brand, product_code, any(product_name) AS product_label, any(category) AS category_label
FROM efn_insight.v_esb_product_list
WHERE ${PT} AND is_fin = 1 AND active = 1
GROUP BY brand, product_code
ORDER BY brand, product_label
LIMIT 500`,
  },

  // ---------- Ringkasan (/) ----------
  exec_kpi: {
    params: ['from', 'to', 'prev_from', 'prev_to', 'pt', 'branch'],
    sql: `SELECT
    toFloat64(sumIf(net, sales_date >= {from:Date}))      AS net_cur,
    toFloat64(sumIf(net, sales_date <= {prev_to:Date}))   AS net_prev,
    sumIf(trx_count, sales_date >= {from:Date})           AS trx_cur,
    sumIf(trx_count, sales_date <= {prev_to:Date})        AS trx_prev,
    toFloat64(sumIf(cogs, sales_date >= {from:Date}))     AS cogs_cur,
    toFloat64(sumIf(cogs, sales_date <= {prev_to:Date}))  AS cogs_prev
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${SALES_RANGE_PREV}
  AND ${PT}
  AND ${BR}`,
  },
  exec_trend_daily: {
    // Satu rentang gabungan periode lalu + sekarang (prev_from..to); pemisahan periode dilakukan di loader.
    params: ['prev_from', 'to', 'pt', 'branch'],
    sql: `SELECT sales_date, brand,
       toFloat64(sum(net)) AS net, sum(trx_count) AS trx
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${SALES_RANGE_PREV}
  AND ${PT}
  AND ${BR}
GROUP BY sales_date, brand
ORDER BY sales_date, brand
LIMIT 5000`,
  },
  exec_branch_rank: {
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT brand, company_code, branch_id, any(branch_name) AS branch_label,
       toFloat64(sum(net)) AS net_sales, sum(trx_count) AS trx,
       if(sum(trx_count) = 0, NULL, toFloat64(sum(net)) / sum(trx_count)) AS avg_trx,
       if(sum(net) <= 0, NULL, toFloat64(sum(cogs)) / toFloat64(sum(net)) * 100) AS hpp_pct
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${SALES_BRANCH}
GROUP BY brand, company_code, branch_id
ORDER BY net_sales DESC
LIMIT 500`,
  },
  exec_pt_share: {
    // (tambahan)
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT company_code, toFloat64(sum(net)) AS net
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
GROUP BY company_code
ORDER BY net DESC
LIMIT 50`,
  },
  exec_sell_through: {
    // (tambahan) KPI tile ke-5 (definisi Q-INS1)
    params: ['from', 'to', 'pt', 'branch', 'product'],
    sql: `SELECT toFloat64(sum(sold)) AS sold,
       toFloat64(sum(${SUPPLY})) AS supply
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${PR}`,
  },
  exec_attention: {
    // (tambahan)
    params: ['pt'],
    sql: `SELECT check_name, brand, item, any(value) AS value, max(last_day) AS last_day
FROM efn_insight.v_esb_data_quality
WHERE check_name IN ('store_no_sales_h1', 'last_run_not_ok') AND ${PT}
GROUP BY check_name, brand, item
ORDER BY check_name, brand, item
LIMIT 50`,
  },

  // ---------- Produksi vs penjualan (/flow) ----------
  flow_summary: {
    params: ['from', 'to', 'prev_from', 'prev_to', 'pt', 'branch', 'product'],
    sql: `SELECT
    toFloat64(sumIf(ck_prod, day >= {from:Date}))  AS ck_prod_cur,  toFloat64(sumIf(ck_prod, day <= {prev_to:Date}))  AS ck_prod_prev,
    toFloat64(sumIf(ck_out, day >= {from:Date}))   AS ck_out_cur,   toFloat64(sumIf(ck_out, day <= {prev_to:Date}))   AS ck_out_prev,
    toFloat64(sumIf(in_ck + in_other, day >= {from:Date}))  AS received_cur,
    toFloat64(sumIf(in_ck + in_other, day <= {prev_to:Date})) AS received_prev,
    toFloat64(sumIf(store_prod, day >= {from:Date})) AS store_prod_cur,
    toFloat64(sumIf(st_in, day >= {from:Date}))      AS st_in_cur,
    toFloat64(sumIf(st_out, day >= {from:Date}))     AS st_out_cur,
    toFloat64(sumIf(sold, day >= {from:Date}))     AS sold_cur,     toFloat64(sumIf(sold, day <= {prev_to:Date}))     AS sold_prev,
    toFloat64(sumIf(${SUPPLY}, day >= {from:Date}))  AS supply_cur,
    toFloat64(sumIf(${SUPPLY}, day <= {prev_to:Date})) AS supply_prev
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE_PREV}
  AND ${PT}
  AND ${BR}
  AND ${PR}`,
  },
  flow_by_branch: {
    params: ['from', 'to', 'pt', 'branch', 'product'],
    sql: `SELECT brand, company_code, branch_id, any(branch_name) AS branch_label,
       toFloat64(sum(${SUPPLY})) AS supply,
       toFloat64(sum(sold)) AS sold_qty,
       if(sum(${SUPPLY}) <= 0, NULL,
          sum(sold) / sum(${SUPPLY}) * 100) AS sell_through_pct
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${PR}
  AND ${SALES_BRANCH}
GROUP BY brand, company_code, branch_id
HAVING supply != 0 OR sold_qty != 0
ORDER BY sell_through_pct ASC NULLS LAST
LIMIT 500`,
  },
  flow_daily_trend: {
    // (tambahan)
    params: ['from', 'to', 'pt', 'branch', 'product', 'weekly'],
    sql: `SELECT if({weekly:UInt8} = 1, toMonday(day), day) AS period,
       toFloat64(sum(ck_prod)) AS ck_prod, toFloat64(sum(store_prod)) AS store_prod,
       toFloat64(sum(ck_out)) AS ck_out, toFloat64(sum(sold)) AS sold
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${PR}
GROUP BY period
ORDER BY period
LIMIT 1000`,
  },
  flow_by_product: {
    params: ['from', 'to', 'pt', 'branch', 'product'],
    sql: `SELECT brand, product_code, any(product_name) AS product_label, startsWith(product_code, 'NAME:') AS unmapped,
       toFloat64(sum(ck_prod)) AS ck_prod_qty, toFloat64(sum(ck_out)) AS ck_out_qty,
       toFloat64(sum(in_ck + in_other)) AS received, toFloat64(sum(store_prod)) AS store_prod_qty,
       toFloat64(sum(sold)) AS sold_qty,
       toFloat64(sum(${SUPPLY})) AS supply, toFloat64(sum(st_out)) AS st_out_qty,
       if(sum(${SUPPLY}) <= 0, NULL,
          sum(sold) / sum(${SUPPLY}) * 100) AS sell_through_pct
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${PR}
GROUP BY brand, product_code
ORDER BY sold_qty DESC
LIMIT 500`,
  },
  flow_outliers: {
    params: ['from', 'to', 'pt', 'branch', 'product'],
    sql: `SELECT brand, company_code, branch_id, any(branch_name) AS branch_label, product_code,
       any(product_name) AS product_label, startsWith(product_code, 'NAME:') AS unmapped,
       toFloat64(sum(in_ck + in_other + st_in - st_out)) AS received,
       toFloat64(sum(store_prod)) AS store_prod_qty,
       toFloat64(sum(sold)) AS sold_qty,
       toFloat64(sum(${SUPPLY})) AS supply, toFloat64(sum(st_out)) AS st_out_qty,
       if(sum(${SUPPLY}) <= 0, NULL,
          sum(sold) / sum(${SUPPLY}) * 100) AS sell_through_pct
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${PR}
  AND ${SALES_BRANCH}
GROUP BY brand, company_code, branch_id, product_code
HAVING received != 0 OR sold_qty != 0 OR store_prod_qty != 0
ORDER BY sell_through_pct ASC NULLS LAST, sold_qty DESC
LIMIT 500`,
  },
  flow_product_daily: {
    // (tambahan)
    params: ['from', 'to', 'pt', 'branch', 'product_key'],
    sql: `SELECT day, toFloat64(sum(ck_prod)) AS ck_prod, toFloat64(sum(ck_out)) AS ck_out,
       toFloat64(sum(in_ck + in_other)) AS received, toFloat64(sum(sold)) AS sold
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND ${BR}
  AND concat(brand, ':', product_code) = {product_key:String}
GROUP BY day
ORDER BY day
LIMIT 1000`,
  },

  // ---------- Penjualan & menu (/sales) ----------
  sales_kpi: {
    // (tambahan)
    params: ['from', 'to', 'prev_from', 'prev_to', 'pt', 'branch', 'cat'],
    sql: `SELECT
    toFloat64(sumIf(net, sales_date >= {from:Date}))       AS net_cur,
    toFloat64(sumIf(net, sales_date <= {prev_to:Date}))    AS net_prev,
    toFloat64(sumIf(gross, sales_date >= {from:Date}))     AS gross_cur,
    toFloat64(sumIf(discount, sales_date >= {from:Date}))  AS discount_cur,
    toFloat64(sumIf(discount, sales_date <= {prev_to:Date})) AS discount_prev,
    toFloat64(sumIf(gross, sales_date <= {prev_to:Date}))  AS gross_prev,
    toFloat64(sumIf(qty, sales_date >= {from:Date}))       AS qty_cur,
    toFloat64(sumIf(qty, sales_date <= {prev_to:Date}))    AS qty_prev,
    toFloat64(sumIf(cogs, sales_date >= {from:Date}))      AS cogs_cur,
    toFloat64(sumIf(cogs, sales_date <= {prev_to:Date}))   AS cogs_prev
FROM efn_insight.v_esb_sales_daily
WHERE ${SALES_RANGE_PREV}
  AND ${PT}
  AND ${BR}
  AND ${CAT}`,
  },
  sales_by_weekday: {
    params: ['from', 'to', 'pt', 'branch', 'cat'],
    sql: `SELECT toDayOfWeek(sales_date) AS dow,
       toFloat64(sum(net)) / uniqExact(sales_date) AS avg_net,
       uniqExact(sales_date) AS days
FROM efn_insight.v_esb_sales_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${CAT}
GROUP BY dow
ORDER BY dow
LIMIT 7`,
  },
  sales_weekday_heatmap: {
    // (tambahan)
    params: ['from', 'to', 'pt', 'branch', 'cat'],
    sql: `SELECT toMonday(sales_date) AS week, toDayOfWeek(sales_date) AS dow, toFloat64(sum(net)) AS net
FROM efn_insight.v_esb_sales_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${CAT}
GROUP BY week, dow
ORDER BY week, dow
LIMIT 1000`,
  },
  sales_by_category: {
    params: ['from', 'to', 'pt', 'branch', 'cat'],
    sql: `SELECT if(menu_category = '', 'Tanpa kategori', menu_category) AS category,
       toFloat64(sum(net)) AS net_sales, toFloat64(sum(qty)) AS qty_sold,
       if(sum(net) = 0, NULL, toFloat64(sum(net) - sum(cogs)) / toFloat64(sum(net)) * 100) AS margin_pct
FROM efn_insight.v_esb_sales_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${CAT}
GROUP BY category
ORDER BY net_sales DESC
LIMIT 500`,
  },
  sales_channel_split: {
    // Alias sengaja beda dari nama kolom (branch_key/branch_label): alias = nama kolom memicu alias siklik di ClickHouse.
    params: ['from', 'to', 'pt', 'branch', 'cat', 'by_branch'],
    sql: `SELECT brand,
       if({by_branch:UInt8} = 1, branch_id, toUInt32(0)) AS branch_key,
       if({by_branch:UInt8} = 1, any(branch_name), '') AS branch_label,
       toFloat64(sumIf(net, is_takeaway = 1)) AS net_takeaway,
       toFloat64(sumIf(net, is_takeaway = 0)) AS net_dine_in
FROM efn_insight.v_esb_sales_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${CAT}
GROUP BY brand, branch_key
ORDER BY brand, branch_key
LIMIT 500`,
  },
  sales_top_menu: {
    params: ['from', 'to', 'pt', 'branch', 'cat', 'sort'],
    sql: `SELECT brand, menu_code, any(menu_name) AS menu_label, any(menu_category) AS category,
       toFloat64(sum(net)) AS net_sales, toFloat64(sum(qty)) AS qty_sold,
       if(sum(net) = 0, NULL, toFloat64(sum(net) - sum(cogs)) / toFloat64(sum(net)) * 100) AS margin_pct
FROM efn_insight.v_esb_sales_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${CAT}
GROUP BY brand, menu_code
ORDER BY multiIf({sort:String} = 'qty', qty_sold, {sort:String} = 'margin', ifNull(margin_pct, -1e18), net_sales) DESC
LIMIT 500`,
  },
  menu_mix_by_branch: {
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT brand, branch_id, any(branch_name) AS branch_label, menu_category,
       sum(net) AS net_sales,
       if(sum(sum(net)) OVER (PARTITION BY brand, branch_id) = 0, NULL,
          round(sum(net) / sum(sum(net)) OVER (PARTITION BY brand, branch_id) * 100, 2)) AS share_pct
FROM efn_insight.v_esb_menu_mix
WHERE month BETWEEN toStartOfMonth({from:Date}) AND toStartOfMonth({to:Date})
  AND ${PT}
  AND ${BR}
GROUP BY brand, branch_id, menu_category
ORDER BY brand, branch_label, share_pct DESC
LIMIT 5000`,
  },

  // ---------- Pembayaran (/payments) ----------
  pay_kpi: {
    // (tambahan)
    params: ['from', 'to', 'prev_from', 'prev_to', 'pt', 'branch'],
    sql: `SELECT
    toFloat64(sumIf(amount, sales_date >= {from:Date}))     AS amount_cur,
    toFloat64(sumIf(amount, sales_date <= {prev_to:Date}))  AS amount_prev,
    sumIf(payment_count, sales_date >= {from:Date})         AS count_cur,
    sumIf(payment_count, sales_date <= {prev_to:Date})      AS count_prev,
    toFloat64(sumIf(mdr, sales_date >= {from:Date}))        AS mdr_cur,
    toFloat64(sumIf(mdr, sales_date <= {prev_to:Date}))     AS mdr_prev,
    arraySort(groupUniqArrayIf(toString(sales_date), source = 'oms' AND sales_date >= {from:Date})) AS oms_days
FROM efn_insight.v_esb_payment_daily
WHERE ${SALES_RANGE_PREV}
  AND ${PT}
  AND ${BR}`,
  },
  pay_by_method: {
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT payment_type, if(payment_method = '', 'Tidak diketahui', payment_method) AS method,
       sum(payment_count) AS cnt, toFloat64(sum(amount)) AS amount_total, toFloat64(sum(mdr)) AS mdr_total,
       if(sum(amount) = 0, NULL, toFloat64(sum(mdr)) / toFloat64(sum(amount)) * 100) AS mdr_pct,
       if(sum(payment_count) = 0, NULL, toFloat64(sum(amount)) / sum(payment_count)) AS avg_amount
FROM efn_insight.v_esb_payment_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
GROUP BY payment_type, method
ORDER BY amount_total DESC
LIMIT 500`,
  },
  pay_trend: {
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT sales_date, payment_type, toFloat64(sum(amount)) AS amount, sum(payment_count) AS payment_count
FROM efn_insight.v_esb_payment_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
GROUP BY sales_date, payment_type
ORDER BY sales_date, payment_type
LIMIT 5000`,
  },
  pay_by_branch: {
    // (tambahan) branch_id 0 = branchCode pembayaran tak dikenal di master cabang.
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT brand, branch_id, any(b_name) AS branch_name, sum(amt) AS amount, sum(m) AS mdr,
       argMax(method, amt) AS top_method
FROM
(
    SELECT brand, branch_id, any(branch_name) AS b_name,
           if(payment_method = '', 'Tidak diketahui', payment_method) AS method,
           toFloat64(sum(amount)) AS amt, toFloat64(sum(mdr)) AS m
    FROM efn_insight.v_esb_payment_daily
    WHERE ${SALES_RANGE}
      AND ${PT}
      AND ${BR}
      AND ${SALES_BRANCH_OR_UNKNOWN}
    GROUP BY brand, branch_id, method
)
GROUP BY brand, branch_id
ORDER BY amount DESC
LIMIT 500`,
  },

  // ---------- Distribusi (/distribution) ----------
  dist_kpi: {
    // (tambahan)
    params: ['from', 'to', 'prev_from', 'prev_to', 'pt', 'branch', 'product'],
    sql: `SELECT
    toFloat64(sumIf(ck_out, day >= {from:Date}))    AS ck_out_cur,   toFloat64(sumIf(ck_out, day <= {prev_to:Date}))   AS ck_out_prev,
    toFloat64(sumIf(in_ck, day >= {from:Date}))     AS in_ck_cur,    toFloat64(sumIf(in_ck, day <= {prev_to:Date}))    AS in_ck_prev,
    toFloat64(sumIf(st_out, day >= {from:Date}))    AS st_out_cur,   toFloat64(sumIf(st_out, day <= {prev_to:Date}))   AS st_out_prev,
    toFloat64(sumIf(in_other, day >= {from:Date}))  AS in_other_cur, toFloat64(sumIf(in_other, day <= {prev_to:Date})) AS in_other_prev
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE_PREV}
  AND ${PT}
  AND ${BR}
  AND ${PR}`,
  },
  dist_ck_delivery: {
    params: ['from', 'to', 'pt', 'branch', 'product'],
    sql: `SELECT day, toFloat64(sum(ck_out)) AS ck_out_qty, toFloat64(sum(in_ck)) AS in_ck_qty,
       toFloat64(sum(in_other)) AS in_other_qty
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${PR}
GROUP BY day
ORDER BY day
LIMIT 1000`,
  },
  dist_transfer: {
    // Cabang non-penjualan (HO/gudang/damage; bukan toko dan bukan CK) digabung menjadi satu baris per PT dengan branch_id 0
    // dan label "Non-toko (HO/gudang/damage)": transfer ke/dari cabang itu bukan perpindahan antar toko.
    params: ['from', 'to', 'pt', 'branch', 'product'],
    sql: `SELECT brand, company_code, branch_key AS branch_id,
       if(branch_key = 0, 'Non-toko (HO/gudang/damage)', any(b_name)) AS branch_label,
       toFloat64(sum(ck_out)) AS ck_out_qty, toFloat64(sum(in_ck)) AS in_ck_qty, toFloat64(sum(in_other)) AS in_other_qty,
       toFloat64(sum(st_in)) AS st_in_qty, toFloat64(sum(st_out)) AS st_out_qty,
       toFloat64(sum(st_in) - sum(st_out)) AS st_net
FROM
(
    SELECT brand, company_code, branch_name AS b_name,
           if(${SALES_BRANCH}, branch_id, toUInt32(0)) AS branch_key,
           ck_out, in_ck, in_other, st_in, st_out
    FROM efn_insight.v_esb_flow_daily
    WHERE ${DAY_RANGE}
      AND ${PT}
      AND ${BR}
      AND ${PR}
)
GROUP BY brand, company_code, branch_key
HAVING ck_out_qty != 0 OR in_ck_qty != 0 OR in_other_qty != 0 OR st_in_qty != 0 OR st_out_qty != 0
ORDER BY brand, branch_key = 0, branch_label
LIMIT 500`,
  },
  dist_transfer_top_products: {
    // (tambahan)
    params: ['from', 'to', 'pt', 'branch', 'product'],
    sql: `SELECT brand, product_code, any(product_name) AS product_label,
       toFloat64(sum(st_in)) AS st_in_qty, toFloat64(sum(st_out)) AS st_out_qty, toFloat64(sum(st_in + st_out)) AS moved
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${PR}
GROUP BY brand, product_code
HAVING moved > 0
ORDER BY moved DESC
LIMIT 10`,
  },
  dist_ck_gap_by_product: {
    // (tambahan) pengganti "tabel selisih cabang x produk": ck_out di cabang CK, in_ck di toko -> selisih bermakna per produk-hari.
    params: ['from', 'to', 'pt', 'product'],
    sql: `SELECT day, brand, product_code, any(product_name) AS product_label,
       toFloat64(sum(ck_out)) AS ck_out_qty, toFloat64(sum(in_ck)) AS in_ck_qty, toFloat64(sum(ck_out) - sum(in_ck)) AS gap,
       toFloat64(sum(ck_prod)) AS ck_prod_qty
FROM efn_insight.v_esb_flow_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND ${PR}
GROUP BY day, brand, product_code
HAVING abs(gap) > 0.0005
ORDER BY abs(gap) DESC
LIMIT 500`,
  },

  // ---------- Pergerakan barang semua kelompok (review 21 Nov 2026; v_esb_movement_daily) ----------
  // v_esb_flow_daily hanya memuat FIN. Data nyata PB (lead, 21 Nov 2026): CK mengirim FIN + WIP; bahan baku, kemasan, supplies
  // dikirim GUDANG (WHProd, Warehouse Palem = origin_kind 'non_sales') dan sebagian toko (KACS). Karena itu semua asal ditampilkan
  // dengan kolom asal/tujuan, bukan hanya CK. qty TIDAK boleh dijumlah lintas uom: selalu dikelompokkan per uom.
  // company_code view ini = PT cabang ASAL.
  mov_by_group: {
    params: ['from', 'to', 'pt', 'branch', 'product'],
    sql: `SELECT origin_kind, dest_kind, item_group, uom, toFloat64(sum(qty)) AS qty_sum, sum(lines) AS lines_n,
       uniqExact(brand, product_code) AS skus
FROM efn_insight.v_esb_movement_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND kind = 'delivery'
  AND ${MBR}
  AND ${PR}
GROUP BY origin_kind, dest_kind, item_group, uom
ORDER BY origin_kind, dest_kind, item_group, uom
LIMIT 500`,
  },
  mov_by_category: {
    params: ['from', 'to', 'pt', 'branch', 'product'],
    sql: `SELECT origin_kind, item_group, category, uom, toFloat64(sum(qty)) AS qty_sum, sum(lines) AS lines_n,
       uniqExact(brand, product_code) AS skus
FROM efn_insight.v_esb_movement_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND kind = 'delivery'
  AND ${MBR}
  AND ${PR}
GROUP BY origin_kind, item_group, category, uom
ORDER BY origin_kind, item_group, qty_sum DESC
LIMIT 1000`,
  },
  mov_ck_product: {
    // Produksi vs kiriman FIN per CK asal x produk: dasar kolom Keterangan "kirim melebihi produksi". Filter cabang sengaja
    // tidak dipakai (perbandingan di tingkat CK); filter produk dipakai.
    params: ['from', 'to', 'pt', 'product'],
    sql: `SELECT brand, product_code, origin_branch_id, any(origin_branch_name) AS ck_label,
       toFloat64(sumIf(qty, kind = 'production')) AS prod_qty,
       toFloat64(sumIf(qty, kind = 'delivery')) AS out_qty
FROM efn_insight.v_esb_movement_daily
WHERE ${DAY_RANGE}
  AND ${PT}
  AND origin_kind = 'ck' AND item_group = 'fin' AND uom = 'EA'
  AND ${PR}
GROUP BY brand, product_code, origin_branch_id
HAVING prod_qty != 0 OR out_qty != 0
ORDER BY brand, product_code, origin_branch_id
LIMIT 5000`,
  },

  // ---------- Kualitas data (/data-quality) ----------
  // dq_status_board (tambahan di katalog data-engineer) TIDAK dipakai: papan status dihitung dari jumlah baris
  // tiap tabel temuan di bawah (satu sumber kebenaran).
  dq_freshness: {
    params: ['pt'],
    sql: `SELECT brand, item AS entity, min(value) AS age_hours, max(last_day) AS last_day
FROM efn_insight.v_esb_data_quality
WHERE check_name = 'data_age_hours' AND ${PT}
GROUP BY brand, entity
ORDER BY brand, entity
LIMIT 500`,
  },
  dq_last_run: {
    params: ['pt'],
    sql: `SELECT check_name, brand, groupUniqArray(company_code) AS company_codes, item,
       max(value) AS value_max, max(last_day) AS last_seen
FROM efn_insight.v_esb_data_quality
WHERE ${PT} AND check_name = 'last_run_not_ok'
GROUP BY check_name, brand, item
ORDER BY value_max DESC
LIMIT 500`,
  },
  dq_alias_unmapped: {
    params: ['pt'],
    sql: `SELECT check_name, brand, groupUniqArray(company_code) AS company_codes, item,
       max(value) AS value_max, max(last_day) AS last_seen
FROM efn_insight.v_esb_data_quality
WHERE ${PT} AND check_name = 'alias_unmapped'
GROUP BY check_name, brand, item
ORDER BY value_max DESC
LIMIT 500`,
  },
  dq_non_ea: {
    params: ['pt'],
    sql: `SELECT check_name, brand, groupUniqArray(company_code) AS company_codes, item,
       max(value) AS value_max, max(last_day) AS last_seen
FROM efn_insight.v_esb_data_quality
WHERE ${PT} AND check_name = 'non_ea_lines'
GROUP BY check_name, brand, item
ORDER BY value_max DESC
LIMIT 500`,
  },
  dq_store_no_sales: {
    params: ['pt'],
    sql: `SELECT check_name, brand, groupUniqArray(company_code) AS company_codes, item,
       max(value) AS value_max, max(last_day) AS last_seen
FROM efn_insight.v_esb_data_quality
WHERE ${PT} AND check_name = 'store_no_sales_h1'
GROUP BY check_name, brand, item
ORDER BY value_max DESC
LIMIT 500`,
  },

  // ---------- G52: Eksekutif F&B (/fnb/exec; docs/04 §28.15.9; view kelas PT, memuat cogs/mdr) ----------
  // Nama kolom keluaran: alias yang sama dengan kolom view memicu alias siklik di ClickHouse (lihat catatan di bawah),
  // jadi `net`, `cogs`, `amount`, `mdr`, `payment_method` menjadi `net_sum`, `cogs_sum`, `amount_sum`, `mdr_sum`, `method`.
  fexec_kpi: {
    params: ['from', 'to', 'yoy_from', 'yoy_to', 'pt', 'branch'],
    sql: `SELECT
    toFloat64(sumIf(net, ${CUR}))          AS net_cur,
    toFloat64(sumIf(cogs, ${CUR}))         AS cogs_cur,
    toFloat64(sumIf(net - cogs, ${CUR}))   AS gm_cur,
    sumIf(trx_count, ${CUR})               AS trx_cur,
    toFloat64(sumIf(net, ${CUR} AND ${YOY_PAIR}))        AS net_cmp,
    toFloat64(sumIf(cogs, ${CUR} AND ${YOY_PAIR}))       AS cogs_cmp,
    toFloat64(sumIf(net - cogs, ${CUR} AND ${YOY_PAIR})) AS gm_cmp,
    sumIf(trx_count, ${CUR} AND ${YOY_PAIR})             AS trx_cmp,
    toFloat64(sumIf(net, ${YOY} AND ${YOY_PAIR}))        AS net_yoy,
    toFloat64(sumIf(cogs, ${YOY} AND ${YOY_PAIR}))       AS cogs_yoy,
    toFloat64(sumIf(net - cogs, ${YOY} AND ${YOY_PAIR})) AS gm_yoy,
    sumIf(trx_count, ${YOY} AND ${YOY_PAIR})             AS trx_yoy
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${CUR_OR_YOY}
  AND ${PT}
  AND ${BR}`,
  },
  fexec_margin_monthly: {
    // 13 bulan berakhir di bulan `to` (m_from = awal bulan ke-13 mundur).
    params: ['m_from', 'to', 'pt', 'branch'],
    sql: `SELECT toStartOfMonth(sales_date) AS month, company_code,
       toFloat64(sum(net)) AS net_sum, toFloat64(sum(cogs)) AS cogs_sum, toFloat64(sum(net) - sum(cogs)) AS gm
FROM efn_insight.v_esb_sales_branch_daily
WHERE sales_date BETWEEN {m_from:Date} AND {to:Date}
  AND ${PT}
  AND ${BR}
GROUP BY month, company_code
ORDER BY month, company_code
LIMIT 500`,
  },
  fexec_pt_compare: {
    params: ['from', 'to', 'yoy_from', 'yoy_to', 'pt', 'branch'],
    sql: `SELECT brand, company_code,
       toFloat64(sumIf(net, ${CUR}))        AS net_sum,
       toFloat64(sumIf(cogs, ${CUR}))       AS cogs_sum,
       toFloat64(sumIf(net - cogs, ${CUR})) AS gm,
       ${GM_PCT(CUR)}                       AS gm_pct,
       sumIf(trx_count, ${CUR})             AS trx,
       toFloat64(sumIf(net, ${CUR} AND ${YOY_PAIR})) AS net_cmp,
       toFloat64(sumIf(net, ${YOY} AND ${YOY_PAIR})) AS net_yoy
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${CUR_OR_YOY}
  AND ${PT}
  AND ${BR}
GROUP BY brand, company_code
ORDER BY gm DESC
LIMIT 50`,
  },
  fexec_mdr_cost: {
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT company_code, if(payment_method = '', 'Tidak diketahui', payment_method) AS method,
       toFloat64(sum(amount)) AS amount_sum, toFloat64(sum(mdr)) AS mdr_sum,
       if(sum(amount) <= 0, NULL, toFloat64(sum(mdr)) / toFloat64(sum(amount)) * 100) AS mdr_pct
FROM efn_insight.v_esb_payment_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
GROUP BY company_code, method
ORDER BY mdr_sum DESC
LIMIT 500`,
  },
  fexec_branch_rank: {
    params: ['from', 'to', 'yoy_from', 'yoy_to', 'pt', 'branch'],
    sql: `SELECT brand, company_code, branch_id, any(branch_name) AS branch_label,
       toFloat64(sumIf(net, ${CUR}))        AS net_sum,
       toFloat64(sumIf(net - cogs, ${CUR})) AS gm,
       ${GM_PCT(CUR)}                       AS gm_pct,
       toFloat64(sumIf(net, ${YOY} AND ${YOY_PAIR})) AS net_yoy,
       if(sumIf(net, ${YOY} AND ${YOY_PAIR}) <= 0, NULL,
          (toFloat64(sumIf(net, ${CUR} AND ${YOY_PAIR})) - toFloat64(sumIf(net, ${YOY} AND ${YOY_PAIR})))
          / toFloat64(sumIf(net, ${YOY} AND ${YOY_PAIR})) * 100) AS growth_pct
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${CUR_OR_YOY}
  AND ${PT}
  AND ${BR}
  AND ${SALES_BRANCH}
GROUP BY brand, company_code, branch_id
ORDER BY gm DESC
LIMIT 500`,
  },
  fexec_yoy_coverage: {
    // Hari pertama data penjualan per PT: pembanding tahun lalu hanya bermakna bila first_day <= yoy_from.
    params: ['pt'],
    sql: `SELECT company_code, first_day
FROM efn_insight.v_esb_coverage
WHERE ${PT} AND entity = 'sales'
ORDER BY company_code, first_day
LIMIT 50`,
  },

  // ---------- G52: Toko F&B (/fnb/store; view kelas BR; TANPA cogs/mdr/net pembayaran: L-244, Q-INS-F3) ----------
  fstore_kpi: {
    params: ['ref_day', 'pt', 'branch'],
    sql: `SELECT
    toFloat64(sumIf(net, sales_date = {ref_day:Date}))          AS net_day,
    toFloat64(sumIf(net, sales_date = ${REF_WEEK_AGO}))         AS net_day_lw,
    sumIf(trx_count, sales_date = {ref_day:Date})               AS trx_day,
    sumIf(trx_count, sales_date = ${REF_WEEK_AGO})              AS trx_day_lw,
    if(sumIf(trx_count, sales_date = {ref_day:Date}) = 0, NULL,
       toFloat64(sumIf(net, sales_date = {ref_day:Date})) / sumIf(trx_count, sales_date = {ref_day:Date})) AS avg_trx_day,
    if(sumIf(trx_count, sales_date = ${REF_WEEK_AGO}) = 0, NULL,
       toFloat64(sumIf(net, sales_date = ${REF_WEEK_AGO})) / sumIf(trx_count, sales_date = ${REF_WEEK_AGO})) AS avg_trx_lw
FROM efn_insight.v_esb_store_sales_daily
WHERE (sales_date = {ref_day:Date} OR sales_date = ${REF_WEEK_AGO})
  AND ${PT}
  AND ${BR}`,
  },
  fstore_daily_vs_lastweek: {
    // 14 hari berakhir di ref_day: pekan ini (ref_day-6..ref_day) vs pekan lalu (ref_day-13..ref_day-7), dipisah di halaman.
    params: ['ref_day', 'pt', 'branch'],
    sql: `SELECT sales_date, brand, branch_id, toFloat64(sum(net)) AS net_sum, sum(trx_count) AS trx
FROM efn_insight.v_esb_store_sales_daily
WHERE sales_date BETWEEN subtractDays({ref_day:Date}, 13) AND {ref_day:Date}
  AND ${PT}
  AND ${BR}
GROUP BY sales_date, brand, branch_id
ORDER BY sales_date, brand, branch_id
LIMIT 5000`,
  },
  fstore_top_menu: {
    params: ['ref_day', 'pt', 'branch'],
    sql: `SELECT brand, menu_code, any(menu_name) AS menu_label, any(menu_category) AS category,
       toFloat64(sum(qty)) AS qty_sold, toFloat64(sum(net)) AS net_sales
FROM efn_insight.v_esb_store_menu_daily
WHERE sales_date = {ref_day:Date}
  AND ${PT}
  AND ${BR}
GROUP BY brand, menu_code
ORDER BY qty_sold DESC
LIMIT 20`,
  },
  fstore_payment_mix: {
    params: ['ref_day', 'pt', 'branch'],
    sql: `SELECT payment_type, if(payment_method = '', 'Tidak diketahui', payment_method) AS method,
       sum(payment_count) AS cnt, toFloat64(sum(amount)) AS amount_sum
FROM efn_insight.v_esb_store_payment_daily
WHERE sales_date = {ref_day:Date}
  AND ${PT}
  AND ${BR}
GROUP BY payment_type, method
ORDER BY amount_sum DESC
LIMIT 100`,
  },
  fstore_ck_receipts: {
    // Kiriman CK yang DITERIMA cabang tujuan: 7 hari berakhir di ref_day. qty tidak dijumlah lintas uom (uom ada di GROUP BY).
    params: ['ref_day', 'pt', 'branch'],
    sql: `SELECT day, branch_id, any(branch_name) AS branch_label, origin_branch_name, item_group, product_code,
       any(product_name) AS product_label, uom, toFloat64(sum(qty)) AS qty_sum, sum(lines) AS lines_sum
FROM efn_insight.v_esb_store_receipt_daily
WHERE day BETWEEN subtractDays({ref_day:Date}, 6) AND {ref_day:Date}
  AND ${PT}
  AND ${BR}
GROUP BY day, branch_id, origin_branch_name, item_group, product_code, uom
ORDER BY day DESC, origin_branch_name, product_label
LIMIT 500`,
  },
  fstore_branch_list: {
    // Pemilih cabang: view kelas BR sehingga sesi ber-cabang hanya melihat cabangnya (tanpa makro BR: daftar utuh dalam cakupan).
    params: ['pt'],
    sql: `SELECT brand, company_code, branch_id, branch_name
FROM efn_insight.v_esb_branch_list
WHERE ${PT} AND is_store = 1 AND active = 1
ORDER BY brand, branch_name
LIMIT 500`,
  },

  // ---------- G69: penjualan level struk ESB OMS (brand PB; view `v_esb_oms_*`, kontrak docs/04 §33.5) ----------
  // Semua view OMS kelas BR (punya brand + branch_id): cakupan cabang memakai makro BR yang sama dengan view BR lain.
  foms_freshness: {
    // Per cabang, hanya hari ini; label "data per HH:MM WIB" = waktu tarik terakhir terbaru di cabang terpilih/terlihat.
    params: ['pt', 'branch'],
    // last_finished_at = DateTime64 UTC -> WIB; maxOrNull: tanpa baris hari ini = NULL (max() memberi 1970-01-01).
    sql: `SELECT toString(toTimeZone(maxOrNull(last_finished_at), 'Asia/Jakarta')) AS finished_at
FROM efn_insight.v_esb_oms_freshness
WHERE ${PT}
  AND ${BR}`,
  },
  foms_hourly: {
    // Per jam x kanal (visit purpose) untuk hari acuan; status selesai saja (diatur di view). Dijumlahkan lintas cabang terpilih.
    params: ['ref_day', 'pt', 'branch'],
    sql: `SELECT hour, visit_purpose_name AS channel_name, sum(bills) AS bills_sum, sum(pax) AS pax_sum,
       toFloat64(sum(gross_sales)) AS gross_sum, toFloat64(sum(net_sales)) AS net_sum, toFloat64(sum(items)) AS items_sum
FROM efn_insight.v_esb_oms_hourly
WHERE sales_date = {ref_day:Date}
  AND ${PT}
  AND ${BR}
GROUP BY hour, channel_name
ORDER BY hour, channel_name
LIMIT 1000`,
  },
  foms_fin_hourly: {
    // Terjual per jam per produk FIN. product_key view = kode produk polos (= v_esb_flow_daily.product_code); brand dibawa terpisah.
    params: ['ref_day', 'pt', 'branch', 'product'],
    // uom = satuan resep; non-EA tidak sebanding dengan pasokan Core (EA), ditandai di lib/oms-vm.ts.
    sql: `SELECT brand, product_key, uom, any(product_name) AS product_label, hour, toFloat64(sum(qty_fin)) AS qty_sum
FROM efn_insight.v_esb_oms_fin_hourly
WHERE sales_date = {ref_day:Date}
  AND ${PT}
  AND ${BR}
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_key)))
GROUP BY brand, product_key, uom, hour
ORDER BY brand, product_key, uom, hour
LIMIT 5000`,
  },
  foms_fin_supply: {
    // Pasokan toko hari acuan per produk FIN dari mart Core (definisi sama dengan sell-through /flow: SUPPLY).
    params: ['ref_day', 'pt', 'branch', 'product'],
    sql: `SELECT concat(brand, ':', product_code) AS product_key, any(product_name) AS product_label,
       toFloat64(sum(${SUPPLY})) AS supply_qty, toFloat64(sum(store_prod)) AS store_prod_qty
FROM efn_insight.v_esb_flow_daily
WHERE day = {ref_day:Date}
  AND ${PT}
  AND ${BR}
  AND ${PR}
  AND ${SALES_BRANCH}
GROUP BY product_key
LIMIT 2000`,
  },
  foms_fin_quality: {
    // Qty menu tak terpetakan per cabang-hari; porsi = Σunmapped_qty / Σmenu_qty x 100 dihitung di lib/oms-vm.ts (unmappedSharePct).
    params: ['ref_day', 'pt', 'branch'],
    sql: `SELECT toFloat64(unmapped_qty) AS unmapped_qty_f, toFloat64(menu_qty) AS menu_qty_f
FROM efn_insight.v_esb_oms_fin_mapping_quality
WHERE sales_date = {ref_day:Date}
  AND ${PT}
  AND ${BR}
LIMIT 500`,
  },

  // ---------- G52: Marketing F&B (/fnb/marketing) ----------
  fmkt_discount_kpi: {
    params: ['from', 'to', 'prev_from', 'prev_to', 'pt', 'branch'],
    sql: `SELECT
    toFloat64(sumIf(gross, sales_date >= {from:Date}))        AS gross_cur,
    toFloat64(sumIf(gross, sales_date <= {prev_to:Date}))     AS gross_prev,
    toFloat64(sumIf(discount, sales_date >= {from:Date}))     AS discount_cur,
    toFloat64(sumIf(discount, sales_date <= {prev_to:Date}))  AS discount_prev,
    toFloat64(sumIf(net, sales_date >= {from:Date}))          AS net_cur,
    toFloat64(sumIf(net, sales_date <= {prev_to:Date}))       AS net_prev,
    if(sumIf(gross, sales_date >= {from:Date}) <= 0, NULL,
       toFloat64(sumIf(discount, sales_date >= {from:Date})) / toFloat64(sumIf(gross, sales_date >= {from:Date})) * 100) AS disc_pct,
    if(sumIf(gross, sales_date <= {prev_to:Date}) <= 0, NULL,
       toFloat64(sumIf(discount, sales_date <= {prev_to:Date})) / toFloat64(sumIf(gross, sales_date <= {prev_to:Date})) * 100) AS disc_pct_prev
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${SALES_RANGE_PREV}
  AND ${PT}
  AND ${BR}`,
  },
  fmkt_discount_trend: {
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT sales_date, toFloat64(sum(gross)) AS gross_sum, toFloat64(sum(discount)) AS discount_sum
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
GROUP BY sales_date
ORDER BY sales_date
LIMIT 400`,
  },
  fmkt_discount_menu: {
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT brand, menu_code, any(menu_name) AS menu_label, any(menu_category) AS category,
       toFloat64(sum(qty)) AS qty_sum, toFloat64(sum(gross)) AS gross_sum, toFloat64(sum(discount)) AS discount_sum,
       if(sum(gross) <= 0, NULL, toFloat64(sum(discount)) / toFloat64(sum(gross)) * 100) AS disc_pct
FROM efn_insight.v_esb_sales_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
GROUP BY brand, menu_code
HAVING discount_sum > 0
ORDER BY discount_sum DESC
LIMIT 100`,
  },
  fmkt_discount_branch: {
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT brand, company_code, branch_id, any(branch_name) AS branch_label,
       toFloat64(sum(gross)) AS gross_sum, toFloat64(sum(discount)) AS discount_sum,
       if(sum(gross) <= 0, NULL, toFloat64(sum(discount)) / toFloat64(sum(gross)) * 100) AS disc_pct
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${SALES_BRANCH}
GROUP BY brand, company_code, branch_id
ORDER BY discount_sum DESC
LIMIT 500`,
  },
  fmkt_channel_mix: {
    // Pengelompokan kanal (GoFood/Grab/...) dilakukan di lib/channels.ts dari nama metode (Q-INS-F4), bukan di SQL.
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT payment_type, if(payment_method = '', 'Tidak diketahui', payment_method) AS method,
       sum(payment_count) AS cnt, toFloat64(sum(amount)) AS amount_sum
FROM efn_insight.v_esb_payment_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
GROUP BY payment_type, method
ORDER BY amount_sum DESC
LIMIT 200`,
  },
  fmkt_takeaway_share: {
    // is_takeaway ditebak dari nama menu ESB: selalu dilabeli "perkiraan" di UI.
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT is_takeaway, toFloat64(sum(qty)) AS qty_sum, toFloat64(sum(net)) AS net_sum
FROM efn_insight.v_esb_sales_daily
WHERE ${SALES_RANGE}
  AND ${PT}
  AND ${BR}
GROUP BY is_takeaway
ORDER BY is_takeaway
LIMIT 2`,
  },

  // ---------- Forecast F&B (tambahan, 23 Nov 2026): v_esb_forecast_daily = run forecast OK daily terbaru per PT; satuan qty ----------
  // dc_codes = kode gudang pusat (agregat outlet; env INSIGHT_FNB_DC_CODES), dikeluarkan dari total dan top produk.
  fnb_fc_next: {
    params: ['pt', 'today', 'until', 'dc_codes'],
    sql: `SELECT forecast_date, toFloat64(sum(yhat)) AS yhat_sum, toFloat64(sum(p10)) AS p10_sum, toFloat64(sum(p90)) AS p90_sum,
       max(generated_at) AS gen_at
FROM efn_insight.v_esb_forecast_daily
WHERE ${PT}
  AND forecast_date BETWEEN {today:Date} AND {until:Date}
  AND NOT has({dc_codes:Array(String)}, warehouse_code)
GROUP BY forecast_date
ORDER BY forecast_date
LIMIT 60`,
  },
  fnb_fc_by_wh: {
    params: ['pt', 'today', 'until'],
    sql: `SELECT company_code, warehouse_id, any(warehouse_code) AS wh_code, any(warehouse_name) AS wh_label,
       toFloat64(sum(yhat)) AS yhat_sum, toFloat64(sum(p10)) AS p10_sum, toFloat64(sum(p90)) AS p90_sum,
       uniqExact(product_id) AS product_n
FROM efn_insight.v_esb_forecast_daily
WHERE ${PT}
  AND forecast_date BETWEEN {today:Date} AND {until:Date}
GROUP BY company_code, warehouse_id
ORDER BY yhat_sum DESC
LIMIT 500`,
  },
  fnb_fc_top_products: {
    params: ['pt', 'today', 'until', 'dc_codes'],
    sql: `SELECT company_code, product_code, any(product_name) AS product_label, any(category) AS category_label,
       toFloat64(sum(yhat)) AS yhat_sum, toFloat64(sum(p10)) AS p10_sum, toFloat64(sum(p90)) AS p90_sum
FROM efn_insight.v_esb_forecast_daily
WHERE ${PT}
  AND forecast_date BETWEEN {today:Date} AND {until:Date}
  AND NOT has({dc_codes:Array(String)}, warehouse_code)
GROUP BY company_code, product_code
ORDER BY yhat_sum DESC
LIMIT 20`,
  },

  // ---------- Tahun lalu vs sekarang (/yoy, 24 Nov 2026; init/23-insight-yoy.sql) ----------
  // (tambahan) Pembanding = rentang yang sama digeser 364 hari (hari dalam minggu sama). Hanya pasangan cabang-hari yang
  // slice penjualannya selesai di KEDUA sisi (YOY_PAIR); hari yang belum lengkap (mis. 17 Nov 2026) dikeluarkan di dua sisi.
  yoy_branch: {
    params: ['from', 'to', 'yoy_from', 'yoy_to', 'pt', 'branch'],
    sql: `SELECT x.brand AS brand, x.branch_id AS branch_id, any(x.b_name) AS branch_name, any(x.c_code) AS company_code,
       toFloat64(sumIf(x.v_net, x.cur = 1)) AS net_cur, toFloat64(sumIf(x.v_net, x.cur = 0)) AS net_ly,
       toFloat64(sumIf(x.v_gross, x.cur = 1)) AS gross_cur, toFloat64(sumIf(x.v_gross, x.cur = 0)) AS gross_ly,
       toFloat64(sumIf(x.v_disc, x.cur = 1)) AS disc_cur, toFloat64(sumIf(x.v_disc, x.cur = 0)) AS disc_ly,
       toFloat64(sumIf(x.v_cogs, x.cur = 1)) AS cogs_cur, toFloat64(sumIf(x.v_cogs, x.cur = 0)) AS cogs_ly,
       toFloat64(sumIf(x.v_trx, x.cur = 1)) AS trx_cur, toFloat64(sumIf(x.v_trx, x.cur = 0)) AS trx_ly,
       countIf(x.cur = 1 AND x.v_net > 0) AS days_cur, countIf(x.cur = 0 AND x.v_net > 0) AS days_ly,
       toString(any(f.first_sale)) AS first_sale
FROM
(
    SELECT brand, branch_id, branch_name AS b_name, company_code AS c_code, net AS v_net, gross AS v_gross,
           discount AS v_disc, cogs AS v_cogs, trx_count AS v_trx, toUInt8(sales_date >= {from:Date}) AS cur
    FROM efn_insight.v_esb_sales_branch_daily
    WHERE ${CUR_OR_YOY}
      AND ${PT}
      AND ${BR}
      AND ${SALES_BRANCH}
      AND ${YOY_PAIR}
) AS x
LEFT JOIN
(
    SELECT brand, branch_id, min(sales_date) AS first_sale
    FROM efn_insight.v_esb_sales_branch_daily
    WHERE ${PT} AND net > 0
    GROUP BY brand, branch_id
) AS f ON f.brand = x.brand AND f.branch_id = x.branch_id
GROUP BY x.brand, x.branch_id
ORDER BY net_cur DESC
LIMIT 500`,
  },
  yoy_daily: {
    params: ['from', 'to', 'yoy_from', 'yoy_to', 'pt', 'branch'],
    sql: `SELECT toString(if(sales_date >= {from:Date}, sales_date, sales_date + 364)) AS day, brand, branch_id,
       toFloat64(sumIf(net, sales_date >= {from:Date})) AS net_cur, toFloat64(sumIf(net, sales_date < {from:Date})) AS net_ly,
       toFloat64(sumIf(trx_count, sales_date >= {from:Date})) AS trx_cur,
       toFloat64(sumIf(trx_count, sales_date < {from:Date})) AS trx_ly
FROM efn_insight.v_esb_sales_branch_daily
WHERE ${CUR_OR_YOY}
  AND ${PT}
  AND ${BR}
  AND ${SALES_BRANCH}
  AND ${YOY_PAIR}
GROUP BY day, brand, branch_id
ORDER BY day
LIMIT 20000`,
  },
  yoy_gaps: {
    params: ['from', 'to', 'yoy_from', 'yoy_to', 'pt', 'branch'],
    sql: `SELECT toString(g.d) AS day, toUInt32(countIf(g.c = 1 AND g.l = 0)) AS miss_ly, toUInt32(countIf(g.c = 0 AND g.l = 1)) AS miss_cur
FROM
(
    SELECT u.brand AS brand, u.branch_id AS branch_id, u.d AS d, max(u.src_c) AS c, max(u.src_l) AS l
    FROM
    (
        SELECT brand, branch_id, day AS d, toUInt8(1) AS src_c, toUInt8(0) AS src_l
        FROM efn_insight.v_esb_sales_done_day
        WHERE ${PT} AND ${BR} AND day BETWEEN {from:Date} AND {to:Date}
        UNION ALL
        SELECT brand, branch_id, day + 364 AS d, toUInt8(0) AS src_c, toUInt8(1) AS src_l
        FROM efn_insight.v_esb_sales_done_day
        WHERE ${PT} AND ${BR} AND day BETWEEN {yoy_from:Date} AND {yoy_to:Date}
    ) AS u
    WHERE (u.brand, u.branch_id) IN (${SALES_BRANCH_SET})
    GROUP BY u.brand, u.branch_id, u.d
) AS g
GROUP BY g.d
HAVING miss_ly + miss_cur > 0
ORDER BY g.d
LIMIT 400`,
  },
  yoy_menu: {
    params: ['from', 'to', 'yoy_from', 'yoy_to', 'pt', 'branch', 'cat'],
    sql: `SELECT menu_name, anyIf(menu_category, menu_category != '') AS category,
       toFloat64(sumIf(qty_paid, sales_date >= {from:Date})) AS qty_paid_cur, toFloat64(sumIf(qty_paid, sales_date < {from:Date})) AS qty_paid_ly,
       toFloat64(sumIf(gross_paid, sales_date >= {from:Date})) AS gross_paid_cur, toFloat64(sumIf(gross_paid, sales_date < {from:Date})) AS gross_paid_ly,
       toFloat64(sumIf(disc_paid, sales_date >= {from:Date})) AS disc_paid_cur, toFloat64(sumIf(disc_paid, sales_date < {from:Date})) AS disc_paid_ly,
       toFloat64(sumIf(cogs_paid, sales_date >= {from:Date})) AS cogs_paid_cur, toFloat64(sumIf(cogs_paid, sales_date < {from:Date})) AS cogs_paid_ly,
       toFloat64(sumIf(qty_free, sales_date >= {from:Date})) AS qty_free_cur, toFloat64(sumIf(qty_free, sales_date < {from:Date})) AS qty_free_ly,
       toFloat64(sumIf(cogs_free, sales_date >= {from:Date})) AS cogs_free_cur, toFloat64(sumIf(cogs_free, sales_date < {from:Date})) AS cogs_free_ly,
       toFloat64(sumIf(qty_pkg, sales_date >= {from:Date})) AS qty_pkg_cur, toFloat64(sumIf(qty_pkg, sales_date < {from:Date})) AS qty_pkg_ly,
       toFloat64(sumIf(cogs_pkg, sales_date >= {from:Date})) AS cogs_pkg_cur, toFloat64(sumIf(cogs_pkg, sales_date < {from:Date})) AS cogs_pkg_ly,
       toFloat64(sumIf(pkg_gross, sales_date >= {from:Date})) AS pkg_gross_cur, toFloat64(sumIf(pkg_gross, sales_date < {from:Date})) AS pkg_gross_ly,
       toFloat64(sumIf(pkg_disc, sales_date >= {from:Date})) AS pkg_disc_cur, toFloat64(sumIf(pkg_disc, sales_date < {from:Date})) AS pkg_disc_ly
FROM efn_insight.v_esb_menu_econ_daily
WHERE ${CUR_OR_YOY}
  AND ${PT}
  AND ${BR}
  AND ${CAT}
  AND ${SALES_BRANCH}
  AND ${YOY_PAIR}
GROUP BY menu_name
ORDER BY gross_paid_cur + pkg_gross_cur DESC
LIMIT 3000`,
  },

  // ---------- G68: /pnl (view kelas PT; basis = angka laporan ESB; scope 'branch' = per cabang, 'brand' = total brand) ----------
  pnl_kpi: {
    params: ['m_from', 'm_to', 'yoy_from', 'yoy_to', 'pt', 'branch'],
    sql: `SELECT
    ${PL_SUM('net_sales', PL_CUR)} AS net_sales,
    ${PL_SUM('gross_profit', PL_CUR)} AS gross_profit,
    ${PL_SUM('opex', PL_CUR)} AS opex,
    ${PL_SUM('ebitda', PL_CUR)} AS ebitda,
    ${PL_SUM('net_profit', PL_CUR)} AS net_profit,
    ${PL_SUM('net_sales', PL_LY)} AS net_sales_ly,
    ${PL_SUM('gross_profit', PL_LY)} AS gross_profit_ly,
    ${PL_SUM('opex', PL_LY)} AS opex_ly,
    ${PL_SUM('ebitda', PL_LY)} AS ebitda_ly,
    ${PL_SUM('net_profit', PL_LY)} AS net_profit_ly,
    toUInt32(uniqExactIf(branch_id, ${PL_CUR})) AS branches,
    toUInt32(uniqExactIf(period_month, ${PL_LY})) AS ly_months
FROM efn_insight.v_esb_pl_monthly
WHERE (${PL_CUR} OR ${PL_LY})
  AND ${PL_SUMMARY_BRANCH}
  AND ${PT}
  AND ${BR}`,
  },
  pnl_trend_monthly: {
    params: ['m_from', 'm_to', 'pt', 'branch'],
    sql: `SELECT period_month,
    ${PL_SUM('net_sales')} AS net_sales,
    ${PL_SUM('gross_profit')} AS gross_profit,
    ${PL_SUM('ebitda')} AS ebitda,
    ${PL_SUM('net_profit')} AS net_profit,
    max(is_partial) AS partial_month
FROM efn_insight.v_esb_pl_monthly
WHERE ${PL_CUR}
  AND ${PL_SUMMARY_BRANCH}
  AND ${PT}
  AND ${BR}
GROUP BY period_month
ORDER BY period_month
LIMIT 60`,
  },
  pnl_statement: {
    // Pos ringkasan per bulan untuk dua scope; baris `brand` hanya muncul bila filter cabang kosong (branch_id brand = 0).
    // "Tidak teralokasi ke outlet" = brand - jumlah cabang, dihitung di lib/pnl.ts.
    params: ['m_from', 'm_to', 'pt', 'branch'],
    sql: `SELECT period_month, scope, section, toFloat64(sum(amount)) AS amount_sum
FROM efn_insight.v_esb_pl_monthly
WHERE ${PL_CUR}
  AND line_type = 'summary'
  AND ${PT}
  AND ${BR}
GROUP BY period_month, scope, section
ORDER BY period_month, section
LIMIT 5000`,
  },
  pnl_branch_rank: {
    params: ['m_from', 'm_to', 'pt', 'branch'],
    sql: `SELECT brand, company_code, branch_id, any(branch_name) AS branch_label,
    ${PL_SUM('net_sales')} AS net_sales,
    ${PL_SUM('gross_profit')} AS gross_profit,
    ${PL_SUM('ebitda')} AS ebitda,
    ${PL_SUM('net_profit')} AS net_profit
FROM efn_insight.v_esb_pl_monthly
WHERE ${PL_CUR}
  AND ${PL_SUMMARY_BRANCH}
  AND ${PT}
  AND ${BR}
GROUP BY brand, company_code, branch_id
ORDER BY net_sales DESC
LIMIT 500`,
  },
  pnl_accounts: {
    // Akun per seksi untuk periode; 50 teratas (menurut nilai mutlak) per seksi. Drill seksi = filter kolom di tabel.
    params: ['m_from', 'm_to', 'pt', 'branch'],
    sql: `SELECT section, account_no, any(account_name) AS account_label, toFloat64(sum(amount)) AS amount_sum
FROM efn_insight.v_esb_pl_monthly
WHERE ${PL_CUR}
  AND line_type = 'account'
  AND scope = 'branch'
  AND ${PT}
  AND ${BR}
GROUP BY section, account_no
ORDER BY section, abs(amount_sum) DESC
LIMIT 50 BY section
LIMIT 600`,
  },

  // ---------- G68: /waste (view kelas PT; nilai = HPP jurnal item; qty per satuan, tidak dijumlah lintas satuan) ----------
  waste_kpi: {
    // Waste dan pemakaian internal selalu dua KPI tetap (filter `uclass` hanya untuk tren, rincian, dan produk).
    params: ['from', 'to', 'prev_from', 'prev_to', 'pt', 'branch'],
    sql: `SELECT
    toFloat64(sumIf(value_net, usage_class = 'waste' AND journal_date >= {from:Date}))        AS waste_cur,
    toFloat64(sumIf(value_net, usage_class = 'waste' AND journal_date <= {prev_to:Date}))     AS waste_prev,
    toFloat64(sumIf(value_net, usage_class = 'internal_use' AND journal_date >= {from:Date}))    AS internal_cur,
    toFloat64(sumIf(value_net, usage_class = 'internal_use' AND journal_date <= {prev_to:Date})) AS internal_prev,
    (SELECT toFloat64(sumIf(net, sales_date >= {from:Date}))
       FROM efn_insight.v_esb_sales_branch_daily
      WHERE ${SALES_RANGE_PREV} AND ${PT} AND ${BR}) AS net_cur,
    (SELECT toFloat64(sumIf(net, sales_date <= {prev_to:Date}))
       FROM efn_insight.v_esb_sales_branch_daily
      WHERE ${SALES_RANGE_PREV} AND ${PT} AND ${BR}) AS net_prev
FROM efn_insight.v_esb_item_usage_daily
WHERE ${U_RANGE_PREV}
  AND ${PT}
  AND ${BR}`,
  },
  waste_trend: {
    // weekly = 1 bila rentang > 62 hari: dikelompokkan per minggu (Senin).
    params: ['from', 'to', 'weekly', 'pt', 'branch', 'uclass'],
    sql: `SELECT if({weekly:UInt8} = 1, toStartOfWeek(journal_date, 1), journal_date) AS bucket, usage_class,
       toFloat64(sum(value_net)) AS value_sum
FROM efn_insight.v_esb_item_usage_daily
WHERE ${U_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${U_CLASS}
GROUP BY bucket, usage_class
ORDER BY bucket, usage_class
LIMIT 5000`,
  },
  waste_by_purpose: {
    // docs = jumlah dokumen per hari x cabang x purpose (uniqExact per grup view); aman dijumlah lintas hari/cabang, tidak lintas purpose.
    params: ['from', 'to', 'pt', 'branch', 'uclass'],
    sql: `SELECT usage_class, purpose_name, toFloat64(sum(value_net)) AS value_sum, sum(docs) AS docs_sum
FROM efn_insight.v_esb_item_usage_daily
WHERE ${U_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${U_CLASS}
GROUP BY usage_class, purpose_name
ORDER BY value_sum DESC
LIMIT 200`,
  },
  waste_branch_rank: {
    // Dua sumber digabung UNION ALL lalu dijumlah per cabang: pemakaian (waste, pemakaian internal) dan penjualan bersih.
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT brand, company_code, branch_id, any(label) AS branch_label,
       toFloat64(sum(w)) AS waste_value,
       toFloat64(sum(i)) AS internal_value,
       toFloat64(sum(n)) AS net_sum,
       if(sum(n) <= 0, NULL, toFloat64(sum(w)) / toFloat64(sum(n)) * 100) AS waste_pct_net
FROM
(
    SELECT brand, company_code, branch_id, branch_name AS label,
           if(usage_class = 'waste', toFloat64(value_net), toFloat64(0)) AS w,
           if(usage_class = 'internal_use', toFloat64(value_net), toFloat64(0)) AS i,
           toFloat64(0) AS n
    FROM efn_insight.v_esb_item_usage_daily
    WHERE ${U_RANGE} AND ${PT} AND ${BR}
    UNION ALL
    SELECT brand, company_code, branch_id, branch_name AS label,
           toFloat64(0) AS w, toFloat64(0) AS i, toFloat64(net) AS n
    FROM efn_insight.v_esb_sales_branch_daily
    WHERE ${SALES_RANGE} AND ${PT} AND ${BR}
)
GROUP BY brand, company_code, branch_id
HAVING sum(w) != 0 OR sum(i) != 0
ORDER BY waste_value DESC
LIMIT 500`,
  },
  waste_top_products: {
    // View bulanan: bulan penuh yang tersentuh rentang (awal bulan `from` s.d. `to`); qty per satuan.
    params: ['from', 'to', 'pt', 'branch', 'uclass'],
    sql: `SELECT brand, product_code, any(product_name) AS name_text, uom,
       toFloat64(sum(qty_out)) AS qty_sum, toFloat64(sum(value_net)) AS value_sum
FROM efn_insight.v_esb_item_usage_product_monthly
WHERE month BETWEEN toStartOfMonth({from:Date}) AND {to:Date}
  AND ${PT}
  AND ${BR}
  AND ${U_CLASS}
GROUP BY brand, product_code, uom
ORDER BY value_sum DESC
LIMIT 50`,
  },

  // ---------- G68: /fill-rate (view kelas PT; kohort tanggal permintaan; hanya baris matang & dihitung, §32.6.1) ----------
  fr_kpi: {
    params: ['from', 'to', 'prev_from', 'prev_to', 'pt', 'branch', 'supplier'],
    sql: `SELECT
    ${F_PCT('line_ratio_sum', 'lines', F_CUR)}  AS fill_rate_line_cur,
    ${F_PCT('line_ratio_sum', 'lines', F_PREV)} AS fill_rate_line_prev,
    ${F_PCT('lines_full', 'lines', F_CUR)}      AS pct_lines_full_cur,
    ${F_PCT('lines_full', 'lines', F_PREV)}     AS pct_lines_full_prev,
    ${F_PCT('requests_full', 'requests', F_CUR)}  AS pct_requests_full_cur,
    ${F_PCT('requests_full', 'requests', F_PREV)} AS pct_requests_full_prev,
    ${F_AVG_LEAD(F_CUR)}  AS avg_lead_days_cur,
    ${F_AVG_LEAD(F_PREV)} AS avg_lead_days_prev,
    toFloat64(sumIf(lines, ${F_CUR}))  AS lines_cur,
    toFloat64(sumIf(lines, ${F_PREV})) AS lines_prev,
    toFloat64(sumIf(requests, ${F_CUR})) AS requests_cur,
    toFloat64(sumIf(lines_open, ${F_CUR})) AS lines_open_cur,
    toFloat64(sumIf(lines_uom_mismatch, ${F_CUR})) AS lines_uom_mismatch_cur
FROM efn_insight.v_esb_fill_rate_daily
WHERE ${F_RANGE_PREV}
  AND ${PT}
  AND ${BR}
  AND ${F_SUPPLIER}`,
  },
  fr_trend_weekly: {
    params: ['from', 'to', 'pt', 'branch', 'supplier'],
    sql: `SELECT toStartOfWeek(transfer_date, 1) AS week_start,
       if(sum(lines) = 0, NULL, toFloat64(sum(line_ratio_sum)) / toFloat64(sum(lines)) * 100) AS fill_rate_line,
       if(sum(lines) = 0, NULL, toFloat64(sum(lines_full)) / toFloat64(sum(lines)) * 100) AS pct_lines_full,
       toFloat64(sum(lines)) AS lines_sum
FROM efn_insight.v_esb_fill_rate_daily
WHERE ${F_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${F_SUPPLIER}
GROUP BY week_start
ORDER BY week_start
LIMIT 200`,
  },
  fr_by_outlet: {
    params: ['from', 'to', 'pt', 'branch', 'supplier'],
    sql: `SELECT brand, company_code, branch_id, any(branch_name) AS branch_label,
       toFloat64(sum(requests)) AS requests_sum,
       toFloat64(sum(lines)) AS lines_sum,
       if(sum(lines) = 0, NULL, toFloat64(sum(line_ratio_sum)) / toFloat64(sum(lines)) * 100) AS fill_rate_line,
       if(sum(lines) = 0, NULL, toFloat64(sum(lines_full)) / toFloat64(sum(lines)) * 100) AS pct_lines_full,
       toFloat64(sum(lines_zero)) AS lines_zero_sum
FROM efn_insight.v_esb_fill_rate_daily
WHERE ${F_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${F_SUPPLIER}
GROUP BY brand, company_code, branch_id
ORDER BY fill_rate_line ASC NULLS LAST
LIMIT 500`,
  },
  fr_by_supplier: {
    params: ['from', 'to', 'pt', 'branch', 'supplier'],
    sql: `SELECT brand, supplier_branch_id, any(supplier_name) AS supplier_label,
       toFloat64(sum(lines)) AS lines_sum,
       if(sum(lines) = 0, NULL, toFloat64(sum(line_ratio_sum)) / toFloat64(sum(lines)) * 100) AS fill_rate_line,
       if(sum(lead_days_n) = 0, NULL, toFloat64(sum(lead_days_sum)) / toFloat64(sum(lead_days_n))) AS avg_lead_days
FROM efn_insight.v_esb_fill_rate_daily
WHERE ${F_RANGE}
  AND ${PT}
  AND ${BR}
  AND ${F_SUPPLIER}
GROUP BY brand, supplier_branch_id
ORDER BY fill_rate_line ASC NULLS LAST
LIMIT 100`,
  },
  fr_suppliers: {
    // (tambahan) opsi filter Pemasok: pemasok yang punya permintaan matang pada rentang/PT/cabang terpilih (tanpa filter pemasok).
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT brand, supplier_branch_id, any(supplier_name) AS supplier_label
FROM efn_insight.v_esb_fill_rate_daily
WHERE ${F_RANGE}
  AND ${PT}
  AND ${BR}
GROUP BY brand, supplier_branch_id
ORDER BY supplier_label
LIMIT 200`,
  },
  fr_top_short_products: {
    // View bulanan: bulan penuh yang tersentuh rentang. Fill rate qty hanya per produk x satuan (sent_capped / request_qty).
    params: ['from', 'to', 'pt', 'branch'],
    sql: `SELECT brand, product_code, any(product_name) AS name_text, uom,
       toFloat64(sum(request_qty)) AS request_sum,
       toFloat64(sum(qty_sent)) AS sent_sum,
       if(sum(request_qty) <= 0, NULL, toFloat64(sum(sent_capped)) / toFloat64(sum(request_qty)) * 100) AS fill_rate_qty,
       toFloat64(sum(request_qty)) - toFloat64(sum(sent_capped)) AS short_qty
FROM efn_insight.v_esb_fill_rate_product_monthly
WHERE month BETWEEN toStartOfMonth({from:Date}) AND {to:Date}
  AND ${PT}
  AND ${BR}
GROUP BY brand, product_code, uom
ORDER BY short_qty DESC
LIMIT 50`,
  },

  // ---------- Margin bahan (/margin-bahan, G67, docs/04 §31; init/24-margin-bahan.sql) ----------
  // Wrapper `efn_insight.v_esb_*_monthly` = kolom mart tanpa _version, versi terakhir per brand, row policy kelas PT.
  // Hanya parameter `pt` (jendela bulan ditentukan job refresh, bukan filter tanggal halaman).
  mb_group_monthly: {
    params: ['pt'],
    // Tanpa alias bernama sama dengan kolom (alias siklik di ClickHouse); Date keluar sebagai 'YYYY-MM-DD', angka dikonversi di halaman.
    sql: `SELECT brand, company_code, month, ing_group, basket_value, base_value
FROM efn_insight.v_esb_ing_group_monthly
WHERE ${PT}
ORDER BY month, ing_group
LIMIT 5000`,
  },
  mb_price_monthly: {
    // Kolom dipangkas ke yang dipakai halaman (tanpa receipt_qty/value/price/price_4m) agar muatan kecil.
    params: ['pt'],
    sql: `SELECT brand, company_code, month, product_code, product_name, ing_group, uom, idx, usage_value
FROM efn_insight.v_esb_ing_price_monthly
WHERE ${PT}
ORDER BY brand, product_code, month
LIMIT 40000`,
  },
  mb_menu_monthly: {
    params: ['pt'],
    sql: `SELECT brand, company_code, month, menu_name, menu_category, qty, net, cogs, bom_matched, cost_idx, leaf_coverage
FROM efn_insight.v_esb_menu_cost_monthly
WHERE ${PT}
ORDER BY brand, menu_name, month
LIMIT 30000`,
  },
} as const satisfies Record<string, QueryDef>;

export type QueryName = keyof typeof QUERIES;
