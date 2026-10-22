import { Module } from '@nestjs/common';
import { LoyaltyModule } from '../loyalty/loyalty.module';
import { OtpModule } from '../otp/otp.module';
import { DiscountTokenService } from './discount-token.service';
import { LoyaltyAccrualService } from './loyalty-accrual.service';
import { LoyaltyPolicySignatureGuard } from './loyalty-policy.guard';
import { LoyaltyReconService } from './loyalty-recon.service';
import { LoyaltyReservationSweeper } from './loyalty-reservation.sweeper';
import { PosLoyaltyFinanceController } from './pos-loyalty-finance.controller';
import { PosLoyaltyController } from './pos-loyalty.controller';
import { PosLoyaltyService } from './pos-loyalty.service';
import { PosController } from './pos.controller';
import { PosService } from './pos.service';

@Module({
  imports: [OtpModule, LoyaltyModule],
  controllers: [PosController, PosLoyaltyController, PosLoyaltyFinanceController],
  providers: [
    PosService,
    DiscountTokenService,
    PosLoyaltyService,
    LoyaltyReservationSweeper,
    LoyaltyReconService,
    LoyaltyAccrualService,
    LoyaltyPolicySignatureGuard,
  ],
  // G22: earn QR meja (fnb) memakai jalur ledger POS yang sama
  exports: [PosLoyaltyService],
})
export class PosModule {}
