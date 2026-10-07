import { FastifyAdapter, NestFastifyApplication } from '@nestjs/platform-fastify';
import { APP_FILTER } from '@nestjs/core';
import { Test } from '@nestjs/testing';
import { AppConfig } from '../config/app-config';
import { ContractExceptionFilter } from '../common/errors/contract-exception.filter';
import { GLOBAL_PREFIX, GLOBAL_PREFIX_EXCLUDE } from '../common/http/global-prefix';
import { PII_AAD } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';
import { FakeLoyaltyDb } from '../common/testing/fake-loyalty-db';
import { FakeClock, FakeDb, catchApiError, randomSecret, testCipher, testConfig } from '../common/testing/fakes';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { parseMigrationMember, parseOpeningBalance } from './dto/migration.dto';
import { MIGRATION_KEY_HEADER, MigrationKeyGuard } from './migration-key.guard';
import { MigrationController } from './migration.controller';
import { MigrationService } from './migration.service';

const MEMBER = '6281234567890';
const SHA = 'a'.repeat(64);

describe('Migrasi (docs/04 §9.6 K-7)', () => {
  describe('opening-balance', () => {
    function setup() {
      const clock = new FakeClock();
      const config = testConfig({ LOYALTY_POINT_EXPIRY_DAYS: '365' });
      const cipher = testCipher(config);
      const db = new FakeLoyaltyDb();
      const loyalty = new LoyaltyService(config, db, cipher);
      loyalty.now = clock.now;
      const svc = new MigrationService(config, db, cipher, loyalty);
      svc.now = clock.now;
      return { config, cipher, db, svc, hash: memberHash(config.memberHashPepper, MEMBER) };
    }

    it('entri ledger migration_opening terenkripsi; idempoten per source_ref; data beda → idempotency_conflict', async () => {
      const ctx = setup();
      const dto = { sourceRef: 'legacy-001', memberRef: MEMBER, points: 250, companyCode: 'RTL' as const };
      expect(await ctx.svc.openingBalance(dto)).toEqual({ applied: true, points_balance: 250 });
      expect(ctx.db.ledger[0]).toMatchObject({
        member_ref_hash: ctx.hash,
        company_code: 'RTL',
        delta_points: 250,
        source: 'migration_opening',
        source_ref: 'legacy-001',
        idempotency_key: 'migration_opening:legacy-001',
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
  });

  describe('members', () => {
    function setup() {
      const config = testConfig();
      const cipher = testCipher(config);
      const members = new Map<string, { id: string; deleted_at: Date | null; enc: Buffer }>();
      const consents: unknown[][] = [];
      const db = new FakeDb((text, p) => {
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
      const svc = new MigrationService(config, db, cipher, loyalty);
      return { config, cipher, members, consents, svc, hash: memberHash(config.memberHashPepper, MEMBER) };
    }

    it('upsert member (nomor terenkripsi AAD member) + consent ber-bukti sekali; impor ulang tidak menambah baris', async () => {
      const ctx = setup();
      const dto = parseMigrationMember({
        source_ref: 'legacy-001',
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
      await ctx.svc.importMember(parseMigrationMember({ source_ref: 'x', member_ref: MEMBER }));
      ctx.members.get(ctx.hash)!.deleted_at = new Date();
      const err = await catchApiError(() => ctx.svc.importMember(parseMigrationMember({ source_ref: 'x', member_ref: MEMBER })));
      expect(err.getStatus()).toBe(409);
    });

    it.each([
      [{ source_ref: 'x', member_ref: MEMBER, consents: [{ purpose: 'ads', granted: true, version: 'v', text_sha256: SHA }] }],
      [{ source_ref: 'x', member_ref: MEMBER, consents: [{ purpose: 'marketing', granted: 'yes', version: 'v', text_sha256: SHA }] }],
      [{ source_ref: 'x', member_ref: MEMBER, consents: [{ purpose: 'marketing', granted: true, version: 'v', text_sha256: 'zz' }] }],
      [{ source_ref: 'x', member_ref: MEMBER, consents: [{ purpose: 'marketing', granted: true, version: 'v', text_sha256: SHA, recorded_at: '2999-01-01T00:00:00Z' }] }],
      [{ source_ref: 'x y', member_ref: MEMBER }],
      [{ member_ref: MEMBER }],
    ])('validasi member → 400 (%j)', async (body) => {
      const err = await catchApiError(() => parseMigrationMember(body));
      expect(err.getStatus()).toBe(400);
    });

    it('validasi opening-balance', async () => {
      expect(parseOpeningBalance({ source_ref: 'a', member_ref: MEMBER, points: 5, company_code: 'FNB' })).toMatchObject({ points: 5 });
      for (const body of [
        { source_ref: 'a', member_ref: MEMBER, points: 0, company_code: 'FNB' },
        { source_ref: 'a', member_ref: MEMBER, points: 1.5, company_code: 'FNB' },
        { source_ref: 'a', member_ref: MEMBER, points: 5, company_code: 'XX' },
      ]) {
        expect((await catchApiError(() => parseOpeningBalance(body))).getStatus()).toBe(400);
      }
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
    const body = { source_ref: 'a', member_ref: MEMBER, points: 5, company_code: 'FNB' };

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
