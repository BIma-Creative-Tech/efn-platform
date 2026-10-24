import { AmqpService, PermanentMessageError } from '../common/amqp/amqp.service';
import { PII_AAD } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';
import { FakeClock, FakeDb, testCipher, testConfig } from '../common/testing/fakes';
import { LOYALTY_EARN_CONSUMER, LoyaltyEarnConsumer } from './loyalty-earn.consumer';
import { COMPANY_CODES, LoyaltyService, isCompanyCode } from './loyalty.service';

const MEMBER = '6281234567890';
const REPORT = '9b2e1c4d-5f6a-4b7c-8d9e-0f1a2b3c4d5e';
const OCR = { userId: 'efn-ocr' };

interface LedgerRow {
  hash: string;
  enc: Buffer;
  company: string;
  delta: number;
  expiresAt: string | null;
}

interface ReportRow {
  status: string;
  user_hash: string;
  user_enc: Buffer;
}

/** Fake PostgreSQL minimal: UNIQUE(idempotency_key), PK processed_message, tabel price_hunter_report. */
function fakeDb(reports: Map<string, ReportRow>) {
  const ledger = new Map<string, LedgerRow>();
  const processed = new Set<string>();
  const db = new FakeDb((text, params) => {
    if (text.includes('INSERT INTO platform.processed_message')) {
      const k = `${String(params[0])}|${String(params[1])}`;
      if (processed.has(k)) return { rows: [], rowCount: 0 };
      processed.add(k);
      return { rows: [{ consumer: params[0] }], rowCount: 1 };
    }
    if (text.includes('FROM platform.price_hunter_report')) {
      const r = reports.get(String(params[0]));
      return r ? { rows: [{ id: params[0], ...r }], rowCount: 1 } : { rows: [], rowCount: 0 };
    }
    if (text.includes('INSERT INTO platform.loyalty_ledger')) {
      const key = String(params[7]);
      if (ledger.has(key)) return { rows: [], rowCount: 0 };
      ledger.set(key, {
        hash: String(params[0]),
        enc: params[1] as Buffer,
        company: String(params[2]),
        delta: Number(params[3]),
        expiresAt: (params[8] as string | null) ?? null,
      });
      return { rows: [{ id: String(ledger.size) }], rowCount: 1 };
    }
    if (text.includes('SUM(delta_points)')) {
      const at = String(params[1]);
      let sum = 0;
      for (const r of ledger.values()) {
        if (r.hash === params[0] && (r.expiresAt === null || r.expiresAt > at)) sum += r.delta;
      }
      return { rows: [{ balance: String(sum) }], rowCount: 1 };
    }
    return undefined;
  });
  return { db, ledger, processed };
}

function setup() {
  const clock = new FakeClock();
  const config = testConfig();
  const cipher = testCipher(config);
  const hash = memberHash(config.memberHashPepper, MEMBER);
  const reports = new Map<string, ReportRow>([
    [REPORT, { status: 'valid', user_hash: hash, user_enc: cipher.encrypt(MEMBER, PII_AAD.PRICE_HUNTER_REPORT_USER) }],
  ]);
  const { db, ledger, processed } = fakeDb(reports);
  const loyalty = new LoyaltyService(config, db, cipher);
  loyalty.now = clock.now;
  const consumer = new LoyaltyEarnConsumer({} as AmqpService, db, loyalty, config, cipher);
  consumer.pendingRetryDelaysMs = [];
  return { clock, config, cipher, hash, reports, db, ledger, processed, loyalty, consumer };
}

function message(hash: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: 2,
    idempotency_key: `price_hunter:${REPORT}`,
    member_hash: hash,
    points: 50,
    reason: 'Price Hunter valid',
    source: 'price_hunter',
    source_ref: REPORT,
    ...over,
  };
}

