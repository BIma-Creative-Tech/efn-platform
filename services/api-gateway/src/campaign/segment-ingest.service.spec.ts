import { randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { IncomingMessage, ServerResponse } from 'node:http';
import { FakeDb, testConfig } from '../common/testing/fakes';
import { SegmentIngestService } from './segment-ingest.service';
import { SegmentInternalServer } from './segment-internal.server';

const RUN = randomUUID();
const K1 = '1'.repeat(64);
const K2 = '2'.repeat(64);
const chunk = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  schema_version: 1,
  company_code: 'SMD',
  run_id: RUN,
  segment_code: 'oos_viewed',
  chunk_no: 0,
  subject_keys: [K2, K1, K1],
  ...extra,
});

function setup(opts: { inserted?: boolean; prevSha?: string; runOtherCompany?: string } = {}) {
  const db = new FakeDb((t) => {
    if (t.includes('company_code <> $2')) return opts.runOtherCompany ? { rows: [{ company_code: opts.runOtherCompany }], rowCount: 1 } : { rows: [], rowCount: 0 };
    if (t.includes('INSERT INTO behavior.segment_chunk')) return opts.inserted === false ? { rows: [], rowCount: 0 } : { rows: [{}], rowCount: 1 };
    if (t.includes('SELECT body_sha256')) return { rows: [{ body_sha256: opts.prevSha ?? '' }], rowCount: 1 };
    if (t.includes('INSERT INTO behavior.segment_member')) return { rows: [], rowCount: 2 };
    return undefined;
  });
  return { db, svc: new SegmentIngestService(db) };
}

describe('SegmentIngestService (POST /internal/v1/segments/{run_id}/members, §28.7)', () => {
  it('chunk baru: subject_key unik & terurut disimpan, subjek terhapus dikecualikan', async () => {
    const ctx = setup();
    const out = await ctx.svc.ingest(RUN, chunk());
    expect(out).toEqual({ status: 200, body: { ok: true, run_id: RUN, segment_code: 'oos_viewed', chunk_no: 0, accepted: 2, duplicate_chunk: false } });
    const ins = ctx.db.find('INSERT INTO behavior.segment_member')[0]!;
    expect(ins.params[2]).toEqual([K1, K2]);
    expect(ins.params[3]).toBe('SMD');
    expect(ins.text).toContain('behavior.erased_subject');
    expect(ctx.db.find('INSERT INTO behavior.segment_chunk')[0]!.params[5]).toBe('SMD');
  });

  it('SEC-G50-08: company_code wajib & ∈ COMPANY_CODES; run_id yang sudah milik PT lain → 409 run_company_mismatch', async () => {
    const ctx = setup();
    const noCompany = chunk();
    delete noCompany.company_code;
    expect((await ctx.svc.ingest(RUN, noCompany)).status).toBe(400);
    expect((await ctx.svc.ingest(RUN, chunk({ company_code: 'ZZZ' }))).status).toBe(400);
    expect(ctx.db.queries).toHaveLength(0);
    const other = setup({ runOtherCompany: 'PB' });
    expect(await other.svc.ingest(RUN, chunk())).toMatchObject({ status: 409, body: { error: { code: 'run_company_mismatch' } } });
    expect(other.db.find('INSERT INTO behavior.segment_member')).toHaveLength(0);
  });

  it('kirim ulang isi sama → duplicate_chunk; isi berbeda dengan kunci sama → 409 chunk_conflict', async () => {
    const first = setup();
    await first.svc.ingest(RUN, chunk());
    const sha = first.db.find('INSERT INTO behavior.segment_chunk')[0]!.params[3] as string;
    const dup = setup({ inserted: false, prevSha: sha });
    expect(await dup.svc.ingest(RUN, chunk({ subject_keys: [K1, K2] }))).toMatchObject({ status: 200, body: { duplicate_chunk: true, accepted: 0 } });
    expect(dup.db.find('INSERT INTO behavior.segment_member')).toHaveLength(0);
    const conflict = setup({ inserted: false, prevSha: 'f'.repeat(64) });
    expect(await conflict.svc.ingest(RUN, chunk())).toMatchObject({ status: 409, body: { error: { code: 'chunk_conflict' } } });
  });

  it('validasi: run_id ≠ path, > 5000 subjek, subjek bukan hex, field asing → 400 tanpa query', async () => {
    const ctx = setup();
    for (const [path, body] of [
      [randomUUID(), chunk()],
      [RUN, chunk({ subject_keys: Array.from({ length: 5001 }, (_, i) => i.toString(16).padStart(64, '0')) })],
      [RUN, chunk({ subject_keys: ['628123456789'] })],
      [RUN, chunk({ member_ids: [1] })],
      [RUN, chunk({ segment_code: 'DROP TABLE' })],
    ] as [string, Record<string, unknown>][]) {
      expect((await ctx.svc.ingest(path, body)).status).toBe(400);
    }
    expect(ctx.db.queries).toHaveLength(0);
  });
});

