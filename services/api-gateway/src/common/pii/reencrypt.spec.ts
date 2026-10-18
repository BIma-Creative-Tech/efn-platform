import { randomBytes } from 'node:crypto';
import { QueryResultLike } from '../db/db';
import { FakeDb, testConfig } from '../testing/fakes';
import { PII_AAD, PiiCipher } from './crypto';
import { PII_COLUMNS, findPiiColumns } from './pii-columns';
import { PiiKeyRingCheck } from './pii-key-check';
import { countUnreadable, reencryptColumn } from './reencrypt';

const K1 = randomBytes(32);
const K2 = randomBytes(32);
const COL = PII_COLUMNS.find((c) => c.table === 'member')!;

/** Tabel in-memory untuk satu kolom: query SELECT batch / UPDATE optimistik / count per versi. */
function fakeTable(rows: Map<string, Buffer>) {
  return new FakeDb((text, params): QueryResultLike<Record<string, unknown>> | undefined => {
    if (text.includes('GROUP BY 1')) {
      const counts = new Map<number, number>();
      for (const b of rows.values()) counts.set(b[0], (counts.get(b[0]) ?? 0) + 1);
      return { rows: [...counts].map(([v, n]) => ({ v, n })), rowCount: counts.size };
    }
    if (text.startsWith('SELECT') && text.includes('LIMIT $2')) {
      const [active, limit, last] = params as [number, number, string | undefined];
      const out = [...rows.entries()]
        .filter(([pk, b]) => b[0] !== active && (last === undefined || pk > last))
        .sort(([a], [b]) => (a < b ? -1 : 1))
        .slice(0, limit)
        .map(([pk, blob]) => ({ pk, blob }));
      return { rows: out, rowCount: out.length };
    }
    if (text.startsWith('UPDATE')) {
      const [next, pk, old] = params as [Buffer, string, Buffer];
      if (!rows.get(pk)?.equals(old)) return { rows: [], rowCount: 0 };
      rows.set(pk, next);
      return { rows: [], rowCount: 1 };
    }
    if (text.includes('count(*) AS n')) {
      const loaded = params[0] as number[];
      const n = [...rows.values()].filter((b) => !loaded.includes(b[0])).length;
      return { rows: [{ n }], rowCount: 1 };
    }
    return undefined;
  });
}

