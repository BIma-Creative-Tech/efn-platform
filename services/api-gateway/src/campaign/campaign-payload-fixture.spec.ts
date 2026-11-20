import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { parseCampaignPayload, parseClaim } from './odoo-campaign';

/**
 * SEC-G50-02 (keputusan lead): kontrak payload `efn.campaign/efn_campaign_claim` → BFF = parser BFF
 * (`parseCampaignPayload`). Contoh normatif = fixture BERSAMA yang juga dibaca test Odoo `efn_campaign`:
 *   services/odoo/addons/efn_campaign/tests/fixtures/campaign_payload_v1.json
 * CI men-checkout seluruh repo → path relatif dari root repo. File hilang = test GAGAL (bukan dilewati).
 *
 * Bentuk fixture yang dibaca (Odoo boleh menambah key lain; diabaikan):
 * {
 *   "schema_version": 1,
 *   "company_code": "SMD",                      // PT pemilik API key (default "SMD" bila tidak ada)
 *   "cases":   [{ "name": "...", "approval_rev": 0?, "payload": {…payload outbox…},
 *                 "expected"?: { "codes"?: [...], "payload"?: {…subset CampaignPayload…} } }],
 *   "invalid"?: [{ "name": "...", "payload": {…}, "reason": "<kode Invalid parser, mis. company_mismatch>" }]
 * }
 * Juga diterima: array kasus di tingkat atas.
 */
const REPO_ROOT = join(__dirname, '..', '..', '..', '..');
const FIXTURE = join(REPO_ROOT, 'services', 'odoo', 'addons', 'efn_campaign', 'tests', 'fixtures', 'campaign_payload_v1.json');

interface FixtureCase {
  name?: string;
  approval_rev?: number;
  payload: Record<string, unknown>;
  expected?: { codes?: string[]; payload?: Record<string, unknown> };
}
interface FixtureInvalid {
  name?: string;
  payload: Record<string, unknown>;
  reason?: string;
}

function loadFixture(): { company: string; cases: FixtureCase[]; invalid: FixtureInvalid[] } {
  if (!existsSync(FIXTURE)) {
    throw new Error(`Fixture bersama kontrak kampanye tidak ditemukan: ${FIXTURE} (SEC-G50-02; dibuat bersama perbaikan Odoo efn_campaign)`);
  }
  const raw = JSON.parse(readFileSync(FIXTURE, 'utf8')) as unknown;
  if (Array.isArray(raw)) return { company: 'SMD', cases: raw as FixtureCase[], invalid: [] };
  const o = raw as {
    company_code?: string;
    cases?: FixtureCase[];
    examples?: Record<string, Record<string, unknown>>;
    invalid?: FixtureInvalid[];
  };
  // Bentuk yang ditulis Odoo (tests/fixtures/campaign_payload_v1.json): `examples` = { <nama>: <payload> }.
  const fromExamples: FixtureCase[] = Object.entries(o.examples ?? {}).map(([name, payload]) => ({ name, payload }));
  const cases = [...(o.cases ?? []), ...fromExamples];
  const company = o.company_code ?? (cases[0]?.payload.company_code as string | undefined) ?? 'SMD';
  return { company, cases, invalid: o.invalid ?? [] };
}

/** Key `CampaignPayload` yang diteruskan parser apa adanya (selain normalisasi kecil yang diuji terpisah). */
const PASS_THROUGH = ['campaign_id', 'company_code', 'kind', 'segment_code', 'channels', 'voucher_mode', 'voucher_program_ref', 'voucher_value_idr', 'budget_idr', 'cap_per_member_week', 'valid_from', 'valid_to'] as const;

describe('Fixture bersama payload kampanye Odoo → BFF (SEC-G50-02)', () => {
  it('fixture ada dan memuat minimal satu kasus valid per mode voucher yang dipakai', () => {
    const { cases } = loadFixture();
    expect(cases.length).toBeGreaterThan(0);
    for (const c of cases) expect(typeof c.payload).toBe('object');
  });

  it('setiap kasus valid lolos parser dan menghasilkan objek yang diharapkan', () => {
    const { company, cases } = loadFixture();
    for (const c of cases) {
      const where = c.name ?? JSON.stringify(c.payload).slice(0, 80);
      let parsed: ReturnType<typeof parseCampaignPayload>;
      try {
        parsed = parseCampaignPayload(c.payload, company);
      } catch (err) {
        throw new Error(`kasus fixture "${where}" ditolak parser BFF: ${err instanceof Error ? err.message : String(err)}`);
      }
      const p = c.payload;
      // Pemetaan implisit: field kanonik diteruskan tanpa perubahan
      for (const k of PASS_THROUGH) {
        if (p[k] !== undefined) expect({ where, k, v: (parsed.payload as unknown as Record<string, unknown>)[k] }).toEqual({ where, k, v: p[k] ?? null });
      }
      expect({ where, run: parsed.payload.segment_run_id }).toEqual({ where, run: typeof p.segment_run_id === 'string' ? p.segment_run_id.toLowerCase() : null });
      expect({ where, message: parsed.payload.message }).toEqual({ where, message: p.message });
      const expectedCodes =
        p.voucher_mode === 'anonymous_code' ? [...new Set(p.voucher_codes as string[])] : p.voucher_mode === 'public_code' ? [p.public_code as string] : [];
      expect({ where, codes: parsed.codes }).toEqual({ where, codes: c.expected?.codes ?? expectedCodes });
      // Payload antrean tidak pernah memuat kode voucher / data member
      const serialized = JSON.stringify(parsed.payload);
      for (const code of parsed.codes) expect({ where, leaked: serialized.includes(code) }).toEqual({ where, leaked: false });
      expect(serialized).not.toContain('partner_id');
      if (c.expected?.payload) expect(parsed.payload).toMatchObject(c.expected.payload);
    }
  });

  it('kasus valid juga lolos lewat bentuk respons klaim `{items:[{id, approval_rev, payload}]}`', () => {
    const { company, cases } = loadFixture();
    const res = parseClaim({ items: cases.slice(0, 20).map((c, i) => ({ id: i + 1, approval_rev: c.approval_rev ?? (c.payload.approval_rev as number) ?? 0, payload: c.payload })) }, company);
    expect(res.rejected).toEqual([]);
    expect(res.items).toHaveLength(Math.min(20, cases.length));
  });

  it('kasus invalid (bila ada) ditolak dengan kode yang diharapkan', () => {
    const { company, invalid } = loadFixture();
    for (const c of invalid) {
      const where = c.name ?? 'invalid';
      let reason: string | null = null;
      try {
        parseCampaignPayload(c.payload, company);
      } catch (err) {
        reason = err instanceof Error ? err.message : String(err);
      }
      expect({ where, rejected: reason !== null }).toEqual({ where, rejected: true });
      if (c.reason) expect({ where, reason }).toEqual({ where, reason: c.reason });
    }
  });
});
