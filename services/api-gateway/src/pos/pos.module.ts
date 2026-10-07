import { Module } from '@nestjs/common';
import { LoyaltyModule } from '../loyalty/loyalty.module';
import { OtpModule } from '../otp/otp.module';
import { DiscountTokenService } from './discount-token.service';
import { LoyaltyReconService } from './loyalty-recon.service';
import { LoyaltyReservationSweeper } from './loyalty-reservation.sweeper';
import { PosLoyaltyController } from './pos-loyalty.controller';
import { PosLoyaltyService } from './pos-loyalty.service';
import { PosController } from './pos.controller';
import { PosService } from './pos.service';

@Module({
  imports: [OtpModule, LoyaltyModule],
  controllers: [PosController, PosLoyaltyController],
  providers: [PosService, DiscountTokenService, PosLoyaltyService, LoyaltyReservationSweeper, LoyaltyReconService],
})
export class PosModule {}
