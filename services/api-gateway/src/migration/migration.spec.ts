import { createHmac } from 'node:crypto';
import { Logger } from '@nestjs/common';
import { FastifyAdapter, NestFastifyApplication } from '@nestjs/platform-fastify';
import { APP_FILTER } from '@nestjs/core';
import { Test } from '@nestjs/testing';
import { AppConfig } from '../config/app-config';
import { ContractExceptionFilter } from '../common/errors/contract-exception.filter';
import { GLOBAL_PREFIX, GLOBAL_PREFIX_EXCLUDE } from '../common/http/global-prefix';
import { MetricsService } from '../common/metrics/metrics.service';
import { PII_AAD } from '../common/pii/crypto';
import { erasureSuppressionTag } from '../common/pii/erasure-suppression';
import { memberHash } from '../common/pii/pii';
import { FakeLoyaltyDb } from '../common/testing/fake-loyalty-db';
import { FakeClock, FakeDb, catchApiError, randomSecret, testCipher, testConfig } from '../common/testing/fakes';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { parseMigrationMember, parseOpaqueSourceRef, parseOpeningBalance } from './dto/migration.dto';
import { MIGRATION_KEY_HEADER, MigrationKeyGuard } from './migration-key.guard';
import { MigrationController } from './migration.controller';
import { MigrationService } from './migration.service';

const MEMBER = '6281234567890';
const SHA = 'a'.repeat(64);
// W-25 (Q-P13): source_ref opak — UUID v4 acak atau HMAC hex
const SR1 = '3f2b6c1e-8a4d-4c2e-9b1f-0a2b3c4d5e6f';
const SR2 = '9c1d7e2a-4b5f-4a60-8c7d-1e2f3a4b5c6d';
const SR9 = 'b7e4d2c1a9f80e3d5c6b7a8f9e0d1c2b';
const SRX = '0f1e2d3c-4b5a-4968-8776-655443322110';
const SRY = 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90';
const SRA = '6d5c4b3a-2f1e-4d0c-9b8a-7f6e5d4c3b2a';

