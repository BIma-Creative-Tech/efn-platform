/**
 * KATALOG QUERY supermarket (docs/04 §28.13.9, §28.13.11; G47). Nama view/kolom = kontrak §28.13.9 (menang atas
 * docs/design/insight-supermarket.md untuk nama view/kolom). Semua SQL konstanta; TIDAK ada SQL/identifier dari pengguna.
 * Nilai filter hanya lewat placeholder bertipe `{nama:Tipe}` + parameter HTTP `param_nama`.
 *
 * Aturan katalog (dijaga tests/queries-sm.test.ts):
 *  - hanya view `efn_insight.v_sm_*`;
 *  - setiap query memfilter `company_id IN {cids}` (lapis 1) di samping row policy (lapis 2, §28.13.7); query berbasis warehouse
 *    juga memfilter `warehouse_id` (kecuali baris tingkat PT `warehouse_id = 0` dan view kelas F `v_sm_freshness`);
 *  - alias agregat TIDAK BOLEH sama dengan nama kolom view (ClickHouse akan menafsirkan ulang alias di WHERE);
 *  - non-agregat tunggal wajib `LIMIT` literal.
 *
 * Parameter: `wh` = outlet terpilih (tanpa 0); `whs` = semua warehouse sesi tanpa 0 (abaikan filter); `whz` = pilihan termasuk 0
 * (halaman Marketing: event tanpa outlet = 0). Diisi loader dari sesi terverifikasi + filter yang sudah divalidasi.
 *
 * STATUS: seluruh katalog dijalankan lead 21 Nov 2026 ke ClickHouse 24.8 (skema + row policy SM): 41/41 OK setelah perbaikan
 * sm_cm_signal. Uji ulang: tests/sm-queries.live.test.ts (CH_LIVE_URL).
 */
import 'server-only';
import { K_ANON } from './sm-defaults';

export type SmChType = 'Date' | 'Array(UInt32)' | 'String' | 'Float64' | 'UInt32';

export const SM_PARAM_TYPES = {
  from: 'Date',
  to: 'Date',
  prev_from: 'Date',
  prev_to: 'Date',
  yoy_from: 'Date',
  yoy_to: 'Date',
  today: 'Date',
  until: 'Date',
  gm_from: 'Date',
  cids: 'Array(UInt32)',
  wh: 'Array(UInt32)',
  whs: 'Array(UInt32)',
  whz: 'Array(UInt32)',
  cat: 'Array(UInt32)',
  ids: 'Array(UInt32)',
  channel: 'String',
  price_warn: 'Float64',
  min_n: 'UInt32',
} as const satisfies Record<string, SmChType>;

export type SmParamName = keyof typeof SM_PARAM_TYPES;

export interface SmQueryDef {
  sql: string;
  params: readonly SmParamName[];
}

const PLACEHOLDER = /\{(\w+):([A-Za-z0-9()]+)\}/g;

/** Turunkan daftar parameter dari placeholder dan pastikan tipenya sama dengan SM_PARAM_TYPES (gagal keras saat impor). */
function q(sql: string): SmQueryDef {
  const found = new Map<string, string>();
  for (const m of sql.matchAll(PLACEHOLDER)) {
    const [, name, type] = m;
    const want = (SM_PARAM_TYPES as Record<string, string>)[name];
    if (want === undefined || want !== type) throw new Error(`queries-sm: placeholder ${name}:${type} tidak cocok katalog`);
    found.set(name, type);
  }
  return { sql, params: Array.from(found.keys()).sort() as SmParamName[] };
}

// --- fragmen standar ---
const CO = 'company_id IN {cids:Array(UInt32)}';
const WH = 'warehouse_id IN {wh:Array(UInt32)}';
const SCOPE = `${CO} AND ${WH}`;
const SCOPE_S = `${CO} AND warehouse_id IN {whs:Array(UInt32)}`;
const SCOPE_Z = `${CO} AND warehouse_id IN {whz:Array(UInt32)}`;
/** Kategori terpilih + turunannya (cocokkan `categ_parent_path` Odoo "1/3/9/", §28.13.11). Kosong = semua kategori. */
const CATF =
  `(empty({cat:Array(UInt32)}) OR categ_id IN (SELECT categ_id FROM efn_insight.v_sm_product_list WHERE ${CO} ` +
  `AND arrayExists(c -> position(concat('/', categ_parent_path), concat('/', toString(c), '/')) > 0, {cat:Array(UInt32)})))`;
