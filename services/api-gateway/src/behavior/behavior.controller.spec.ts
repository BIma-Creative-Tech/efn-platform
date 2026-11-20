import { ROUTE_ARGS_METADATA } from '@nestjs/common/constants';
import { RouteParamtypes } from '@nestjs/common/enums/route-paramtypes.enum';
import { FastifyRequest } from 'fastify';
import { MemberService } from '../common/member/member.service';
import { AccessTokenService } from '../common/security/access-token.service';
import { catchApiError, testConfig } from '../common/testing/fakes';
import { WishlistController } from '../wishlist/wishlist.controller';
import { CampaignInboxController } from '../campaign/campaign-inbox.controller';
import { BehaviorController, PublicConfigController, optionalMember } from './behavior.controller';

const req = (authorization?: string): FastifyRequest => ({ headers: authorization === undefined ? {} : { authorization } }) as unknown as FastifyRequest;

describe('optionalMember (Bearer opsional §28.6)', () => {
  const tokens = { verify: jest.fn() } as unknown as AccessTokenService & { verify: jest.Mock };
  const members = { findActive: jest.fn() } as unknown as MemberService & { findActive: jest.Mock };

  it('tanpa header → anonim (null)', async () => {
    expect(await optionalMember(req(), tokens, members)).toBeNull();
  });

  it('header rusak / token tidak valid / member terhapus → 401 (tidak diturunkan jadi anonim)', async () => {
    expect((await catchApiError(() => optionalMember(req('Basic abc'), tokens, members))).getStatus()).toBe(401);
    tokens.verify.mockReturnValueOnce(null);
    expect((await catchApiError(() => optionalMember(req('Bearer a.b.c'), tokens, members))).getStatus()).toBe(401);
    tokens.verify.mockReturnValueOnce({ memberId: 'm1' });
    members.findActive.mockResolvedValueOnce(null);
    expect((await catchApiError(() => optionalMember(req('Bearer a.b.c'), tokens, members))).getStatus()).toBe(401);
  });

  it('token valid → AuthUser', async () => {
    tokens.verify.mockReturnValueOnce({ memberId: 'm1' });
    members.findActive.mockResolvedValueOnce({ id: 'm1', memberHash: 'h'.repeat(64) });
    expect(await optionalMember(req('Bearer a.b.c'), tokens, members)).toEqual({ memberId: 'm1', memberHash: 'h'.repeat(64) });
  });
});

describe('GET /v1/config features (§28.6, §28.10)', () => {
  it('default semua false; tracking hanya true bila flag + teks consent tracking dikonfigurasi', () => {
    expect(new PublicConfigController(testConfig()).get()).toEqual({
      features: { tracking: false, wishlist: false, restock_alert: false, price_alert: false, campaign_inbox: false },
      tracking_consent: null,
    });
    expect(new PublicConfigController(testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true' })).get().features.tracking).toBe(false);
    const on = new PublicConfigController(
      testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true', WISHLIST_ENABLED: 'true', CONSENT_TRACKING_VERSION: 'tracking@1', CONSENT_TRACKING_TEXT_SHA256: 'c'.repeat(64) }),
    ).get();
    expect(on.features).toMatchObject({ tracking: true, wishlist: true });
    expect(on.tracking_consent).toEqual({ version: 'tracking@1' });
  });
});

describe('Audit URL tanpa PII (R2) — controller G50-A', () => {
  it('hanya param/query non-PII', () => {
    const allowed = new Set(['channel', 'channel_code', 'product_id', 'id']);
    for (const ctrl of [BehaviorController, PublicConfigController, WishlistController, CampaignInboxController] as (new (...a: never[]) => unknown)[]) {
      for (const method of Object.getOwnPropertyNames(ctrl.prototype)) {
        const args = (Reflect.getMetadata(ROUTE_ARGS_METADATA, ctrl, method) ?? {}) as Record<string, { data?: unknown }>;
        for (const [k, meta] of Object.entries(args)) {
          const type = Number(k.split(':')[0]);
          if (type !== RouteParamtypes.QUERY && type !== RouteParamtypes.PARAM) continue;
          expect({ where: `${ctrl.name}.${method}`, ok: typeof meta.data === 'string' && allowed.has(meta.data) }).toEqual({ where: `${ctrl.name}.${method}`, ok: true });
        }
      }
    }
  });
});
