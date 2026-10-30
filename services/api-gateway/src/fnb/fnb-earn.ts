import { Injectable } from '@nestjs/common';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { PosLoyaltyService } from '../pos/pos-loyalty.service';
import { FnbEarnPort } from './fnb-pos.service';
import { FnbRepo } from './fnb.repository';

/**
 * Earn QR meja (docs/04 §14.A.4, D-22-5) di transaksi event yang sama: `member.member_ref_enc` → (dekripsi, verifikasi
 * hash, enkripsi ulang AAD ledger) → `PosLoyaltyService.earnQrInTx` (`pos_earn:<order_uuid>`, kebijakan earn PT,
 * pagar `earn_limit_exceeded`). Member sudah tidak aktif (dihapus/erasure) → `skipped`. Nomor HP hanya di memori.
 */
@Injectable()
export class PgFnbEarn implements FnbEarnPort {
  constructor(
    private readonly loyalty: LoyaltyService,
    private readonly pos: PosLoyaltyService,
  ) {}

  async earn(
    r: FnbRepo,
    input: { memberId: string; orderUuid: string; companyCode: 'PB' | 'BCF'; eligibleAmount: number; configId: number; mgmEligibleAmount?: number },
  ): Promise<{ outcome: 'earned' | 'replay' | 'limited' | 'conflict' | 'skipped'; points: number }> {
    const q = r.sql();
    if (!q) throw new Error('earn QR wajib di dalam transaksi PostgreSQL');
    const member = await this.loyalty.ledgerEncForMember(q, input.memberId);
    if (!member) return { outcome: 'skipped', points: 0 };
    return this.pos.earnQrInTx(q, {
      idempotencyKey: `pos_earn:${input.orderUuid}`,
      orderRef: input.orderUuid,
      memberRef: member.memberRef,
      companyCode: input.companyCode,
      eligibleAmount: input.eligibleAmount,
      posConfigId: input.configId,
      ...(input.mgmEligibleAmount !== undefined ? { mgmEligibleAmount: input.mgmEligibleAmount } : {}),
    });
  }
}