const D_CUR = '(day BETWEEN {from:Date} AND {to:Date})';
const D_PREV = '(day BETWEEN {prev_from:Date} AND {prev_to:Date})';
const D_YOY = '(day BETWEEN {yoy_from:Date} AND {yoy_to:Date})';
const FD_CUR = '(delivery_date BETWEEN {from:Date} AND {to:Date})';
const FD_PREV = '(delivery_date BETWEEN {prev_from:Date} AND {prev_to:Date})';
const V = 'efn_insight.v_sm_';

export const SM_QUERIES = {
  // ---------- filter, konfigurasi, kesegaran ----------
  sm_warehouses: q(`SELECT warehouse_id, warehouse_code, warehouse_name, outlet_type, active
FROM ${V}warehouse_list
WHERE ${SCOPE_S}
ORDER BY outlet_type, warehouse_name
LIMIT 300`),
  sm_categories: q(`SELECT categ_id, any(category) AS category_name
FROM ${V}product_list
WHERE ${CO} AND active = 1
GROUP BY categ_id
ORDER BY category_name
LIMIT 500`),
  sm_kpi_config: q(`SELECT company_id, key, value_num, value_str, is_default, effective_since
FROM ${V}kpi_config
WHERE ${CO}
ORDER BY company_id, key
LIMIT 500`),
  sm_freshness: q(`SELECT mart, freshness_class, data_as_of, source_as_of, last_run_at, last_status
FROM ${V}freshness
ORDER BY mart
LIMIT 50`),
  sm_product_names: q(`SELECT product_id, any(product_tmpl_id) AS tmpl_id, any(product_name) AS name_text
FROM ${V}product_list
WHERE ${CO} AND product_id IN {ids:Array(UInt32)}
GROUP BY product_id
LIMIT 200`),

  // ---------- Kepala toko (/store) ----------
  sm_st_proposals: q(`SELECT proposal_id, warehouse_id, delivery_date, cutoff_at, state, approved_at, picking_id, line_count, lines_excluded, lines_changed
FROM ${V}replenishment
WHERE ${SCOPE} AND delivery_date BETWEEN {today:Date} AND {until:Date}
ORDER BY delivery_date, cutoff_at, proposal_id
LIMIT 20`),
  sm_st_expiry: q(`SELECT alert_id, warehouse_id, lot_id, lot_name, product_tmpl_id, product_name, categ_id, stage, days_left, qty, value_cost,
  markdown_id, markdown_state, markdown_effective_pct, markdown_price, markdown_date_to, display_status
FROM ${V}lot_expiry
WHERE ${SCOPE} AND ${CATF}
ORDER BY days_left ASC, value_cost DESC
LIMIT 200`),
  sm_st_expiry_trend: q(`SELECT day, toFloat64(sum(lots)) AS lots_n
FROM ${V}expiry_daily
WHERE ${SCOPE} AND day BETWEEN {prev_from:Date} AND {to:Date}
GROUP BY day
ORDER BY day
LIMIT 10`),
  sm_st_critical: q(`SELECT warehouse_id, product_id, product_tmpl_id, default_code, product_name, on_hand_qty, min_qty, avg_daily_sales, days_of_cover,
  is_out, is_critical, open_proposal_id, snapshot_ts
FROM ${V}stock_critical
WHERE ${SCOPE} AND ${CATF} AND (is_out = 1 OR is_critical = 1)
ORDER BY is_out DESC, avg_daily_sales DESC
LIMIT 200`),
  sm_waste_sums: q(`SELECT toFloat64(sumIf(waste_value_cost, ${D_CUR})) AS waste_cur, toFloat64(sumIf(waste_value_cost, ${D_PREV})) AS waste_prev
FROM ${V}waste_daily
WHERE ${SCOPE} AND ${CATF} AND (${D_CUR} OR ${D_PREV})`),
  sm_waste_reasons: q(`SELECT reason_name, toFloat64(sum(waste_value_cost)) AS value_sum, toFloat64(sum(waste_qty)) AS qty_sum
FROM ${V}waste_daily
WHERE ${SCOPE} AND ${CATF} AND ${D_CUR}
GROUP BY reason_name
ORDER BY value_sum DESC
LIMIT 12`),
  sm_sales_net: q(`SELECT toFloat64(sumIf(net_sales, ${D_CUR})) AS net_cur, toFloat64(sumIf(net_sales, ${D_PREV})) AS net_prev
FROM ${V}sales_daily
WHERE ${SCOPE} AND ${CATF} AND (${D_CUR} OR ${D_PREV})`),
  sm_cogs_sums: q(`SELECT toFloat64(sumIf(cogs, ${D_CUR})) AS cogs_cur, toFloat64(sumIf(cogs, ${D_PREV})) AS cogs_prev
FROM ${V}margin_daily
WHERE ${SCOPE} AND ${CATF} AND (${D_CUR} OR ${D_PREV})`),

  // ---------- Direksi (/exec) ----------
  sm_ex_sales: q(`SELECT toFloat64(sumIf(net_sales_incl_tax, ${D_CUR})) AS rev_cur, toFloat64(sumIf(net_sales_incl_tax, ${D_PREV})) AS rev_prev,
  toFloat64(sumIf(net_sales_incl_tax, ${D_YOY})) AS rev_yoy, toFloat64(sumIf(net_sales, ${D_CUR})) AS net_cur, toFloat64(sumIf(net_sales, ${D_PREV})) AS net_prev
FROM ${V}sales_daily
WHERE ${SCOPE} AND ${CATF} AND (${D_CUR} OR ${D_PREV} OR ${D_YOY})`),
  sm_ex_trend: q(`SELECT day, toFloat64(sum(net_sales_incl_tax)) AS rev_sum
FROM ${V}sales_daily
WHERE ${SCOPE} AND ${CATF} AND (${D_CUR} OR ${D_PREV})
GROUP BY day
ORDER BY day
LIMIT 800`),
  sm_ex_margin: q(`SELECT toFloat64(sumIf(net_sales, ${D_CUR})) AS net_cur, toFloat64(sumIf(net_sales, ${D_PREV})) AS net_prev,
  toFloat64(sumIf(gross_margin, ${D_CUR})) AS gm_cur, toFloat64(sumIf(gross_margin, ${D_PREV})) AS gm_prev,
  toFloat64(sumIf(net_sales_with_cost, ${D_CUR})) AS cov_cur, toFloat64(sumIf(cogs, ${D_CUR})) AS cogs_cur, toFloat64(sumIf(cogs, ${D_PREV})) AS cogs_prev
FROM ${V}margin_daily
WHERE ${SCOPE} AND ${CATF} AND (${D_CUR} OR ${D_PREV})`),
  sm_ex_gmroi: q(`SELECT month, outlet_type, toFloat64(sum(gross_margin)) AS gm_sum, toFloat64(sum(avg_stock_value_cost_daily)) AS stock_daily_sum,
  toFloat64(sum(avg_stock_value_cost_month_end)) AS stock_me_sum, toFloat64(any(days_in_month)) AS days_n
FROM ${V}gmroi_monthly
WHERE ${SCOPE} AND ${CATF} AND month BETWEEN {gm_from:Date} AND {to:Date}
GROUP BY month, outlet_type
ORDER BY month
LIMIT 100`),
  // WAPE hanya outlet: forecast DC = permintaan keluar DC (jumlah outlet) tanpa penjualan aktual -> galat palsu (UAT G51).
  sm_ex_wape_weekly: q(`SELECT week_start, toFloat64(sum(abs_error)) AS abs_sum, toFloat64(sum(actual_qty)) AS actual_sum, toFloat64(sum(forecast_qty)) AS fc_sum
FROM ${V}forecast_accuracy_weekly
WHERE ${SCOPE} AND ${CATF} AND outlet_type != 'dc' AND week_start BETWEEN {from:Date} AND {to:Date}
GROUP BY week_start
ORDER BY week_start
LIMIT 60`),
  sm_ex_wape_outlets: q(`SELECT warehouse_id, toFloat64(sum(abs_error)) AS abs_sum, toFloat64(sum(actual_qty)) AS actual_sum, toFloat64(sum(forecast_qty)) AS fc_sum,
  toUInt64(uniqExact(product_id)) AS sku_n
FROM ${V}forecast_accuracy_weekly
WHERE ${SCOPE} AND ${CATF} AND outlet_type != 'dc' AND week_start BETWEEN {from:Date} AND {to:Date}
GROUP BY warehouse_id
LIMIT 300`),
  sm_ex_by_outlet_sales: q(`SELECT warehouse_id, toFloat64(sumIf(net_sales_incl_tax, ${D_CUR})) AS rev_cur, toFloat64(sumIf(net_sales_incl_tax, ${D_PREV})) AS rev_prev,
  toFloat64(sumIf(net_sales_incl_tax, ${D_YOY})) AS rev_yoy, toFloat64(sumIf(net_sales, ${D_CUR})) AS net_cur
FROM ${V}sales_daily
WHERE ${SCOPE} AND ${CATF} AND (${D_CUR} OR ${D_PREV} OR ${D_YOY})
GROUP BY warehouse_id
LIMIT 300`),
  sm_ex_by_outlet_margin: q(`SELECT warehouse_id, toFloat64(sumIf(gross_margin, ${D_CUR})) AS gm_cur, toFloat64(sumIf(net_sales, ${D_CUR})) AS net_cur
FROM ${V}margin_daily
WHERE ${SCOPE} AND ${CATF} AND ${D_CUR}
GROUP BY warehouse_id
LIMIT 300`),
  sm_ex_by_outlet_waste: q(`SELECT warehouse_id, toFloat64(sum(waste_value_cost)) AS waste_cur
FROM ${V}waste_daily
WHERE ${SCOPE} AND ${CATF} AND ${D_CUR}
GROUP BY warehouse_id
LIMIT 300`),
  sm_ex_waste_by_cat: q(`SELECT categ_id, toFloat64(sum(waste_value_cost)) AS waste_cur
FROM ${V}waste_daily
WHERE ${SCOPE} AND ${CATF} AND ${D_CUR}
GROUP BY categ_id
LIMIT 500`),
  sm_ex_sales_by_cat: q(`SELECT categ_id, toFloat64(sum(net_sales)) AS net_cur
FROM ${V}sales_daily
WHERE ${SCOPE} AND ${CATF} AND ${D_CUR}
GROUP BY categ_id
LIMIT 500`),

  // ---------- Category manager (/category) ----------
  sm_cm_oos: q(`SELECT toFloat64(sumIf(sku_oos, ${D_CUR})) AS oos_cur, toFloat64(sumIf(sku_universe, ${D_CUR})) AS uni_cur,
  toFloat64(sumIf(sku_oos, ${D_PREV})) AS oos_prev, toFloat64(sumIf(sku_universe, ${D_PREV})) AS uni_prev
FROM ${V}stock_daily
WHERE ${SCOPE} AND ${CATF} AND (${D_CUR} OR ${D_PREV})`),
  sm_cm_oos_matrix: q(`SELECT categ_id, warehouse_id, toFloat64(sum(sku_oos)) AS oos_sum, toFloat64(sum(sku_universe)) AS uni_sum
FROM ${V}stock_daily
WHERE ${SCOPE} AND ${CATF} AND ${D_CUR}
GROUP BY categ_id, warehouse_id
HAVING uni_sum > 0
LIMIT 2000`),
  sm_cm_oos_products: q(`SELECT product_id, toUInt64(countIf(oos = 1)) AS oos_days, toUInt64(countIf(in_universe = 1)) AS universe_days,
  toUInt64(uniqExactIf(warehouse_id, oos = 1)) AS outlets_n
FROM ${V}oos_product_daily
WHERE ${SCOPE} AND ${CATF} AND ${D_CUR} AND in_universe = 1
GROUP BY product_id
HAVING oos_days > 0
ORDER BY oos_days DESC
LIMIT 10`),
  sm_cm_expiry: q(`SELECT alert_id, warehouse_id, lot_id, lot_name, product_tmpl_id, product_name, categ_id, stage, days_left, qty, value_cost,
  markdown_id, markdown_state, markdown_effective_pct, markdown_price, markdown_date_to, display_status
FROM ${V}lot_expiry
WHERE ${SCOPE} AND ${CATF}
ORDER BY days_left ASC, value_cost DESC
LIMIT 300`),
  sm_cm_expiry_totals: q(`SELECT display_status, toUInt64(count()) AS lots_n, toFloat64(sum(value_cost)) AS value_sum
FROM ${V}lot_expiry
WHERE ${SCOPE} AND ${CATF}
GROUP BY display_status
LIMIT 10`),
  sm_cm_price: q(`SELECT product_tmpl_id, default_code, product_name, categ_id, our_price, market_median_30d, sample_n_30d, competitors_n_30d, last_sample_date, diff_pct
FROM ${V}price_index
WHERE ${CO} AND warehouse_id = 0 AND ${CATF} AND sample_n_30d >= {min_n:UInt32} AND diff_pct IS NOT NULL
ORDER BY abs(diff_pct) DESC
LIMIT 100`),
  sm_cm_price_coverage: q(`SELECT toUInt64(sum(sku_active)) AS active_n, toUInt64(sum(sku_with_sample_3)) AS with_n
FROM ${V}price_coverage
WHERE ${CO} AND warehouse_id = 0 AND ${CATF}`),
  // Hitungan perilaku di view bernilai NULL bila viewers_30d < K_ANON; filter literal K_ANON menjaga k-anonim di lapis aplikasi.
  sm_cm_signal: q(`SELECT product_id, categ_id, viewers_30d, views_30d, cart_adds_30d, purchases_30d, our_price, market_median_30d, diff_pct
FROM ${V}price_signal
WHERE ${CO} AND warehouse_id = 0 AND ${CATF} AND viewers_30d >= ${K_ANON} AND diff_pct > {price_warn:Float64}
ORDER BY viewers_30d DESC
LIMIT 100`),

  // ---------- DC (/dc) ----------
  sm_dc_fill_sums: q(`SELECT toFloat64(sumIf(least(qty_basis, qty_approved), ${FD_CUR})) AS lq_cur, toFloat64(sumIf(qty_approved, ${FD_CUR})) AS ap_cur,
  toUInt64(countIf(qty_basis >= qty_approved AND ${FD_CUR})) AS full_cur, toUInt64(countIf(${FD_CUR})) AS lines_cur,
  toFloat64(sumIf(least(qty_basis, qty_approved), ${FD_PREV})) AS lq_prev, toFloat64(sumIf(qty_approved, ${FD_PREV})) AS ap_prev,
  toUInt64(countIf(qty_basis >= qty_approved AND ${FD_PREV})) AS full_prev, toUInt64(countIf(${FD_PREV})) AS lines_prev
FROM ${V}dc_fill_rate
WHERE ${SCOPE} AND qty_approved > 0 AND (${FD_CUR} OR ${FD_PREV})`),
  sm_dc_fill_daily: q(`SELECT delivery_date, toFloat64(sum(least(qty_basis, qty_approved))) AS lq_sum, toFloat64(sum(qty_approved)) AS ap_sum
FROM ${V}dc_fill_rate
WHERE ${SCOPE} AND qty_approved > 0 AND ${FD_CUR}
GROUP BY delivery_date
ORDER BY delivery_date
LIMIT 400`),
  sm_dc_fill_outlets: q(`SELECT warehouse_id, toFloat64(sum(least(qty_basis, qty_approved))) AS lq_sum, toFloat64(sum(qty_approved)) AS ap_sum,
  toUInt64(countIf(qty_basis >= qty_approved)) AS full_n, toUInt64(count()) AS lines_n
FROM ${V}dc_fill_rate
WHERE ${SCOPE} AND qty_approved > 0 AND ${FD_CUR}
GROUP BY warehouse_id
LIMIT 300`),
  sm_dc_orders: q(`SELECT proposal_id, warehouse_id, delivery_date, cutoff_at, state, line_count, lines_changed, qty_proposed_total, qty_approved_total
FROM ${V}replenishment
WHERE ${SCOPE} AND delivery_date >= {today:Date}
ORDER BY delivery_date, cutoff_at, warehouse_id
LIMIT 60`),
  sm_dc_po: q(`SELECT order_id, any(supplier_id) AS supplier_key, any(supplier_name) AS supplier_label, any(po_state) AS state_key, min(date_planned) AS planned_at,
  toFloat64(sum(price_subtotal)) AS value_sum, toUInt64(count()) AS lines_n, toUInt8(max(is_late)) AS late_flag, toUInt8(max(from_orderpoint)) AS auto_flag
FROM ${V}dc_purchase
WHERE ${SCOPE_S} AND outlet_type = 'dc' AND (po_state IN ('draft', 'sent', 'to approve') OR (po_state = 'purchase' AND qty_received < product_qty))
GROUP BY order_id
ORDER BY late_flag DESC, planned_at ASC
LIMIT 100`),
  sm_dc_po_totals: q(`SELECT toUInt64(uniqExactIf(order_id, is_late = 1)) AS late_n, toUInt64(uniqExact(order_id)) AS open_n
FROM ${V}dc_purchase
WHERE ${SCOPE_S} AND outlet_type = 'dc' AND (po_state IN ('draft', 'sent', 'to approve') OR (po_state = 'purchase' AND qty_received < product_qty))`),
  sm_dc_stock_age: q(`SELECT toFloat64(sum(stock_value_cost)) AS total_sum, toFloat64(sum(value_age_0_30)) AS a0_sum, toFloat64(sum(value_age_31_60)) AS a31_sum,
  toFloat64(sum(value_age_61_90)) AS a61_sum, toFloat64(sum(value_age_91p)) AS a91_sum, toFloat64(sum(value_exp_expired)) AS exp0_sum,
  toFloat64(sum(value_exp_0_7)) AS exp7_sum, toFloat64(sum(value_exp_8_30)) AS exp30_sum, toFloat64(sum(value_exp_31_90)) AS exp90_sum,
  toFloat64(sum(value_exp_91p)) AS exp91_sum, toFloat64(sum(value_no_lot)) AS nolot_sum, max(day) AS as_of_day
FROM ${V}stock_value_daily
WHERE ${SCOPE_S} AND outlet_type = 'dc' AND day = (SELECT max(day) FROM ${V}stock_value_daily WHERE ${SCOPE_S} AND outlet_type = 'dc')`),

  // ---------- Marketing (/marketing) ----------
  sm_mk_funnel: q(`SELECT toFloat64(sumIf(viewers, ${D_CUR})) AS view_cur, toFloat64(sumIf(carters, ${D_CUR})) AS cart_cur,
  toFloat64(sumIf(checkout_starters, ${D_CUR})) AS chk_cur, toFloat64(sumIf(purchasers, ${D_CUR})) AS buy_cur,
  toFloat64(sumIf(viewers, ${D_PREV})) AS view_prev, toFloat64(sumIf(purchasers, ${D_PREV})) AS buy_prev
FROM ${V}visitor_funnel_daily
WHERE ${SCOPE_Z} AND (${D_CUR} OR ${D_PREV}) AND ({channel:String} = '' OR channel = {channel:String})`),
  sm_mk_campaigns: q(`SELECT campaign_id, name, kind, state, channels, date_from, date_to, delivered, vouchers_issued, vouchers_used, orders_with_voucher,
  discount_idr, revenue_with_voucher_idr, budget_idr, stop_reason
FROM ${V}campaign_result
WHERE ${CO} AND warehouse_id = 0 AND date_to >= {from:Date} AND date_from <= {to:Date}
ORDER BY date_from DESC, campaign_id DESC
LIMIT 100`),

  // ---------- G51 B1: SMD di halaman F&B (/, /sales, /payments); persona exec saja; omzet SMD termasuk PPN ----------
  sm_fb_daily: q(`SELECT day, toFloat64(sum(net_sales_incl_tax)) AS rev_sum, toUInt64(sum(orders)) AS orders_sum
FROM ${V}outlet_daily
WHERE ${SCOPE} AND (${D_CUR} OR ${D_PREV})
GROUP BY day
ORDER BY day
LIMIT 800`),
  sm_fb_by_outlet: q(`SELECT warehouse_id, toFloat64(sum(net_sales_incl_tax)) AS rev_cur, toUInt64(sum(orders)) AS orders_cur
FROM ${V}outlet_daily
WHERE ${SCOPE} AND ${D_CUR}
GROUP BY warehouse_id
LIMIT 300`),
  sm_fb_sales_kpi: q(`SELECT toFloat64(abs(sumIf(gross_sales, ${D_CUR}))) AS gross_cur, toFloat64(abs(sumIf(gross_sales, ${D_PREV}))) AS gross_prev,
  toFloat64(abs(sumIf(discounts, ${D_CUR}))) AS disc_cur, toFloat64(abs(sumIf(discounts, ${D_PREV}))) AS disc_prev,
  toFloat64(sumIf(qty_sold, ${D_CUR})) AS qty_cur, toFloat64(sumIf(qty_sold, ${D_PREV})) AS qty_prev
FROM ${V}sales_daily
WHERE ${SCOPE} AND (${D_CUR} OR ${D_PREV})`),
  sm_fb_weekday: q(`SELECT toDayOfWeek(day) AS dow, toFloat64(sum(net_sales_incl_tax)) AS rev_sum, toUInt64(uniqExact(day)) AS days_n
FROM ${V}sales_daily
WHERE ${SCOPE} AND ${D_CUR}
GROUP BY dow
ORDER BY dow
LIMIT 7`),
  sm_fb_heat: q(`SELECT toMonday(day) AS week_key, toDayOfWeek(day) AS dow, toFloat64(sum(net_sales_incl_tax)) AS rev_sum
FROM ${V}sales_daily
WHERE ${SCOPE} AND ${D_CUR}
GROUP BY week_key, dow
ORDER BY week_key, dow
LIMIT 500`),
  sm_fb_by_category: q(`SELECT categ_id, any(category) AS category_name, toFloat64(sum(net_sales_incl_tax)) AS rev_sum, toFloat64(sum(qty_sold)) AS qty_sum
FROM ${V}sales_daily
WHERE ${SCOPE} AND ${D_CUR}
GROUP BY categ_id
ORDER BY rev_sum DESC
LIMIT 100`),
  sm_fb_top_products: q(`SELECT product_id, toFloat64(sum(net_sales_incl_tax)) AS rev_sum, toFloat64(sum(qty_sold)) AS qty_sum
FROM ${V}product_sales_daily
WHERE ${SCOPE} AND ${CATF} AND ${D_CUR}
GROUP BY product_id
ORDER BY rev_sum DESC
LIMIT 10`),
  // Pembayaran SMD = uang diterima bersih (docs/04 §28.14.3: `amount` sudah memuat baris kembalian; retur ikut menetralkan), tanpa filter is_refund.
  sm_fb_pay_sums: q(`SELECT toFloat64(sumIf(amount, ${D_CUR})) AS amount_cur, toFloat64(sumIf(amount, ${D_PREV})) AS amount_prev,
  toUInt64(sumIf(payments, ${D_CUR})) AS count_cur, toUInt64(sumIf(payments, ${D_PREV})) AS count_prev
FROM ${V}payment_daily
WHERE ${SCOPE} AND (${D_CUR} OR ${D_PREV})`),
  sm_fb_pay_matrix: q(`SELECT warehouse_id, method_name, method_type, toFloat64(sum(amount)) AS amount_sum, toUInt64(sum(payments)) AS pay_n
FROM ${V}payment_daily
WHERE ${SCOPE} AND ${D_CUR}
GROUP BY warehouse_id, method_name, method_type
ORDER BY amount_sum DESC
LIMIT 2000`),
  sm_fb_pay_trend: q(`SELECT day, method_type, toFloat64(sum(amount)) AS amount_sum, toUInt64(sum(payments)) AS pay_n
FROM ${V}payment_daily
WHERE ${SCOPE} AND ${D_CUR}
GROUP BY day, method_type
ORDER BY day
LIMIT 5000`),

  // ---------- G51 B3: forecast SMD (/forecast). Mingguan + per outlet memakai sm_ex_wape_weekly / sm_ex_wape_outlets ----------
  sm_fc_by_cat: q(`SELECT categ_id, toFloat64(sum(abs_error)) AS abs_sum, toFloat64(sum(actual_qty)) AS actual_sum, toFloat64(sum(forecast_qty)) AS fc_sum,
  toUInt64(uniqExact(product_id)) AS sku_n
FROM ${V}forecast_accuracy_weekly
WHERE ${SCOPE} AND ${CATF} AND outlet_type != 'dc' AND week_start BETWEEN {from:Date} AND {to:Date}
GROUP BY categ_id
LIMIT 500`),
  // Jumlah p10/p90 per SKU melebar dibanding kuantil total sebenarnya (kuantil tidak aditif): pita ditampilkan sebagai batas konservatif.
  sm_fc_next: q(`SELECT forecast_date, toFloat64(sum(yhat)) AS yhat_sum, toFloat64(sum(p10)) AS p10_sum, toFloat64(sum(p90)) AS p90_sum, max(generated_at) AS gen_at
FROM ${V}forecast_daily
WHERE ${SCOPE} AND ${CATF} AND forecast_date BETWEEN {today:Date} AND {until:Date}
GROUP BY forecast_date
ORDER BY forecast_date
LIMIT 60`),

  // ---------- G51 B4: umur simpan & sisa umur lot (/category, /store) ----------
  // Semua lot berstok (v_sm_lot_stock, keputusan lead 21 Nov), bukan hanya lot beralert; lot terdekat kedaluwarsa per produk x outlet.
  sm_shelf_lots: q(`SELECT product_id, warehouse_id, min(days_left) AS days_min, argMin(lot_name, days_left) AS lot_nearest,
  argMin(expiration_date, days_left) AS exp_nearest, argMin(quantity, days_left) AS qty_nearest, argMin(value_cost, days_left) AS value_nearest,
  toUInt64(count()) AS lots_n, toFloat64(sum(quantity)) AS qty_total
FROM ${V}lot_stock
WHERE ${SCOPE} AND ${CATF} AND quantity > 0
GROUP BY product_id, warehouse_id
ORDER BY days_min ASC, value_nearest DESC
LIMIT 300`),
  sm_shelf_products: q(`SELECT product_id, any(shelf_life_days) AS shelf_days, any(category) AS category_name, any(product_name) AS name_text
FROM ${V}product_list
WHERE ${CO} AND product_id IN {ids:Array(UInt32)}
GROUP BY product_id
LIMIT 300`),

  // ---------- G51 B5: distribusi DC -> outlet SMD (/distribution). Fill rate memakai sm_dc_fill_sums / sm_dc_fill_daily / sm_dc_fill_outlets ----------
  sm_dist_late_outlets: q(`SELECT warehouse_id, toUInt64(uniqExactIf(proposal_id, on_time = 0)) AS late_n, toUInt64(uniqExact(proposal_id)) AS deliveries_n
FROM ${V}dc_fill_rate
WHERE ${SCOPE} AND qty_approved > 0 AND ${FD_CUR}
GROUP BY warehouse_id
LIMIT 300`),
  sm_dist_short_products: q(`SELECT product_id, toFloat64(sum(greatest(qty_approved - qty_basis, 0))) AS short_sum, toFloat64(sum(qty_approved)) AS approved_sum,
  toUInt64(countIf(qty_basis < qty_approved)) AS short_lines
FROM ${V}dc_fill_rate
WHERE ${SCOPE} AND qty_approved > 0 AND ${FD_CUR}
GROUP BY product_id
HAVING short_sum > 0
ORDER BY short_sum DESC
LIMIT 10`),
  sm_dist_proposals: q(`SELECT state, toUInt64(count()) AS proposals_n, toFloat64(sum(qty_proposed_total)) AS proposed_sum, toFloat64(sum(qty_approved_total)) AS approved_sum,
  toUInt64(sum(lines_excluded)) AS excluded_n, toUInt64(sum(lines_changed)) AS changed_n
FROM ${V}replenishment
WHERE ${SCOPE} AND ${FD_CUR}
GROUP BY state
ORDER BY proposals_n DESC
LIMIT 10`),
  sm_dist_reduced_products: q(`SELECT product_id, toUInt64(countIf(excluded = 1)) AS excluded_n, toFloat64(sum(qty_proposed)) AS proposed_sum, toFloat64(sum(qty_approved)) AS approved_sum
FROM ${V}replenishment_line
WHERE ${SCOPE} AND ${FD_CUR} AND (excluded = 1 OR qty_approved < qty_proposed)
GROUP BY product_id
ORDER BY excluded_n DESC, proposed_sum DESC
LIMIT 10`),
} satisfies Record<string, SmQueryDef>;

export type SmQueryName = keyof typeof SM_QUERIES;