describe('SegmentInternalServer.handle (mTLS CN segment-worker)', () => {
  function fakeReq(opts: { cn?: string; url?: string; method?: string; body?: string; contentType?: string }): IncomingMessage {
    const req = new EventEmitter() as unknown as IncomingMessage & EventEmitter;
    Object.assign(req, {
      url: opts.url ?? `/internal/v1/segments/${RUN}/members`,
      method: opts.method ?? 'POST',
      headers: { 'content-type': opts.contentType ?? 'application/json' },
      socket: { getPeerCertificate: () => (opts.cn ? { subject: { CN: opts.cn }, subjectaltname: '' } : {}) },
      resume: () => undefined,
    });
    setImmediate(() => {
      if (opts.body !== undefined) req.emit('data', Buffer.from(opts.body));
      req.emit('end');
    });
    return req;
  }
  interface FakeRes {
    status: number;
    body: string;
    writeHead(s: number): FakeRes;
    end(b?: string): FakeRes;
  }
  function fakeRes(): FakeRes {
    const r: FakeRes = {
      status: 0,
      body: '',
      writeHead(s: number): FakeRes {
        r.status = s;
        return r;
      },
      end(b?: string): FakeRes {
        r.body = b ?? '';
        return r;
      },
    };
    return r;
  }

  const server = (svc: SegmentIngestService) => new SegmentInternalServer(testConfig({ PROMO_CAMPAIGN_ENABLED: 'true' }), svc);

  it('CN selain segment-worker → 403 tanpa membaca body', async () => {
    const ctx = setup();
    const res = fakeRes();
    await server(ctx.svc).handle(fakeReq({ cn: 'forecast-worker', body: JSON.stringify(chunk()) }), res as unknown as ServerResponse, ['segment-worker']);
    expect(res.status).toBe(403);
    expect(ctx.db.queries).toHaveLength(0);
  });

  it('CN benar: path/metode/content-type/JSON salah → 404/405/415/400; valid → 200', async () => {
    const ctx = setup();
    const run = async (o: Parameters<typeof fakeReq>[0]) => {
      const res = fakeRes();
      await server(ctx.svc).handle(fakeReq({ cn: 'segment-worker', ...o }), res as unknown as ServerResponse, ['segment-worker']);
      return res;
    };
    expect((await run({ url: '/internal/v1/segments/x/members' })).status).toBe(404);
    expect((await run({ method: 'GET' })).status).toBe(405);
    expect((await run({ contentType: 'text/plain', body: '{}' })).status).toBe(415);
    expect((await run({ body: '{bukan json' })).status).toBe(400);
    const ok = await run({ body: JSON.stringify(chunk()) });
    expect(ok.status).toBe(200);
    expect(JSON.parse(ok.body)).toMatchObject({ ok: true, accepted: 2 });
  });
});
