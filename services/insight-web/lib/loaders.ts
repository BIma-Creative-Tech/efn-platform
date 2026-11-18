/**
 * Pemuat data server-only: konteks (sesi + filter), eksekusi query katalog, hasil per-blok (satu blok gagal tidak
 * menjatuhkan halaman), dan pemuat tabel yang dipakai BERSAMA oleh halaman dan ekspor CSV.
 */
import 'server-only';
import { brandName } from './brands';
import type { ParamBag } from './ch-params';
import { chQuery, ChError, type ChPool } from './clickhouse';
import type { Filters } from './filters';
import { log, newRef } from './log';
import type { QueryName } from './queries';
import type { Session } from './session';
import type { TableName } from './table-defs';
import { freshnessStatus, gapStatus, sellThroughStatus } from './thresholds';

export interface Ctx {
  session: Session;
  filters: Filters;
  prev: { from: string; to: string };
  /** 'export' = semaphore ekspor terpisah (SEC-189); bawaan 'page'. */
  pool?: ChPool;
}

export type Row = Record<string, string | number | boolean | null>;
export type Result<T> = { ok: true; data: T } | { ok: false; ref: string; reason?: 'rate_limited' };

export function bagFor(ctx: Ctx): ParamBag {
  const f = ctx.filters;
  return {
    from: f.from,
    to: f.to,
    prev_from: ctx.prev.from,
    prev_to: ctx.prev.to,
    pt: f.pt,
    branch: f.branch,
    cat: f.cat,
    product: f.product,
  };
}

export function run<T>(name: QueryName, ctx: Ctx, extra: ParamBag = {}): Promise<T[]> {
  return chQuery<T>(name, ctx.session, { ...bagFor(ctx), ...extra }, ctx.pool);
}

export async function settle<T>(p: Promise<T>): Promise<Result<T>> {
  try {
    return { ok: true, data: await p };
  } catch (err) {
    if (err instanceof ChError) {
      return err.kind === 'rate_limited' ? { ok: false, ref: err.ref, reason: 'rate_limited' } : { ok: false, ref: err.ref };
    }
    const ref = newRef();
    log('error', 'loader', { ref, kind: 'unexpected' });
    return { ok: false, ref };
  }
}