describe('re-encrypt PII (U2)', () => {
  it('mengenkripsi ulang v1 → v2 dalam batch, idempotent, baris rusak dihitung failed tanpa loop', async () => {
    const old = new PiiCipher(new Map([[1, K1]]), 1);
    const rows = new Map<string, Buffer>();
    for (let i = 0; i < 7; i += 1) rows.set(`id-${i}`, old.encrypt(`62812000000${i}`, COL.aad));
    rows.set('id-9', Buffer.concat([Buffer.from([1]), randomBytes(40)])); // rusak
    const both = new PiiCipher(new Map([[1, K1], [2, K2]]), 2);
    const db = fakeTable(rows);

    const r = await reencryptColumn(db, both, COL, 3);
    expect(r.before).toEqual({ '1': 8 });
    expect(r.reencrypted).toBe(7);
    expect(r.failed).toBe(1);
    const v2only = new PiiCipher(new Map([[2, K2]]), 2);
    expect(v2only.decrypt(rows.get('id-3')!, COL.aad)).toBe('628120000003');

    const again = await reencryptColumn(db, both, COL, 3);
    expect(again.reencrypted).toBe(0);
    expect(again.failed).toBe(1);
    expect(await countUnreadable(db, COL, [2])).toBe(1); // hanya baris rusak
  });

  it('dry-run hanya menghitung', async () => {
    const rows = new Map([['a', new PiiCipher(K1).encrypt('x', COL.aad)]]);
    const db = fakeTable(rows);
    const r = await reencryptColumn(db, new PiiCipher(new Map([[1, K1], [2, K2]]), 2), COL, 10, true);
    expect(r).toMatchObject({ before: { '1': 1 }, reencrypted: 0 });
    expect(db.find('UPDATE')).toHaveLength(0);
  });

  it('registry mencakup semua kolom *_enc dengan AAD kontrak; filter --columns', () => {
    expect(PII_COLUMNS.map((c) => `${c.table}.${c.column}`)).toEqual([
      'price_hunter_report.user_enc',
      'loyalty_ledger.member_ref_enc',
      'otp_audit.ip_enc',
      'member.member_ref_enc',
      'consent.ip_enc',
      'online_order_part.pickup_code_enc',
      'online_order_part.counter_code_enc',
    ]);
    expect(PII_AAD.ONLINE_COUNTER_CODE).toBe('efn:online_order_part.counter_code_enc:v1');
    expect(findPiiColumns('online_order_part.counter_code_enc')[0].rowAad!('AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA')).toBe(
      'efn:online_order_part.counter_code_enc:v2:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    );
    expect(PII_AAD.ONLINE_PICKUP_CODE).toBe('efn:online_order_part.pickup_code_enc:v1');
    expect(findPiiColumns('online_order_part.pickup_code_enc')[0]).toMatchObject({ pk: 'id', aad: PII_AAD.ONLINE_PICKUP_CODE });
    expect(PII_AAD.MEMBER_REF).toBe('efn:member.member_ref_enc:v1');
    expect(PII_AAD.CONSENT_IP).toBe('efn:consent.ip_enc:v1');
    expect(findPiiColumns('consent.ip_enc')).toHaveLength(1);
    expect(() => findPiiColumns('x.y')).toThrow('kolom tidak dikenal');
  });
});

describe('key ring PII tanpa v1 (U2)', () => {
  const b64 = (k: Buffer): string => k.toString('base64');

  it('v1 wajib selama belum dipensiunkan', () => {
    const cfg = testConfig({ PII_ENCRYPTION_KEY: '', PII_ENCRYPTION_KEY_V2: b64(K2), PII_ACTIVE_KEY_VERSION: '2' });
    expect(() => cfg.piiKeyRing).toThrow('PII_RETIRED_VERSIONS=1');
  });

  it('PII_RETIRED_VERSIONS=1 + aktif v2 → start tanpa v1; kunci v1 yang masih ada diabaikan', () => {
    const cfg = testConfig({
      PII_ENCRYPTION_KEY: b64(K1),
      PII_ENCRYPTION_KEY_V2: b64(K2),
      PII_ACTIVE_KEY_VERSION: '2',
      PII_RETIRED_VERSIONS: '1',
    });
    expect([...cfg.piiKeyRing.keys()]).toEqual([2]);
    expect(cfg.piiActiveKeyVersion).toBe(2);
    const noV1 = testConfig({ PII_ENCRYPTION_KEY: '', PII_ENCRYPTION_KEY_V2: b64(K2), PII_ACTIVE_KEY_VERSION: '2', PII_RETIRED_VERSIONS: '1' });
    expect(() => noV1.assertStartupSecrets()).not.toThrow();
  });

  it('versi aktif tidak boleh pensiun; daftar pensiun harus angka', () => {
    expect(() => testConfig({ PII_RETIRED_VERSIONS: '1' }).piiActiveKeyVersion).toThrow();
    expect(() => testConfig({ PII_RETIRED_VERSIONS: 'satu' }).piiRetiredVersions).toThrow('PII_RETIRED_VERSIONS');
  });

  it('PiiKeyRingCheck: masih ada ciphertext versi pensiun → gagal start; DB error hanya peringatan', async () => {
    const cfg = testConfig({ PII_ENCRYPTION_KEY: '', PII_ENCRYPTION_KEY_V2: b64(K2), PII_ACTIVE_KEY_VERSION: '2', PII_RETIRED_VERSIONS: '1' });
    const cipher = PiiCipher.fromConfig(cfg);
    const dirty = new FakeDb((text) => (text.includes('member') && text.includes('count(*)') ? { rows: [{ n: 3 }], rowCount: 1 } : { rows: [{ n: 0 }], rowCount: 1 }));
    await expect(new PiiKeyRingCheck(cfg, cipher, dirty).onApplicationBootstrap()).rejects.toThrow('member.member_ref_enc=3');
    const clean = new FakeDb(() => ({ rows: [{ n: 0 }], rowCount: 1 }));
    await expect(new PiiKeyRingCheck(cfg, cipher, clean).onApplicationBootstrap()).resolves.toBeUndefined();
    const down = new FakeDb(() => {
      throw new Error('db down');
    });
    await expect(new PiiKeyRingCheck(cfg, cipher, down).onApplicationBootstrap()).resolves.toBeUndefined();
  });
});

describe('SEC-102: kolom ber-AAD per baris (pickup_code_enc)', () => {
  it('re-encrypt membaca AAD v2 per baris atau AAD lama v1, menulis AAD v2 terikat id', async () => {
    const col = PII_COLUMNS.find((c) => c.table === 'online_order_part')!;
    const old = new PiiCipher(new Map([[1, K1]]), 1);
    const idA = '11111111-1111-4111-8111-111111111111';
    const idB = '22222222-2222-4222-8222-222222222222';
    const rows = new Map<string, Buffer>([
      [idA, old.encrypt('ABCDEFGH', col.aad)], // format lama (AAD kolom)
      [idB, old.encrypt('HGFEDCBA', col.rowAad!(idB))], // format v2
    ]);
    const next = new PiiCipher(new Map([[1, K1], [2, K2]]), 2);
    const rep = await reencryptColumn(fakeTable(rows), next, col, 10, false);
    expect(rep).toMatchObject({ reencrypted: 2, failed: 0 });
    expect(next.decrypt(rows.get(idA)!, col.rowAad!(idA))).toBe('ABCDEFGH');
    expect(next.decrypt(rows.get(idB)!, col.rowAad!(idB))).toBe('HGFEDCBA');
    expect(() => next.decrypt(rows.get(idA)!, col.rowAad!(idB))).toThrow(); // tidak bisa ditukar antar-baris
  });
});
