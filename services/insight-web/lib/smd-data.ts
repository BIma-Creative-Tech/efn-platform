/**
 * Pemuat SMD untuk halaman F&B (G51 B1; server-only). Query supermarket lewat `chQuerySm` dengan cakupan sig2 dari SESI
 * (tidak pernah dari input browser); filter outlet dari `PtSelection.wh` yang sudah divalidasi ⊆ wids sesi. Halaman hanya
 * memanggil ini bila `smdActive(...)` benar, jadi sesi tanpa exec / SMD tidak dipilih tidak mengirim query SM sama sekali.
 */
import 'server-only';
import type { ParamBag } from './ch-params';
import type { SmParamBag } from './ch-params-sm';
import { chQuerySm } from './clickhouse';
import { addDays, wibToday } from './dates';
import type { PtSelection } from './filters';
import { run, settle, TABLE_LOADERS, type Ctx, type Result, type Row } from './loaders';
import type { QueryName } from './queries';
import type { SmQueryName } from './queries-sm';
import type { TableName } from './table-defs';
import { toNum, type Raw } from './sm-parse';

/** Jendela forecast ke depan: hari ini + 13 hari = 14 hari. */
export const FORECAST_DAYS = 14;

export function smdBag(ctx: Ctx, sel: PtSelection, extra: SmParamBag = {}, now: Date = new Date()): SmParamBag {
  const outlets = ctx.session.wids.filter((w) => w > 0);
  const today = wibToday(now);
  return {
    cids: ctx.session.cids,
    from: ctx.filters.from,
    to: ctx.filters.to,
    prev_from: ctx.prev.from,
    prev_to: ctx.prev.to,
    today,
    until: addDays(today, FORECAST_DAYS - 1),
    wh: sel.wh.length > 0 ? sel.wh : outlets,
    whs: outlets,
    cat: [],
    ids: [],
    ...extra,
  };
}

export function runSm(name: SmQueryName, ctx: Ctx, sel: PtSelection, extra?: SmParamBag): Promise<Result<Raw[]>> {
  return settle(chQuerySm<Raw>(name, ctx.session, smdBag(ctx, sel, extra), ctx.pool));
}

function EMPTY<T>(): Promise<Result<T[]>> {
  return Promise.resolve<Result<T[]>>({ ok: true, data: [] });
}

/** Query ESB hanya bila brand ESB terpilih (`on`); selain itu hasil kosong tanpa memanggil ClickHouse. */
export function runEsb<T>(on: boolean, name: QueryName, ctx: Ctx, extra: ParamBag = {}): Promise<Result<T[]>> {
  return on ? settle(run<T>(name, ctx, extra)) : EMPTY<T>();
}

export function loadEsbTable(on: boolean, name: TableName, ctx: Ctx): Promise<Result<Row[]>> {
  return on ? settle(TABLE_LOADERS[name](ctx)) : EMPTY<Row>();
}

/** Nama outlet (dan opsional kategori) SMD untuk label; gagal -> peta kosong (label jatuh ke "Outlet #id"). */
export async function loadSmdNames(
  ctx: Ctx,
  sel: PtSelection,
  withCats = false,
): Promise<{ wh: Map<number, string>; cats: Map<number, string> }> {
  const [whs, cats] = await Promise.all([
    runSm('sm_warehouses', ctx, sel),
    withCats ? runSm('sm_categories', ctx, sel) : Promise.resolve<Result<Raw[]>>({ ok: true, data: [] }),
  ]);
  const wh = new Map<number, string>();
  if (whs.ok) {
    for (const r of whs.data) {
      const id = toNum(r.warehouse_id);
      if (id > 0 && typeof r.warehouse_name === 'string' && r.warehouse_name !== '') wh.set(id, r.warehouse_name);
    }
  }
  const cm = new Map<number, string>();
  if (cats.ok) {
    for (const r of cats.data) {
      const id = toNum(r.categ_id);
      if (id > 0 && typeof r.category_name === 'string' && r.category_name !== '') cm.set(id, r.category_name);
    }
  }
  return { wh, cats: cm };
}

/** Nama produk SMD untuk id terbatas (maks 200, pola sm_product_names). */
export async function loadSmdProductNames(ctx: Ctx, sel: PtSelection, ids: readonly number[]): Promise<Map<number, string>> {
  const uniq = Array.from(new Set(ids.filter((n) => n > 0))).slice(0, 200);
  const out = new Map<number, string>();
  if (uniq.length === 0) return out;
  const res = await runSm('sm_product_names', ctx, sel, { ids: uniq });
  if (res.ok) {
    for (const r of res.data) {
      out.set(toNum(r.product_id), typeof r.name_text === 'string' && r.name_text !== '' ? r.name_text : 'Produk tanpa nama');
    }
  }
  return out;
}
