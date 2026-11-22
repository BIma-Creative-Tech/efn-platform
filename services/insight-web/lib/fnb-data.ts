/**
 * Pintu masuk halaman persona F&B (server-only; docs/04 §28.15.9, G52): lapis kedua pemeriksaan akses (setelah proxy.ts),
 * validasi filter dari URL, dan konteks query. Penolakan = redirect ke halaman informasi ramah (bukan 404).
 * Semua query lewat katalog bernama (lib/queries.ts `fexec_*`/`fstore_*`/`fmkt_*`) dan cakupan dari SESI (sig3), bukan dari URL.
 */
import 'server-only';
import { redirect } from 'next/navigation';
import { requireSession } from './auth';
import { esbCodesOf } from './claims';
import { addDays, addYears, startOfMonthShift, wibYesterday } from './dates';
import { fnbRouteDenial, FNB_DENIAL_PATH, FNB_ROUTES, type FnbPersona } from './fnb-personas';
import { pickStoreDay, parseStoreBranches, type DailyRow } from './fnb-store';
import { parseFilters, type RawParams } from './filters';
import { run, settle, num, type Ctx, type Result } from './loaders';
import type { ParamBag } from './ch-params';
import type { QueryName } from './queries';
import { getConfig } from './runtime';

export type FnbSearchParams = Promise<Record<string, string | string[] | undefined>>;

async function guard(persona: FnbPersona) {
  const session = await requireSession();
  const denial = fnbRouteDenial(FNB_ROUTES[persona], session, getConfig().fnbEnabled);
  if (denial) redirect(FNB_DENIAL_PATH[denial]);
  return session;
}

export interface FnbRangePage {
  ctx: Ctx;
  adjusted: boolean;
  /** Parameter tambahan G52 (pembanding tahun lalu + awal 13 bulan); selalu diteruskan ke `q`. */
  extra: ParamBag;
  yoy: { from: string; to: string };
  /** Jalankan query katalog F&B dengan filter halaman; kegagalan satu blok = Result gagal (bukan lempar). */
  q: <T>(name: QueryName, more?: ParamBag) => Promise<Result<T[]>>;
}

/** Eksekutif dan Marketing: filter global (tanggal, PT, cabang) divalidasi dari URL; SMD tidak berlaku (hanya ESB). */
export async function fnbRangePage(persona: 'exec' | 'marketing', searchParams: FnbSearchParams): Promise<FnbRangePage> {
  const session = await guard(persona);
  const raw = await searchParams;
  const { filters, prev, adjusted } = parseFilters(raw as RawParams, esbCodesOf(session.codes), new Date());
  const ctx: Ctx = { session, filters, prev };
  const yoy = { from: addYears(filters.from, -1), to: addYears(filters.to, -1) };
  const extra: ParamBag = { yoy_from: yoy.from, yoy_to: yoy.to, m_from: startOfMonthShift(filters.to, -12) };
  return { ctx, adjusted, extra, yoy, q: <T>(name: QueryName, more: ParamBag = {}) => settle(run<T>(name, ctx, { ...extra, ...more })) };
}

export interface StoreBranchOption {
  key: string;
  label: string;
  brand: string;
  companyCode: string;
}

export interface FnbStorePage {
  ctx: Ctx;
  day: string;
  yesterday: string;
  adjusted: boolean;
  /** Cabang terpilih (sudah dipotong ke `brs`); kosong = semua cabang dalam cakupan sesi. */
  selected: string[];
  /** Daftar cabang toko dalam cakupan (view kelas BR: sesi ber-cabang hanya melihat cabangnya). */
  options: Result<StoreBranchOption[]>;
  /** 14 hari berakhir di `day` (untuk grafik pekan ini vs pekan lalu). */
  daily: Result<DailyRow[]>;
  q: <T>(name: QueryName) => Promise<Result<T[]>>;
}

const first = (v: string | string[] | undefined): string | undefined => (Array.isArray(v) ? v[0] : v);

/**
 * Toko: hari acuan default = hari terakhir yang punya penjualan di cabang dalam cakupan (<= kemarin WIB), dicari dari 14 hari
 * terakhir; `branch` dipotong ke `brs` sesi (nilai di luar dibuang). Tanpa filter periode global (halaman memegang hari + cabang).
 */
export async function fnbStorePage(searchParams: FnbSearchParams): Promise<FnbStorePage> {
  const session = await guard('store');
  const raw = await searchParams;
  const now = new Date();
  const yesterday = wibYesterday(now);

  const br = parseStoreBranches(
    (Array.isArray(raw.branch) ? raw.branch : raw.branch === undefined ? [] : [raw.branch]).flatMap((s) => s.split(',')),
    session.brs,
  );
  const pt = esbCodesOf(session.codes);
  const mk = (day: string): Ctx => ({
    session,
    filters: { from: day, to: day, pt, branch: br.branch, cat: [], product: [] },
    prev: { from: addDays(day, -7), to: addDays(day, -7) },
  });

  const optionsP = settle(
    run<{ brand: string; company_code: string; branch_id: number; branch_name: string }>('fstore_branch_list', mk(yesterday)),
  ).then(
    (r): Result<StoreBranchOption[]> =>
      r.ok
        ? { ok: true, data: r.data.map((b) => ({ key: `${b.brand}:${num(b.branch_id)}`, label: b.branch_name, brand: b.brand, companyCode: b.company_code })) }
        : r,
  );
  const fetchDaily = (day: string) =>
    settle(run<DailyRow>('fstore_daily_vs_lastweek', mk(day), { ref_day: day })).then(
      (r): Result<DailyRow[]> => (r.ok ? { ok: true, data: r.data.map((d) => ({ sales_date: String(d.sales_date), net_sum: num(d.net_sum), trx: num(d.trx) })) } : r),
    );

  const requested = first(raw.day);
  const [options, dailyYesterday] = await Promise.all([optionsP, fetchDaily(yesterday)]);
  const picked = pickStoreDay(requested, dailyYesterday.ok ? dailyYesterday.data : null, yesterday);
  const daily = picked.day === yesterday ? dailyYesterday : await fetchDaily(picked.day);

  const ctx = mk(picked.day);
  return {
    ctx,
    day: picked.day,
    yesterday,
    adjusted: picked.adjusted || br.adjusted,
    selected: br.branch,
    options,
    daily,
    q: <T>(name: QueryName) => settle(run<T>(name, ctx, { ref_day: picked.day })),
  };
}
