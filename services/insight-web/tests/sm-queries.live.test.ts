// G47 M-6 (opt-in): jalankan SELURUH SM_QUERIES lewat jalur insight-web (param + scope sig2) ke ClickHouse 24.8 dengan
// skema + access + row policy SM (kunci sintetis "ab"x32, user efn_insight). Dilewati bila CH_LIVE_URL tidak diset.
// Menangkap kolom yang tidak ada di view (mis. product_tmpl_id di v_sm_price_signal) yang lolos cek statis.
import { describe, expect, it } from 'vitest';
import { buildSmSearchParams, type SmParamBag } from '@/lib/ch-params-sm';
import { SM_PARAM_TYPES, SM_QUERIES, type SmParamName, type SmQueryName } from '@/lib/queries-sm';
import { scopeSettingsSm } from '@/lib/scope';
import type { Session } from '@/lib/session';

const URL_CH = process.env.CH_LIVE_URL;
const KEY = 'ab'.repeat(32);
const session: Pick<Session, 'cids' | 'codes' | 'roles' | 'wids' | 'cats'> = {
  cids: [11],
  codes: ['SMD'],
  roles: ['category', 'dc', 'exec', 'marketing', 'store', 'user'],
  wids: [0, 20, 21, 22],
  cats: [4, 9],
};

function bag(): SmParamBag {
  const b: SmParamBag = {};
  for (const [k, t] of Object.entries(SM_PARAM_TYPES) as [SmParamName, string][]) {
    if (t === 'Date') b[k] = k.startsWith('prev') ? '2026-10-01' : k.startsWith('yoy') ? '2025-11-01' : '2026-11-10';
    else if (t === 'Array(UInt32)') b[k] = k === 'cids' ? [11] : k === 'whz' ? [0, 20, 21] : [20, 21];
    else if (t === 'String') b[k] = 'pos';
    else b[k] = 7;
  }
  b.from = '2026-11-01';
  return b;
}

describe.skipIf(!URL_CH)('SM_QUERIES live di ClickHouse', () => {
  for (const name of Object.keys(SM_QUERIES) as SmQueryName[]) {
    it(name, async () => {
      const sp = buildSmSearchParams(name, scopeSettingsSm(session, KEY), bag());
      const res = await fetch(`${URL_CH}/?${sp.toString()}`, {
        method: 'POST',
        body: SM_QUERIES[name].sql,
        headers: { 'X-ClickHouse-User': 'efn_insight', 'X-ClickHouse-Key': 'g47-local-test' },
      });
      const text = await res.text();
      expect(res.status, text.slice(0, 600)).toBe(200);
    });
  }
});
