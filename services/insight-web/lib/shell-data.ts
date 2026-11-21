/**
 * Data cangkang (layout): opsi filter dan kesegaran data. Kegagalan ClickHouse TIDAK menjatuhkan layout:
 * opsi kosong + kesegaran "tidak diketahui" (status Perlu dicek, bukan "baik").
 */
import 'server-only';
import { brandName, codeName } from './brands';
import { esbCodesOf } from './claims';
import { chQuery, chQuerySm } from './clickhouse';
import { addDays, wibYesterday } from './dates';
import type { FilterOptions } from '@/components/FilterBar';
import { log } from './log';
import type { Session } from './session';
import { smdBranchOptions } from './smd-merge';

export interface FreshnessRow {
  brand: string;
  entity: string;
  ageHours: number;
}

export interface ShellData {
  options: FilterOptions;
  /** Umur data terburuk (jam) untuk entitas penjualan/pembayaran; null = tidak diketahui. */
  ageHours: number | null;
  freshness: FreshnessRow[];
  yesterday: string;
  defaults: { from: string; to: string };
}

/**
 * `smd` = sesi boleh memilih SMD (lib/sm-personas.ts smdAllowed): daftar outlet SMD dimuat SATU query katalog (`sm_warehouses`) agar
 * pilihan Cabang muncul begitu chip SMD dicentang (draft di klien). Query data halaman tetap hanya bila SMD terpilih.
 */
export async function loadShellData(session: Session, now: Date = new Date(), smd = false): Promise<ShellData> {
  const yesterday = wibYesterday(now);
  const defaults = { from: addDays(yesterday, -29), to: yesterday };
  const base = { pt: esbCodesOf(session.codes) as string[] };
  const range = { from: addDays(yesterday, -365), to: yesterday };
  const outlets = session.wids.filter((w) => w > 0);

  // Sesi exec ber-SMD tanpa PT F&B: tidak ada query ESB sama sekali (chQuery menolak sesi tanpa kode ESB).
  const hasEsb = base.pt.length > 0;
  function none<T>(): Promise<T[]> {
    return Promise.resolve([] as T[]);
  }
  const [branches, categories, products, fresh, smWh] = await Promise.allSettled([
    hasEsb ? chQuery<{ brand: string; company_code: string; branch_id: number; branch_name: string; active: number }>('list_branches', session, base) : none<{ brand: string; company_code: string; branch_id: number; branch_name: string; active: number }>(),
    hasEsb ? chQuery<{ menu_category: string }>('list_categories', session, { ...base, ...range }) : none<{ menu_category: string }>(),
    hasEsb ? chQuery<{ brand: string; product_code: string; product_label: string }>('list_products', session, base) : none<{ brand: string; product_code: string; product_label: string }>(),
    hasEsb ? chQuery<{ brand: string; entity: string; age_hours: number; last_day: string }>('dq_freshness', session, base) : none<{ brand: string; entity: string; age_hours: number; last_day: string }>(),
    smd && outlets.length > 0 ? chQuerySm<Record<string, unknown>>('sm_warehouses', session, { cids: session.cids, whs: outlets }) : Promise.resolve([]),
  ]);
  for (const r of [branches, categories, products, fresh, smWh]) {
    if (r.status === 'rejected') log('warn', 'shell_data', { failed: true });
  }

  const seen = new Set<string>();
  const branchOpts: FilterOptions['branches'] = [];
  if (branches.status === 'fulfilled') {
    for (const b of branches.value) {
      const value = `${b.brand}:${b.branch_id}`;
      if (seen.has(value)) continue;
      seen.add(value);
      branchOpts.push({
        value,
        label: Number(b.active) === 0 ? `${b.branch_name} (nonaktif)` : b.branch_name,
        group: codeName(b.company_code),
        code: b.company_code,
      });
    }
  }

  if (smWh.status === 'fulfilled') branchOpts.push(...smdBranchOptions(smWh.value as Record<string, unknown>[]));

  const freshness: FreshnessRow[] =
    fresh.status === 'fulfilled'
      ? fresh.value.map((r) => ({ brand: r.brand, entity: r.entity, ageHours: Number(r.age_hours) })).filter((r) => Number.isFinite(r.ageHours))
      : [];
  const core = freshness.filter((r) => r.entity === 'sales' || r.entity === 'payment');
  const basis = core.length > 0 ? core : freshness;
  const ageHours = basis.length > 0 ? Math.max(...basis.map((r) => r.ageHours)) : null;

  return {
    options: {
      branches: branchOpts,
      categories: categories.status === 'fulfilled' ? categories.value.map((c) => c.menu_category).filter((c) => c !== '') : [],
      products:
        products.status === 'fulfilled'
          ? products.value.map((p) => ({ value: `${p.brand}:${p.product_code}`, label: p.product_label, group: brandName(p.brand) }))
          : [],
    },
    ageHours,
    freshness,
    yesterday,
    defaults,
  };
}
