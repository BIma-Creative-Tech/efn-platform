import { Module } from '@nestjs/common';
import { LoyaltyModule } from '../loyalty/loyalty.module';
import { OtpModule } from '../otp/otp.module';
import { AltchaService } from './altcha.service';
import { AuthController } from './auth.controller';
import { AuthService } from './auth.service';
import { MeController } from './me.controller';

@Module({
  imports: [OtpModule, LoyaltyModule],
  controllers: [AuthController, MeController],
  providers: [AuthService, AltchaService],
  // G22 §14.A.7: AltchaService dipakai kiriman QR meja (FNB_CAPTCHA_MODE)
  exports: [AuthService, AltchaService],
})
export class AuthModule {}