describe('LoyaltyEarnConsumer v2 (idempotency)', () => {
  it('pesan yang sama diproses dua kali → poin hanya bertambah sekali', async () => {
    const ctx = setup();
    expect(await ctx.consumer.handle(message(ctx.hash), OCR)).toBe('applied');
    expect(await ctx.consumer.handle(message(ctx.hash), OCR)).toBe('duplicate');
    expect(ctx.ledger.size).toBe(1);
    expect(await ctx.loyalty.balance(MEMBER)).toBe(50);
    expect(ctx.processed.has(`${LOYALTY_EARN_CONSUMER}|price_hunter:${REPORT}`)).toBe(true);
  });

  it('UNIQUE idempotency_key di ledger mencegah dobel walau processed_message terlewat', async () => {
    const ctx = setup();
    await ctx.loyalty.earn({
      idempotencyKey: `price_hunter:${REPORT}`,
      memberHash: ctx.hash,
      memberRefEnc: ctx.loyalty.encryptMemberRef(MEMBER),
      points: 50,
      reason: 'manual',
      source: 'price_hunter',
      sourceRef: REPORT,
    });
    expect(await ctx.consumer.handle(message(ctx.hash), OCR)).toBe('duplicate');
    expect(await ctx.loyalty.balance(MEMBER)).toBe(50);
  });

  it('ledger: hash member + member_ref_enc wajib (dekripsi = nomor, AAD ledger), company_code default EFN', async () => {
    const ctx = setup();
    await ctx.consumer.handle(message(ctx.hash), OCR);
    const row = [...ctx.ledger.values()][0];
    expect(row.hash).toBe(ctx.hash);
    expect(row.company).toBe('EFN');
    expect(ctx.cipher.decrypt(row.enc, PII_AAD.LOYALTY_LEDGER_MEMBER_REF)).toBe(MEMBER);
    // bukan salinan byte user_enc (AAD berbeda → dienkripsi ulang)
    expect(row.enc.equals(ctx.reports.get(REPORT)!.user_enc)).toBe(false);
    for (const q of ctx.db.queries) expect(q.params).not.toContain(MEMBER);
  });

  it('company_code eksplisit di payload dipakai bila valid (kode G20 §12.1)', async () => {
    const ctx = setup();
    await ctx.consumer.handle(message(ctx.hash, { company_code: 'PB' }), OCR);
    expect([...ctx.ledger.values()][0].company).toBe('PB');
  });

  it('G20 §12.1 + G24 §16.1: COMPANY_CODES = EFN/GL/PB/BCR/BCF/WTZ/FARM; kode lama RTL/FNB ditolak', () => {
    expect(COMPANY_CODES).toEqual(['EFN', 'GL', 'PB', 'BCR', 'BCF', 'WTZ', 'FARM']);
    for (const c of ['GL', 'PB', 'BCR', 'BCF', 'WTZ']) expect(isCompanyCode(c)).toBe(true);
    for (const c of ['RTL', 'FNB', 'gl', 'wtz', 'WETZELS', '']) expect(isCompanyCode(c)).toBe(false);
  });
});

