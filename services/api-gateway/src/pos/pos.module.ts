import { Module } from '@nestjs/common';
import { LoyaltyModule } from '../loyalty/loyalty.module';
import { OtpModule } from '../otp/otp.module';
import { DiscountTokenService } from './discount-token.service';
import { PosController } from './pos.controller';
import { PosService } from './pos.service';

@Module({
  imports: [OtpModule, LoyaltyModule],
  controllers: [PosController],
  providers: [PosService, DiscountTokenService],
})
export class PosModule {}