describe('Migrasi (docs/04 §9.6 K-7)', () => {
  describe('opening-balance', () => {
    function setup() {
      const clock = new FakeClock();
      const config = testConfig({ LOYALTY_POINT_EXPIRY_DAYS: '365' });
      const cipher = testCipher(config);
      const db = new FakeLoyaltyDb();
      const loyalty = new LoyaltyService(config, db, cipher);
      loyalty.now = clock.now;
      const metrics = new MetricsService();
      const svc = new MigrationService(config, db, cipher, loyalty, metrics);
      svc.now = clock.now;
      return { clock, config, cipher, db, svc, metrics, hash: memberHash(config.memberHashPepper, MEMBER) };
    }

    it('entri ledger migration_opening terenkripsi; idempoten per source_ref; data beda → idempotency_conflict', async () => {
      const ctx = setup();
      const dto = { sourceRef: SR1, memberRef: MEMBER, points: 250, companyCode: 'RTL' as const };
      expect(await ctx.svc.openingBalance(dto)).toEqual({ applied: true, points_balance: 250 });
      expect(ctx.db.ledger[0]).toMatchObject({
        member_ref_hash: ctx.hash,
        company_code: 'RTL',
        delta_points: 250,
        source: 'migration_opening',
        source_ref: SR1,
        idempotency_key: `migration_opening:${SR1}`,
        reason: 'opening_balance',
      });
      expect(ctx.db.ledger[0].expires_at).not.toBeNull();
      expect(ctx.cipher.decrypt(ctx.db.ledger[0].member_ref_enc as Buffer, PII_AAD.LOYALTY_LEDGER_MEMBER_REF)).toBe(MEMBER);
      expect(await ctx.svc.openingBalance(dto)).toEqual({ applied: false, points_balance: 250 });
      expect(ctx.db.ledger).toHaveLength(1);
      const err = await catchApiError(() => ctx.svc.openingBalance({ ...dto, points: 251 }));
      expect(err.getStatus()).toBe(409);
      expect((err.body as { error: { code: string } }).error.code).toBe('idempotency_conflict');
    });

    it('SEC-61/SEC-66: metrik poin migrasi per PT + baris EFN_LOYALTY_MIGRATION tanpa nomor; saldo besar → anomali (tetap diterima)', async () => {
      const ctx = setup();
      const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
      const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
      try {
        await ctx.svc.openingBalance({ sourceRef: SR1, memberRef: MEMBER, points: 250, companyCode: 'RTL' });
        expect((await ctx.svc.openingBalance({ sourceRef: SR2, memberRef: MEMBER, points: 6000, companyCode: 'FNB' })).applied).toBe(true);
        const m = await ctx.metrics.loyaltyMigrationPoints.get();
        expect(m.values.map((v) => [v.labels.company, v.value])).toEqual([
          ['RTL', 250],
          ['FNB', 6000],
        ]);
        const lines = log.mock.calls.map((c) => String(c[0])).filter((l) => l.startsWith('EFN_LOYALTY_MIGRATION '));
        expect(lines).toHaveLength(2);
        expect(JSON.parse(lines[0].slice('EFN_LOYALTY_MIGRATION '.length))).toEqual({ company: 'RTL', points: 250, source_ref: SR1 });
        const anomalies = warn.mock.calls.map((c) => String(c[0])).filter((l) => l.startsWith('EFN_LOYALTY_ANOMALY '));
        expect(anomalies).toHaveLength(1);
        expect(JSON.parse(anomalies[0].slice('EFN_LOYALTY_ANOMALY '.length))).toMatchObject({
          kind: 'migration_large_balance',
          severity: 'warning',
          company: 'FNB',
          points: 6000,
          limit: 5000,
        });
        for (const l of [...lines, ...anomalies]) {
          expect(l).not.toContain(MEMBER.slice(2));
          expect(l).not.toContain(ctx.hash);
        }
      } finally {
        warn.mockRestore();
        log.mockRestore();
      }
    });

    it('SEC-65: saldo pembuka untuk subjek yang sedang/pernah dihapus → 409, tanpa entri ledger', async () => {
      const ctx = setup();
      const dto = { sourceRef: SR9, memberRef: MEMBER, points: 10, companyCode: 'RTL' as const };
      ctx.db.erasureActive.add(ctx.hash);
      expect((await catchApiError(() => ctx.svc.openingBalance(dto))).body).toEqual({ error: { code: 'member_deleting', message: expect.any(String) } });
      ctx.db.erasureActive.clear();
      const tag = erasureSuppressionTag(ctx.config.erasureSuppressionKey, ctx.hash);
      ctx.db.suppression.set(tag, new Date(ctx.clock.now() + 86_400_000).toISOString());
      expect((await catchApiError(() => ctx.svc.openingBalance(dto))).body).toEqual({ error: { code: 'member_erased', message: expect.any(String) } });
      expect(ctx.db.ledger).toHaveLength(0);
      const anomalies = await ctx.metrics.loyaltyAnomaly.get();
      expect(anomalies.values.find((v) => v.labels.kind === 'migration_erased_member')?.value).toBe(2);
      // tag kedaluwarsa → impor diterima lagi
      ctx.clock.advanceSeconds(2 * 86_400);
      expect((await ctx.svc.openingBalance(dto)).applied).toBe(true);
    });
  });

  describe('members', () => {
    function setup() {
      const config = testConfig();
      const cipher = testCipher(config);
      const members = new Map<string, { id: string; deleted_at: Date | null; enc: Buffer }>();
      const consents: unknown[][] = [];
      const erasure = { active: new Set<string>(), tags: new Set<string>() };
      const db = new FakeDb((text, p) => {
        if (text.includes('FROM platform.dsar_request') && text.includes('platform.erasure_suppression')) {
          if (erasure.active.has(String(p[0]))) return { rows: [{ kind: 'erasure_active' }], rowCount: 1 };
          return erasure.tags.has(String(p[1])) ? { rows: [{ kind: 'erased' }], rowCount: 1 } : { rows: [], rowCount: 0 };
        }
        if (text.includes('INSERT INTO platform.member')) {
          if (members.has(String(p[1]))) return { rows: [], rowCount: 0 };
          members.set(String(p[1]), { id: String(p[0]), deleted_at: null, enc: p[2] as Buffer });
          return { rows: [{ id: p[0] }], rowCount: 1 };
        }
        if (text.includes('FROM platform.member WHERE member_hash')) {
          const m = members.get(String(p[0]));
          return m ? { rows: [{ id: m.id, deleted_at: m.deleted_at }], rowCount: 1 } : { rows: [], rowCount: 0 };
        }
        if (text.includes('INSERT INTO platform.consent')) {
          if (consents.some((c) => c[0] === p[0] && c[1] === p[1])) return { rows: [], rowCount: 0 };
          consents.push([...p]);
          return { rows: [], rowCount: 1 };
        }
        return undefined;
      });
      const loyalty = new LoyaltyService(config, db, cipher);
      const svc = new MigrationService(config, db, cipher, loyalty, new MetricsService());
      return { config, cipher, members, consents, erasure, db, svc, hash: memberHash(config.memberHashPepper, MEMBER) };
    }

    it('upsert member (nomor terenkripsi AAD member) + consent ber-bukti sekali; impor ulang tidak menambah baris', async () => {
      const ctx = setup();
      const dto = parseMigrationMember({
        source_ref: SR1,
        member_ref: '081234567890',
        consents: [{ purpose: 'marketing', granted: true, version: 'v2019', text_sha256: SHA, recorded_at: '2024-01-02T03:04:05Z' }],
      });
      expect(await ctx.svc.importMember(dto)).toEqual({ member: 'created', consents_recorded: 1 });
      expect(ctx.cipher.decrypt(ctx.members.get(ctx.hash)!.enc, PII_AAD.MEMBER_REF)).toBe(MEMBER);
      expect(ctx.consents[0].slice(1)).toEqual(['marketing', true, 'v2019', SHA, '2024-01-02T03:04:05.000Z']);
      expect(await ctx.svc.importMember(dto)).toEqual({ member: 'existing', consents_recorded: 0 });
    });

    it('member yang sedang dihapus (DSAR) → 409 member_deleting', async () => {
      const ctx = setup();
      await ctx.svc.importMember(parseMigrationMember({ source_ref: SRX, member_ref: MEMBER }));
      ctx.members.get(ctx.hash)!.deleted_at = new Date();
      const err = await catchApiError(() => ctx.svc.importMember(parseMigrationMember({ source_ref: SRX, member_ref: MEMBER })));
      expect(err.getStatus()).toBe(409);
    });

    it('SEC-65: member yang dihapus (erasure processing, member sudah DIHAPUS) → 409 member_deleting, tidak dibuat ulang', async () => {
      const ctx = setup();
      ctx.erasure.active.add(ctx.hash);
      const err = await catchApiError(() => ctx.svc.importMember(parseMigrationMember({ source_ref: SRX, member_ref: MEMBER })));
      expect(err.body).toEqual({ error: { code: 'member_deleting', message: expect.any(String) } });
      expect(ctx.members.size).toBe(0);
      expect(ctx.db.find('INSERT INTO platform.member')).toHaveLength(0);
    });

    it('SEC-65: erasure completed (tag daftar tolak HMAC, bukan member_hash) → 409 member_erased; nomor lain tetap bisa', async () => {
      const ctx = setup();
      const tag = erasureSuppressionTag(ctx.config.erasureSuppressionKey, ctx.hash);
      expect(tag).toMatch(/^[0-9a-f]{64}$/);
      expect(tag).not.toBe(ctx.hash);
      ctx.erasure.tags.add(tag);
      const err = await catchApiError(() =>
        ctx.svc.importMember(
          parseMigrationMember({ source_ref: SRX, member_ref: MEMBER, consents: [{ purpose: 'marketing', granted: true, version: 'v', text_sha256: SHA }] }),
        ),
      );
      expect(err.body).toEqual({ error: { code: 'member_erased', message: expect.any(String) } });
      expect(ctx.members.size).toBe(0);
      expect(ctx.consents).toHaveLength(0);
      // query memakai member_hash (dsar_request aktif) dan tag (daftar tolak) — bukan nomor HP
      const q = ctx.db.find('platform.erasure_suppression')[0];
      expect(q.params.slice(0, 2)).toEqual([ctx.hash, tag]);
      expect(JSON.stringify(q.params)).not.toContain(MEMBER);
      expect((await ctx.svc.importMember(parseMigrationMember({ source_ref: SRY, member_ref: '6285711112222' }))).member).toBe('created');
    });

    it.each([
      [{ source_ref: SRX, member_ref: MEMBER, consents: [{ purpose: 'ads', granted: true, version: 'v', text_sha256: SHA }] }],
      [{ source_ref: SRX, member_ref: MEMBER, consents: [{ purpose: 'marketing', granted: 'yes', version: 'v', text_sha256: SHA }] }],
      [{ source_ref: SRX, member_ref: MEMBER, consents: [{ purpose: 'marketing', granted: true, version: 'v', text_sha256: 'zz' }] }],
      [{ source_ref: SRX, member_ref: MEMBER, consents: [{ purpose: 'marketing', granted: true, version: 'v', text_sha256: SHA, recorded_at: '2999-01-01T00:00:00Z' }] }],
      [{ source_ref: 'x y', member_ref: MEMBER }],
      [{ member_ref: MEMBER }],
    ])('validasi member → 400 (%j)', async (body) => {
      const err = await catchApiError(() => parseMigrationMember(body));
      expect(err.getStatus()).toBe(400);
    });

    it('validasi opening-balance', async () => {
      expect(parseOpeningBalance({ source_ref: SRA, member_ref: MEMBER, points: 5, company_code: 'FNB' })).toMatchObject({ points: 5 });
      for (const body of [
        { source_ref: SRA, member_ref: MEMBER, points: 0, company_code: 'FNB' },
        { source_ref: SRA, member_ref: MEMBER, points: 1.5, company_code: 'FNB' },
        { source_ref: SRA, member_ref: MEMBER, points: 5, company_code: 'XX' },
      ]) {
        expect((await catchApiError(() => parseOpeningBalance(body))).getStatus()).toBe(400);
      }
    });
  });

  describe('W-25 (Q-P13): source_ref wajib opak', () => {
    it('diterima: UUID v4 acak & hex HMAC 32..64 (dinormalkan huruf kecil)', () => {
      expect(parseOpaqueSourceRef(SR1)).toBe(SR1);
      expect(parseOpaqueSourceRef(SR1.toUpperCase())).toBe(SR1);
      expect(parseOpaqueSourceRef(SR9)).toBe(SR9);
      expect(parseOpaqueSourceRef(SRY)).toBe(SRY);
      // HMAC-SHA256 nyata atas id lama (kunci di luar platform)
      const h = createHmac('sha256', 'kunci-migrasi-uji').update('CUST-000123').digest('hex');
      expect(parseOpaqueSourceRef(h)).toBe(h);
      expect(parseOpaqueSourceRef(h.slice(0, 32))).toBe(h.slice(0, 32));
    });

    it.each([
      ['legacy-001'],
      ['6281234567890'], // nomor HP E.164
      ['081234567890'], // nomor HP lokal
      ['+6281234567890'],
      ['CUST-000123'], // nomor pelanggan lama
      ['000123'],
      ['budi@contoh.co.id'], // email
      ['12345678901234567890123456789012'], // 32 digit desimal (nomor di-padding)
      ['3632383132333435363738393030303030'], // hex dari teks angka "62812345678900000"
      ['00000000000000000000000000000abc'], // padding, keragaman rendah
      ['abababababababababababababababab'],
      ['3f2b6c1e-8a4d-1c2e-9b1f-0a2b3c4d5e6f'], // UUID v1 (MAC/waktu)
      ['018f3c1e-8a4d-7c2e-9b1f-0a2b3c4d5e6f'], // UUID v7 (waktu)
      [' 3f2b6c1e-8a4d-4c2e-9b1f-0a2b3c4d5e6f'], // spasi
      ['a'.repeat(65)],
      [''],
      [12345],
    ])('ditolak 400 invalid_source_ref: %j', async (v) => {
      const err = await catchApiError(() => parseOpaqueSourceRef(v));
      expect(err.getStatus()).toBe(400);
      expect(err.body).toEqual({ error: { code: 'invalid_source_ref', message: expect.any(String) } });
    });

    it('jalur impor: DTO member & saldo menolak id lama; service menolak pemanggil internal yang melewati DTO', async () => {
      for (const parse of [
        () => parseMigrationMember({ source_ref: '6281234567890', member_ref: MEMBER }),
        () => parseOpeningBalance({ source_ref: 'CUST-000123', member_ref: MEMBER, points: 5, company_code: 'FNB' }),
      ]) {
        expect((await catchApiError(parse)).body).toMatchObject({ error: { code: 'invalid_source_ref' } });
      }
      const config = testConfig();
      const cipher = testCipher(config);
      const db = new FakeLoyaltyDb();
      const svc = new MigrationService(config, db, cipher, new LoyaltyService(config, db, cipher), new MetricsService());
      expect((await catchApiError(() => svc.openingBalance({ sourceRef: 'legacy-001', memberRef: MEMBER, points: 5, companyCode: 'FNB' }))).getStatus()).toBe(400);
      expect((await catchApiError(() => svc.importMember({ sourceRef: SR1.toUpperCase(), memberRef: MEMBER, consents: [] }))).getStatus()).toBe(400);
    });
  });

  describe('route & guard (Fastify nyata, prefix global sama dengan main.ts)', () => {
    async function boot(overrides: Record<string, string>) {
      const config = testConfig(overrides);
      const svc = {
        importMember: jest.fn().mockResolvedValue({ member: 'created', consents_recorded: 0 }),
        openingBalance: jest.fn().mockResolvedValue({ applied: true, points_balance: 5 }),
      };
      const mod = await Test.createTestingModule({
        controllers: [MigrationController],
        providers: [
          { provide: AppConfig, useValue: config },
          { provide: MigrationService, useValue: svc },
          MigrationKeyGuard,
          { provide: APP_FILTER, useClass: ContractExceptionFilter },
        ],
      }).compile();
      const app = mod.createNestApplication<NestFastifyApplication>(new FastifyAdapter());
      app.setGlobalPrefix(GLOBAL_PREFIX, { exclude: GLOBAL_PREFIX_EXCLUDE });
      await app.init();
      await app.getHttpAdapter().getInstance().ready();
      return { app, svc, config };
    }
    const body = { source_ref: SRA, member_ref: MEMBER, points: 5, company_code: 'FNB' };

    it('MIGRATION_ENABLED=false (default) → 404 meski kunci benar', async () => {
      const key = randomSecret();
      const { app, svc } = await boot({ MIGRATION_API_KEY: key });
      const res = await app.inject({ method: 'POST', url: '/internal/v1/migration/opening-balance', headers: { [MIGRATION_KEY_HEADER]: key }, payload: body });
      expect(res.statusCode).toBe(404);
      expect(svc.openingBalance).not.toHaveBeenCalled();
      await app.close();
    });

    it('aktif: path /internal/v1/migration/* (bukan /v1/internal/…); tanpa/kunci salah/kunci Odoo → 401; kunci benar → 200', async () => {
      const key = randomSecret();
      const odooKey = randomSecret();
      const { app, svc } = await boot({ MIGRATION_ENABLED: 'true', MIGRATION_API_KEY: key, ODOO_BFF_API_KEY: odooKey });
      const post = (url: string, headers: Record<string, string> = {}) => app.inject({ method: 'POST', url, headers, payload: body });
      expect((await post('/internal/v1/migration/opening-balance')).statusCode).toBe(401);
      expect((await post('/internal/v1/migration/opening-balance', { [MIGRATION_KEY_HEADER]: odooKey })).statusCode).toBe(401);
      expect((await post('/internal/v1/migration/opening-balance', { 'x-efn-service-key': key })).statusCode).toBe(401);
      const ok = await post('/internal/v1/migration/opening-balance', { [MIGRATION_KEY_HEADER]: key });
      expect(ok.statusCode).toBe(200);
      expect(ok.json()).toEqual({ applied: true, points_balance: 5 });
      expect((await post('/internal/v1/migration/members', { [MIGRATION_KEY_HEADER]: key })).statusCode).toBe(200);
      expect((await post('/v1/internal/v1/migration/opening-balance', { [MIGRATION_KEY_HEADER]: key })).statusCode).toBe(404);
      expect(svc.openingBalance).toHaveBeenCalledTimes(1);
      await app.close();
    });

    it('startup: MIGRATION_ENABLED tanpa kunci / kunci sama dengan kunci Odoo → gagal start', () => {
      expect(() => testConfig({ MIGRATION_ENABLED: 'true', MIGRATION_API_KEY: '' }).assertStartupSecrets()).toThrow(/MIGRATION_API_KEY/);
      const same = randomSecret();
      expect(() => testConfig({ MIGRATION_ENABLED: 'true', MIGRATION_API_KEY: same, ODOO_BFF_API_KEY: same }).assertStartupSecrets()).toThrow(
        /MIGRATION_API_KEY harus berbeda/,
      );
    });
  });
});
