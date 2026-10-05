import { Module } from '@nestjs/common';
import { OtpService } from './otp.service';
import { WhatsAppModule } from './whatsapp/whatsapp.module';

@Module({
  imports: [WhatsAppModule],
  providers: [OtpService],
  exports: [OtpService, WhatsAppModule],
})
export class OtpModule {}
