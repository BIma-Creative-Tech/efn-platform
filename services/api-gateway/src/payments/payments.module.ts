import { Module } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { createPaymentGateway } from './payment-gateway.factory';
import { PaymentGateway } from './payment-gateway';
import { PosPaymentsController, PaymentWebhookController } from './payments.controller';
import { PAYMENT_GATEWAY, PaymentGatewayHolder, PaymentsService } from './payments.service';

/** Payment gateway-agnostic (docs/04 §8.3, ADR-016 Proposed). */
@Module({
  controllers: [PosPaymentsController, PaymentWebhookController],
  providers: [
    PaymentsService,
    {
      provide: PAYMENT_GATEWAY,
      inject: [AppConfig],
      useFactory: (config: AppConfig): PaymentGatewayHolder => {
        let created = false;
        let gw: PaymentGateway | null = null;
        return {
          get: (): PaymentGateway | null => {
            if (!created) {
              gw = createPaymentGateway(config);
              created = true;
            }
            return gw;
          },
        };
      },
    },
  ],
})
export class PaymentsModule {}