describe('LoyaltyEarnConsumer v2 (SEC-08: autentikasi publisher & validasi semantik)', () => {
  it.each([
    ['publisher lain (efn-bff)', { userId: 'efn-bff' }],
    ['user_id tidak disetel', { userId: undefined }],
    ['user lama bersama (efn)', { userId: 'efn' }],
  ])('%s → PermanentMessageError (DLQ), tanpa efek', async (_n, meta) => {
    const ctx = setup();
    await expect(ctx.consumer.handle(message(ctx.hash), meta)).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.ledger.size).toBe(0);
    expect(ctx.processed.size).toBe(0);
  });

  it('points melebihi LOYALTY_MAX_POINTS_PER_EVENT (default 100) → DLQ; batas bisa diatur env', async () => {
    const ctx = setup();
    await expect(ctx.consumer.handle(message(ctx.hash, { points: 101 }), OCR)).rejects.toBeInstanceOf(PermanentMessageError);
    expect(await ctx.consumer.handle(message(ctx.hash, { points: 100 }), OCR)).toBe('applied');
    expect(testConfig({ LOYALTY_MAX_POINTS_PER_EVENT: '25' }).loyaltyMaxPointsPerEvent).toBe(25);
  });

  it.each(['rejected', 'flagged'])('report berstatus %s (bukan valid) → DLQ tanpa poin', async (status) => {
    const ctx = setup();
    ctx.reports.get(REPORT)!.status = status;
    await expect(ctx.consumer.handle(message(ctx.hash), OCR)).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.ledger.size).toBe(0);
  });

  it('report masih processing setelah menunggu → error transien (requeue), bukan DLQ permanen', async () => {
    const ctx = setup();
    ctx.reports.get(REPORT)!.status = 'processing';
    const err = await ctx.consumer.handle(message(ctx.hash), OCR).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(PermanentMessageError);
    expect(ctx.ledger.size).toBe(0);
  });

  it('report menjadi valid saat menunggu (worker COMMIT setelah publish) → diterapkan', async () => {
    const ctx = setup();
    ctx.reports.get(REPORT)!.status = 'processing';
    ctx.consumer.pendingRetryDelaysMs = [1];
    ctx.consumer.sleep = async () => {
      ctx.reports.get(REPORT)!.status = 'valid';
    };
    expect(await ctx.consumer.handle(message(ctx.hash), OCR)).toBe('applied');
  });

  it('member_hash ≠ user_hash report → DLQ (mint ke member lain dicegah)', async () => {
    const ctx = setup();
    const other = memberHash(ctx.config.memberHashPepper, '6285700000000');
    await expect(ctx.consumer.handle(message(other), OCR)).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.ledger.size).toBe(0);
  });

  it('report tidak ada → DLQ', async () => {
    const ctx = setup();
    const missing = '1b2e1c4d-5f6a-4b7c-8d9e-0f1a2b3c4d5e';
    await expect(
      ctx.consumer.handle(message(ctx.hash, { source_ref: missing, idempotency_key: `price_hunter:${missing}` }), OCR),
    ).rejects.toBeInstanceOf(PermanentMessageError);
  });

  it.each([
    ['schema_version 1 (lama)', { schema_version: 1 }],
    ['membawa member_ref mentah', { member_ref: MEMBER }],
    ['points negatif', { points: -5 }],
    ['points pecahan', { points: 1.5 }],
    ['member_hash invalid', { member_hash: 'bukan-hash' }],
    ['source tidak diizinkan', { source: 'mgm' }],
    ['idempotency_key tidak cocok source_ref', { idempotency_key: 'x-123' }],
    ['source_ref bukan UUID', { source_ref: 'abc', idempotency_key: 'price_hunter:abc' }],
    ['company_code tidak dikenal', { company_code: 'XYZ' }],
  ])('payload tidak valid (%s) → PermanentMessageError (DLQ, tanpa efek)', async (_name, over) => {
    const ctx = setup();
    await expect(ctx.consumer.handle(message(ctx.hash, over), OCR)).rejects.toBeInstanceOf(PermanentMessageError);
    expect(ctx.ledger.size).toBe(0);
    expect(ctx.processed.size).toBe(0);
  });
});

describe('LoyaltyService', () => {
  it('earn tanpa member_ref_enc ditolak', async () => {
    const ctx = setup();
    await expect(
      ctx.loyalty.earn({
        idempotencyKey: 'a',
        memberHash: ctx.hash,
        memberRefEnc: Buffer.alloc(0),
        points: 1,
        reason: '',
        source: 'test',
        sourceRef: null,
      }),
    ).rejects.toThrow('member_ref_enc wajib');
  });

  it('balance mengabaikan entri yang sudah kedaluwarsa', async () => {
    const ctx = setup();
    const base = { memberHash: ctx.hash, memberRefEnc: ctx.loyalty.encryptMemberRef(MEMBER), reason: '', source: 'test', sourceRef: null };
    await ctx.loyalty.earn({ ...base, idempotencyKey: 'a', points: 100 });
    await ctx.loyalty.earn({ ...base, idempotencyKey: 'b', points: 30, expiresAt: new Date(ctx.clock.ms + 3600_000) });
    expect(await ctx.loyalty.balance(MEMBER)).toBe(130);
    ctx.clock.advanceSeconds(3601);
    expect(await ctx.loyalty.balance(MEMBER)).toBe(100);
  });

  it('nomor tanpa entri → 0', async () => {
    const ctx = setup();
    expect(await ctx.loyalty.balance('6289999999999')).toBe(0);
  });
});
