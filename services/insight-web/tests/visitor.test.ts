/**
 * V-01 (23 Nov 2026): halaman Pengunjung lintas PT. Logika murni (hak akses, navigasi, filter, view-model), cakupan bertanda
 * tangan, dan aturan statis katalog query v_vis_*.
 */
import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { ParamError } from '@/lib/ch-params';
import { buildVisSearchParams } from '@/lib/ch-params-visitor';
import { VIS_PARAM_TYPES, VIS_QUERIES, type VisParamName, type VisQueryName } from '@/lib/queries-visitor';
import { scopeSettingsVisitor } from '@/lib/scope';
import { K_ANON } from '@/lib/sm-defaults';
import type { NavGroup } from '@/lib/sm-personas';
import {
  dailySeries,
  pageLabel,
  parseVisitorFilters,
  toProductConversion,
  visitorCodes,
  visitorHref,
  withVisitorNav,
  VISITOR_NAV,
} from '@/lib/visitor';
import { KEY } from './helpers';

const NOW = new Date('2026-11-23T05:00:00Z'); // 23 Nov WIB -> kemarin 22 Nov

describe('visitorCodes (hak akses = aturan sesi yang ada)', () => {
  it('PT F&B tingkat PT: semua kode ESB sesi, tanpa bergantung flag F&B persona', () => {
    expect(visitorCodes({ codes: ['PB'], roles: ['user'], brs: [] }, false)).toEqual(['PB']);
    expect(visitorCodes({ codes: ['BCF', 'PB'], roles: ['all_brands'] }, true)).toEqual(['BCF', 'PB']);
  });
  it('kepala toko F&B ber-cabang (brs) tidak melihat data tingkat PT', () => {
    expect(visitorCodes({ codes: ['PB'], roles: ['store', 'user'], brs: ['pb:7'] }, true)).toEqual([]);
  });
  it('SMD hanya bila INSIGHT_SM_ENABLED dan persona category/exec/marketing', () => {
    expect(visitorCodes({ codes: ['SMD'], roles: ['marketing', 'user'] }, true)).toEqual(['SMD']);
    expect(visitorCodes({ codes: ['SMD'], roles: ['exec', 'user'] }, true)).toEqual(['SMD']);
    expect(visitorCodes({ codes: ['SMD'], roles: ['store', 'user'] }, true)).toEqual([]);
    expect(visitorCodes({ codes: ['SMD'], roles: ['dc', 'user'] }, true)).toEqual([]);
    expect(visitorCodes({ codes: ['SMD'], roles: ['marketing', 'user'] }, false)).toEqual([]);
  });
  it('sesi campuran demo (PB + SMD exec): keduanya, urut naik', () => {
    expect(visitorCodes({ codes: ['PB', 'SMD'], roles: ['exec', 'user'], brs: [] }, true)).toEqual(['PB', 'SMD']);
  });
});

describe('withVisitorNav', () => {
  const summary: NavGroup = { id: 'summary', title: 'Ringkasan', items: [{ href: '/', label: 'Ikhtisar', icon: 'home' }] };
  const sm: NavGroup = { id: 'sm', title: 'Supermarket', items: [{ href: '/marketing', label: 'Marketing', icon: 'megaphone' }] };
  it('menambah Pengunjung di akhir grup Ringkasan tanpa mengubah grup lain', () => {
    const g = withVisitorNav([summary, sm], { codes: ['PB'], roles: ['user'] }, true);
    expect(g[0].items.map((i) => i.href)).toEqual(['/', '/visitors']);
    expect(g[1]).toBe(sm);
    expect(summary.items).toHaveLength(1); // input tidak dimutasi
  });
  it('sesi tanpa Ringkasan (marketing SMD) mendapat grup Ringkasan berisi Pengunjung saja', () => {
    const g = withVisitorNav([sm], { codes: ['SMD'], roles: ['marketing', 'user'] }, true);
    expect(g.map((x) => x.id)).toEqual(['summary', 'sm']);
    expect(g[0].items).toEqual([VISITOR_NAV]);
  });
  it('tanpa kode pengunjung: navigasi tidak berubah', () => {
    expect(withVisitorNav([sm], { codes: ['SMD'], roles: ['store', 'user'] }, true)).toEqual([sm]);
    expect(withVisitorNav([], { codes: ['PB'], roles: ['store', 'user'], brs: ['pb:7'] }, true)).toEqual([]);
  });
});