export const num = (v: unknown): number => {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
};
export const numOrNull = (v: unknown): number | null => {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const ENTITY_LABEL: Record<string, string> = {
  sales: 'Penjualan',
  payment: 'Pembayaran',
  production: 'Produksi',
  delivery: 'Pengiriman',
  transfer: 'Transfer',
  fin_sold: 'Terjual (FIN)',
};

interface BranchRow {
  brand: string;
  company_code: string;
  branch_id: number;
  branch_label: string;
}

const UNMAPPED_SUFFIX = ' (belum dipetakan)';
const productLabel = (label: string, unmapped: unknown) => {
  const base = label.startsWith('NAME:') ? label.slice(5) : label;
  return num(unmapped) === 1 ? `${base}${UNMAPPED_SUFFIX}` : base;
};

type Loader = (ctx: Ctx) => Promise<Row[]>;

/** Nama query katalog yang dipakai tiap tabel (untuk log audit ekspor, SEC-194). */
export const TABLE_QUERIES: Record<TableName, QueryName> = {
  'branch-rank': 'exec_branch_rank',
  'flow-outliers': 'flow_outliers',
  'flow-products': 'flow_by_product',
  'top-menu': 'sales_top_menu',
  'menu-mix-branch': 'menu_mix_by_branch',
  'pay-methods': 'pay_by_method',
  'pay-branches': 'pay_by_branch',
  'dist-matrix': 'dist_transfer',
  'dist-gap': 'dist_ck_gap_by_product',
  'dq-freshness': 'dq_freshness',
  'dq-alias': 'dq_alias_unmapped',
  'dq-no-sales': 'dq_store_no_sales',
  'dq-non-ea': 'dq_non_ea',
  'dq-last-run': 'dq_last_run',
};

interface DqRow {
  brand: string;
  item: string;
  value_max: number;
  last_seen: string;
}

export const TABLE_LOADERS: Record<TableName, Loader> = {
  'branch-rank': async (ctx) => {
    const rows = await run<BranchRow & { net_sales: number; trx: number; avg_trx: number | null; hpp_pct: number | null }>(
      'exec_branch_rank',
      ctx,
    );
    return rows.map((r) => ({
      branch_key: `${r.brand}:${r.branch_id}`,
      branch_label: r.branch_label,
      company_code: r.company_code,
      net_sales: num(r.net_sales),
      trx: num(r.trx),
      avg_trx: numOrNull(r.avg_trx),
      hpp_pct: numOrNull(r.hpp_pct),
    }));
  },

  'flow-outliers': async (ctx) => {
    const rows = await run<
      BranchRow & {
        product_code: string;
        product_label: string;
        unmapped: number;
        received: number;
        store_prod_qty: number;
        sold_qty: number;
        sell_through_pct: number | null;
      }
    >('flow_outliers', ctx);
    return rows.map((r) => {
      const pct = numOrNull(r.sell_through_pct);
      return {
        branch_key: `${r.brand}:${r.branch_id}`,
        branch_label: r.branch_label,
        product_key: `${r.brand}:${r.product_code}`,
        product_label: productLabel(r.product_label, r.unmapped),
        received: num(r.received),
        store_prod_qty: num(r.store_prod_qty),
        sold_qty: num(r.sold_qty),
        sell_through_pct: pct,
        status: sellThroughStatus(pct),
      };
    });
  },

  'flow-products': async (ctx) => {
    const rows = await run<{
      brand: string;
      product_code: string;
      product_label: string;
      unmapped: number;
      ck_prod_qty: number;
      ck_out_qty: number;
      received: number;
      store_prod_qty: number;
      sold_qty: number;
      sell_through_pct: number | null;
    }>('flow_by_product', ctx);
    return rows.map((r) => {
      const pct = numOrNull(r.sell_through_pct);
      return {
        product_key: `${r.brand}:${r.product_code}`,
        product_label: productLabel(r.product_label, r.unmapped),
        ck_prod_qty: num(r.ck_prod_qty),
        ck_out_qty: num(r.ck_out_qty),
        received: num(r.received),
        store_prod_qty: num(r.store_prod_qty),
        sold_qty: num(r.sold_qty),
        sell_through_pct: pct,
        status: sellThroughStatus(pct),
      };
    });
  },

  'top-menu': async (ctx) => {
    const rows = await run<{
      brand: string;
      menu_code: string;
      menu_label: string;
      category: string;
      net_sales: number;
      qty_sold: number;
      margin_pct: number | null;
    }>('sales_top_menu', ctx, { sort: 'net' });
    return rows.map(topMenuRow);
  },

  'menu-mix-branch': async (ctx) => {
    const rows = await run<{
      brand: string;
      branch_id: number;
      branch_label: string;
      menu_category: string;
      net_sales: number;
      share_pct: number | null;
    }>('menu_mix_by_branch', ctx);
    return rows.map((r) => ({
      branch_key: `${r.brand}:${r.branch_id}`,
      branch_label: r.branch_label,
      menu_category: r.menu_category,
      net_sales: num(r.net_sales),
      share_pct: numOrNull(r.share_pct),
    }));
  },

  'pay-methods': async (ctx) => {
    const rows = await run<{
      payment_type: string;
      method: string;
      cnt: number;
      amount_total: number;
      mdr_total: number;
      mdr_pct: number | null;
      avg_amount: number | null;
    }>('pay_by_method', ctx);
    return rows.map((r) => ({
      payment_type: r.payment_type === '' ? 'Tidak diketahui' : r.payment_type,
      method: r.method,
      cnt: num(r.cnt),
      amount_total: num(r.amount_total),
      mdr_total: num(r.mdr_total),
      mdr_pct: numOrNull(r.mdr_pct),
      avg_amount: numOrNull(r.avg_amount),
    }));
  },

  'pay-branches': async (ctx) => {
    const rows = await run<{
      brand: string;
      branch_id: number;
      branch_name: string;
      amount: number;
      mdr: number;
      top_method: string;
    }>('pay_by_branch', ctx);
    return rows.map((r) => ({
      // branch_id 0 = cabang pembayaran tak dikenal di master (tidak bisa dijadikan filter).
      branch_key: num(r.branch_id) > 0 ? `${r.brand}:${r.branch_id}` : null,
      branch_name: r.branch_name,
      amount: num(r.amount),
      mdr: num(r.mdr),
      top_method: r.top_method,
    }));
  },

  'dist-matrix': async (ctx) => {
    const rows = await run<
      BranchRow & {
        ck_out_qty: number;
        in_ck_qty: number;
        in_other_qty: number;
        st_in_qty: number;
        st_out_qty: number;
        st_net: number;
      }
    >('dist_transfer', ctx);
    return rows.map((r) => ({
      branch_key: `${r.brand}:${r.branch_id}`,
      branch_label: r.branch_label,
      ck_out_qty: num(r.ck_out_qty),
      in_ck_qty: num(r.in_ck_qty),
      in_other_qty: num(r.in_other_qty),
      st_in_qty: num(r.st_in_qty),
      st_out_qty: num(r.st_out_qty),
      st_net: num(r.st_net),
    }));
  },

  'dist-gap': async (ctx) => {
    const rows = await run<{
      day: string;
      brand: string;
      product_code: string;
      product_label: string;
      ck_out_qty: number;
      in_ck_qty: number;
      gap: number;
    }>('dist_ck_gap_by_product', ctx);
    return rows.map((r) => {
      const out = num(r.ck_out_qty);
      const gap = num(r.gap);
      return {
        day: r.day,
        product_key: `${r.brand}:${r.product_code}`,
        product_label: productLabel(r.product_label, r.product_code.startsWith('NAME:') ? 1 : 0),
        ck_out_qty: out,
        in_ck_qty: num(r.in_ck_qty),
        gap,
        status: gapStatus(out > 0 ? (gap / out) * 100 : null),
      };
    });
  },

  'dq-freshness': async (ctx) => {
    const rows = await run<{ brand: string; entity: string; age_hours: number; last_day: string }>('dq_freshness', ctx);
    return rows.map((r) => ({
      brand_name: brandName(r.brand),
      entity_label: ENTITY_LABEL[r.entity] ?? r.entity,
      age_hours: numOrNull(r.age_hours),
      last_day: r.last_day,
      status: freshnessStatus(numOrNull(r.age_hours)),
    }));
  },

  'dq-alias': async (ctx) => {
    const rows = await run<DqRow>('dq_alias_unmapped', ctx);
    return rows.map((r) => ({
      name: r.item.startsWith('NAME:') ? r.item.slice(5) : r.item,
      brand_name: brandName(r.brand),
      qty: num(r.value_max),
      last_seen: r.last_seen,
    }));
  },

  'dq-no-sales': async (ctx) => {
    const rows = await run<DqRow>('dq_store_no_sales', ctx);
    return rows.map((r) => ({ branch: r.item, brand_name: brandName(r.brand), last_seen: r.last_seen }));
  },

  'dq-non-ea': async (ctx) => {
    const rows = await run<DqRow>('dq_non_ea', ctx);
    return rows.map((r) => ({ item: r.item, brand_name: brandName(r.brand), rows: num(r.value_max) }));
  },

  'dq-last-run': async (ctx) => {
    const rows = await run<DqRow>('dq_last_run', ctx);
    return rows.map((r) => ({ brand_name: brandName(r.brand), item: r.item, errors: num(r.value_max) }));
  },
};

export function topMenuRow(r: {
  brand: string;
  menu_label: string;
  category: string;
  net_sales: number;
  qty_sold: number;
  margin_pct: number | null;
}): Row {
  return {
    menu_label: r.menu_label,
    category: r.category === '' ? 'Tanpa kategori' : r.category,
    brand_name: brandName(r.brand),
    net_sales: num(r.net_sales),
    qty_sold: num(r.qty_sold),
    margin_pct: numOrNull(r.margin_pct),
  };
}
