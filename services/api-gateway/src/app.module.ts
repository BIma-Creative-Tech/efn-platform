import { Module } from '@nestjs/common';
import { APP_FILTER } from '@nestjs/core';
import { ConfigModule } from '@nestjs/config';
import { TerminusModule } from '@nestjs/terminus';
import { AuthModule } from './auth/auth.module';
import { CommonModule } from './common/common.module';
import { ContractExceptionFilter } from './common/errors/contract-exception.filter';
import { secretsLoader } from './config/secrets.loader';
import { HealthController } from './health/health.controller';
import { LoyaltyModule } from './loyalty/loyalty.module';
import { MarketScoutModule } from './market-scout/market-scout.module';
import { NotificationModule } from './notification/notification.module';
import { OtpModule } from './otp/otp.module';
import { PaymentsModule } from './payments/payments.module';
import { PosModule } from './pos/pos.module';
import { PrivacyModule } from './privacy/privacy.module';

/**
 * Modul root. Bounded context (docs/04_SERVICE_CONTRACTS.md):
 * pos (§3.1/§3.4), auth (§3.2, §8.1–8.2), market-scout (§3.3), loyalty + notification (consumer §4),
 * privacy (§8.4/§8.7/§8.9: consent, DSAR, erasure), payments (§8.3).
 * Modul berikutnya (cart-checkout, mgm, connectors/webhook) ditambahkan sesuai roadmap (docs/01 §7).
 */
@Module({
  imports: [
    // SEC-29: secret tidak diduplikasi ke ConfigService (tidak dipakai); AppConfig membaca env/file sendiri.
    ConfigModule.forRoot({
      isGlobal: true,
      load: [
        (): Record<string, never> => {
          secretsLoader();
          return {};
        },
      ],
    }),
    TerminusModule,
    CommonModule,
    OtpModule,
    LoyaltyModule,
    NotificationModule,
    PosModule,
    AuthModule,
    MarketScoutModule,
    PrivacyModule,
    PaymentsModule,
  ],
  controllers: [HealthController],
  providers: [{ provide: APP_FILTER, useClass: ContractExceptionFilter }],
})
export class AppModule {}