describe('parseVisitorFilters', () => {
  it('bawaan: 30 hari s.d. kemarin WIB, semua kode sesi, semua kanal', () => {
    const { filters, adjusted } = parseVisitorFilters({}, ['PB', 'SMD'], NOW);
    expect(filters).toMatchObject({ from: '2026-10-24', to: '2026-11-22', codes: ['PB', 'SMD'], channel: '' });
    expect(filters.prev).toEqual({ from: '2026-09-24', to: '2026-10-23' });
    expect(adjusted).toBe(false);
  });
  it('pt dipotong ke kode sesi; kode lain -> adjusted, tidak pernah lolos', () => {
    const r = parseVisitorFilters({ pt: 'smd,WTZ' }, ['PB', 'SMD'], NOW);
    expect(r.filters.codes).toEqual(['SMD']);
    expect(r.adjusted).toBe(true);
    const all = parseVisitorFilters({ pt: 'WTZ' }, ['PB'], NOW);
    expect(all.filters.codes).toEqual(['PB']);
  });
  it('tanggal/kanal tidak sah -> bawaan + adjusted', () => {
    for (const raw of [
      { from: '2026-11-01', to: '2026-11-23' }, // hari ini belum lengkap
      { from: '2026-11-10', to: '2026-11-01' },
      { from: '2025-01-01', to: '2026-11-01' },
      { channel: 'pos' },
    ]) {
      const r = parseVisitorFilters(raw, ['PB'], NOW);
      expect(r.adjusted, JSON.stringify(raw)).toBe(true);
      expect(r.filters.to).toBe('2026-11-22');
      expect(r.filters.channel).toBe('');
    }
    expect(parseVisitorFilters(new URLSearchParams('channel=app&from=2026-11-01&to=2026-11-07'), ['PB'], NOW).filters).toMatchObject({
      channel: 'app',
      from: '2026-11-01',
      to: '2026-11-07',
    });
  });
  it('visitorHref membawa tanggal, PT, kanal', () => {
    expect(visitorHref({ from: '2026-11-01', to: '2026-11-07' }, ['PB'], 'web')).toBe('/visitors?from=2026-11-01&to=2026-11-07&pt=PB&channel=web');
    expect(visitorHref({ from: '2026-11-01', to: '2026-11-07' }, [], '')).toBe('/visitors?from=2026-11-01&to=2026-11-07');
  });
});

describe('view-model', () => {
  it('toProductConversion: konversi = beli / perangkat melihat; nama kosong -> Produk #id', () => {
    const p = toProductConversion({ product_id: 7, name_text: '', code_text: 'PB', views_n: 40, viewers_n: 20, carts_n: 5, buys_n: 2 });
    expect(p).toMatchObject({ productId: 7, name: 'Produk #7', code: 'PB', views: 40, conversionPct: 10 });
    expect(toProductConversion({ product_id: 1, viewers_n: 0 }).conversionPct).toBeNull();
  });
  it('dailySeries mengisi hari kosong dengan 0 dan mengabaikan hari di luar rentang', () => {
    const s = dailySeries(
      [
        { day: '2026-11-02', company_code: 'PB', visitors_n: 5 },
        { day: '2026-11-09', company_code: 'PB', visitors_n: 99 },
        { day: '2026-11-01', company_code: 'SMD', visitors_n: 3 },
      ],
      ['2026-11-01', '2026-11-02'],
      'visitors_n',
    );
    expect(s.get('PB')).toEqual([0, 5]);
    expect(s.get('SMD')).toEqual([3, 0]);
  });
  it('label halaman', () => {
    expect(pageLabel('product')).toBe('Detail produk');
    expect(pageLabel('https://x')).toBe('Lainnya');
  });
});

describe('scopeSettingsVisitor', () => {
  it('vcodes urut naik + vsig = sha256(KEY|visitor|vcodes)', () => {
    const s = scopeSettingsVisitor(['SMD', 'PB', 'PB'], KEY);
    expect(s.SQL_efn_vcodes).toBe('PB,SMD');
    expect(s.SQL_efn_vsig).toBe(createHash('sha256').update(`${KEY}|visitor|PB,SMD`).digest('hex'));
  });
  it('menolak kosong / kode tak dikenal', () => {
    expect(() => scopeSettingsVisitor([], KEY)).toThrow();
    expect(() => scopeSettingsVisitor(['XYZ'], KEY)).toThrow();
    expect(() => scopeSettingsVisitor(['PB|visitor|PB'], KEY)).toThrow();
  });
});

