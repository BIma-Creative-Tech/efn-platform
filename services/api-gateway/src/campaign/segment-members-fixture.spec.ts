import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { FakeDb } from '../common/testing/fakes';
import { SegmentIngestService } from './segment-ingest.service';

/**
 * SEC-G50-16: kontrak body `POST /internal/v1/segments/{run_id}/members` = validasi BFF `segment-ingest.service.ts`.
 * Contoh normatif = fixture BERSAMA yang juga dibaca test segment-worker:
 *   services/segment-worker/tests/fixtures/segment_members_v1.json
 * Path dari root repo (CI men-checkout seluruh repo). File hilang = test GAGAL (bukan dilewati).
 *
 * Bentuk fixture yang dibaca (key lain diabaikan); setiap item boleh berupa body langsung atau objek pembungkus:
 * {
 *   "valid":   [ <body> | { "name", "body"|"payload", "path_run_id"? } ]   atau { "<nama>": <body> }
 *   "invalid": [ { "name", "body"|"payload", "path_run_id"?, "code"|"error"|"expected_code" } ] atau { "<nama>": {…} }
 * }
 * Alias diterima: `cases`/`examples` (= valid). `path_run_id` default = `body.run_id`. Kode 409 (`run_company_mismatch`,
 * `chunk_conflict`) disimulasikan dengan state DB yang sesuai.
 */
const REPO_ROOT = join(__dirname, '..', '..', '..', '..');
const FIXTURE = join(REPO_ROOT, 'services', 'segment-worker', 'tests', 'fixtures', 'segment_members_v1.json');

interface Item {
  name: string;
  body: unknown;
  pathRunId: string;
  code: string | null;
}

function toItems(raw: unknown, withCode: boolean): Item[] {
  if (raw === undefined || raw === null) return [];
  const entries: [string, unknown][] = Array.isArray(raw) ? raw.map((v, i) => [`#${i}`, v]) : Object.entries(raw as Record<string, unknown>);
  return entries.map(([key, v]) => {
    const o = (v ?? {}) as Record<string, unknown>;
    const wrapped = 'body' in o || 'payload' in o;
    const body = wrapped ? (o.body ?? o.payload) : v;
    const b = (body ?? {}) as Record<string, unknown>;
    const path = typeof o.path_run_id === 'string' ? o.path_run_id : typeof b.run_id === 'string' ? b.run_id : '00000000-0000-4000-8000-000000000000';
    // Fixture worker: `expect: {status, error_code}` = kode BFF (kanonik); `worker_error` = kode lokal worker.
    const expectObj = (o.expect ?? {}) as Record<string, unknown>;
    const codeRaw = o.code ?? o.expected_code ?? expectObj.error_code ?? o.error ?? null;
    return {
      name: typeof o.name === 'string' ? o.name : key,
      body,
      pathRunId: path,
      code: withCode ? (typeof codeRaw === 'string' ? codeRaw : null) : null,
    };
  });
}

function loadFixture(): { valid: Item[]; invalid: Item[] } {
  if (!existsSync(FIXTURE)) {
    throw new Error(`Fixture bersama kontrak segmen tidak ditemukan: ${FIXTURE} (SEC-G50-16; dibuat bersama perbaikan segment-worker)`);
  }
  const raw = JSON.parse(readFileSync(FIXTURE, 'utf8')) as Record<string, unknown>;
  return {
    valid: [...toItems(raw.valid, false), ...toItems(raw.cases, false), ...toItems(raw.examples, false)],
    invalid: toItems(raw.invalid, true),
  };
}

function service(code: string | null): { svc: SegmentIngestService; db: FakeDb } {
  const db = new FakeDb((t) => {
    if (t.includes('company_code <> $2')) return code === 'run_company_mismatch' ? { rows: [{ company_code: 'GL' }], rowCount: 1 } : { rows: [], rowCount: 0 };
    if (t.includes('INSERT INTO behavior.segment_chunk')) return code === 'chunk_conflict' ? { rows: [], rowCount: 0 } : { rows: [{}], rowCount: 1 };
    if (t.includes('SELECT body_sha256')) return { rows: [{ body_sha256: 'f'.repeat(64) }], rowCount: 1 };
    if (t.includes('INSERT INTO behavior.segment_member')) return { rows: [], rowCount: 1 };
    return undefined;
  });
  return { svc: new SegmentIngestService(db), db };
}

describe('Fixture bersama body anggota segmen segment-worker → BFF (SEC-G50-16)', () => {
  it('fixture ada, memuat kasus valid dan invalid; setiap invalid punya kode galat', () => {
    const { valid, invalid } = loadFixture();
    expect(valid.length).toBeGreaterThan(0);
    expect(invalid.length).toBeGreaterThan(0);
    for (const c of invalid) expect({ name: c.name, hasCode: c.code !== null }).toEqual({ name: c.name, hasCode: true });
  });

  it('setiap body valid diterima (200) dan ditulis dengan PT body', async () => {
    const { valid } = loadFixture();
    for (const c of valid) {
      const { svc, db } = service(null);
      const out = await svc.ingest(c.pathRunId, c.body);
      expect({ name: c.name, status: out.status, body: out.status === 200 ? null : out.body }).toEqual({ name: c.name, status: 200, body: null });
      const b = c.body as Record<string, unknown>;
      expect({ name: c.name, company: db.find('INSERT INTO behavior.segment_chunk')[0]?.params[5] }).toEqual({ name: c.name, company: b.company_code });
    }
  });

  it('setiap body invalid ditolak dengan kode galat persis', async () => {
    const { invalid } = loadFixture();
    for (const c of invalid) {
      const { svc } = service(c.code);
      const out = await svc.ingest(c.pathRunId, c.body);
      const code = out.status === 200 ? null : out.body.error.code;
      expect({ name: c.name, code }).toEqual({ name: c.name, code: c.code });
    }
  });
});
