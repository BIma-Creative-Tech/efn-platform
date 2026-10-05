import { Module } from '@nestjs/common';
import { WhatsAppModule } from '../otp/whatsapp/whatsapp.module';
import { NotifyPushConsumer } from './notify-push.consumer';

@Module({
  imports: [WhatsAppModule],
  providers: [NotifyPushConsumer],
})
export class NotificationModule {}