const names = Object.keys(VIS_QUERIES) as VisQueryName[];
const PLACEHOLDER = /\{(\w+):([A-Za-z0-9()]+)\}/g;
const VIEWS = ['page_daily', 'funnel_daily', 'product_daily', 'search_daily'];

describe('katalog query Pengunjung', () => {
  it.each(names)('%s: placeholder = params terdeklarasi bertipe, tanpa interpolasi, tanpa titik koma', (n) => {
    const def = VIS_QUERIES[n];
    const found = new Map<string, string>();
    for (const m of def.sql.matchAll(PLACEHOLDER)) found.set(m[1], m[2]);
    expect(Array.from(found.keys()).sort()).toEqual([...def.params].sort());
    for (const [p, t] of found) expect(VIS_PARAM_TYPES[p as VisParamName]).toBe(t);
    expect(def.sql).not.toContain('${');
    expect(def.sql.trim().endsWith(';')).toBe(false);
  });
  it.each(names)('%s: hanya view efn_insight.v_vis_*, lapis 1 company_code, tanpa subject_key', (n) => {
    const sql = VIS_QUERIES[n].sql;
    const tables = Array.from(sql.matchAll(/(?:FROM|JOIN)\s+([\w.]+)/g)).map((m) => m[1]);
    expect(tables.length).toBeGreaterThan(0);
    for (const t of tables) {
      expect(t).toMatch(/^efn_insight\.v_vis_[a-z_]+$/);
      expect(VIEWS).toContain(t.replace('efn_insight.v_vis_', ''));
    }
    expect(sql).toContain('company_code IN {codes:Array(String)}');
    expect(sql).not.toMatch(/subject_key|fact_visitor_event/);
  });
  it.each(names)('%s: LIMIT literal bila GROUP BY/ORDER BY', (n) => {
    const sql = VIS_QUERIES[n].sql;
    expect(sql).not.toMatch(/LIMIT\s*\{/);
    if (/GROUP BY|ORDER BY/.test(sql)) expect(sql).toMatch(/LIMIT \d+\s*$/);
  });
  it('k-anonim: produk dan kata kunci hanya bila >= K_ANON perangkat', () => {
    expect(K_ANON).toBe(10);
    expect(VIS_QUERIES.vis_products.sql).toContain(`HAVING viewers_n >= ${K_ANON}`);
    expect(VIS_QUERIES.vis_search_top.sql).toContain(`HAVING subjects_n >= ${K_ANON}`);
    expect(VIS_QUERIES.vis_search_none.sql).toContain(`HAVING subjects_n >= ${K_ANON} AND no_result_n > 0`);
  });
  it('alias agregat tidak sama dengan kolom view', () => {
    const clash = /\b(?:sum|sumIf|any|count|countIf|max|maxIf|min)\([^)]*\)\)?\s+AS\s+(day|company_code|channel|page|visitors|views|viewers|carters|checkout_starters|purchasers|product_id|product_name|cart_adds|purchases|query|searches|no_result|subjects)\b/;
    for (const n of names) expect(clash.test(VIS_QUERIES[n].sql), n).toBe(false);
  });
});

describe('buildVisSearchParams', () => {
  it('setting cakupan vcodes/vsig + hanya param terdeklarasi; param wajib hilang -> ParamError', () => {
    const scope = scopeSettingsVisitor(['PB', 'SMD'], KEY);
    const sp = buildVisSearchParams('vis_pages', scope, { from: '2026-11-01', to: '2026-11-07', codes: ['PB'], channel: '', prev_from: '2026-10-25' });
    expect(sp.get('SQL_efn_vcodes')).toBe('PB,SMD');
    expect(sp.get('SQL_efn_vsig')).toBe(scope.SQL_efn_vsig);
    expect(sp.get('param_codes')).toBe("['PB']");
    expect(sp.get('param_channel')).toBe('');
    expect(sp.get('param_prev_from')).toBeNull(); // berlebih diabaikan
    expect(sp.get('database')).toBe('efn_insight');
    expect(sp.get('SQL_efn_sig3')).toBeNull();
    expect(() => buildVisSearchParams('vis_pages', scope, { from: '2026-11-01', to: '2026-11-07', codes: ['PB'] })).toThrow(ParamError);
    expect(() => buildVisSearchParams('vis_pages', scope, { from: '2026-11-01', to: '2026-11-07', codes: ['PB'], channel: "web' OR 1=1" })).toThrow(
      ParamError,
    );
  });
});
