import { Module } from '@nestjs/common';
import { WaUnofficialConfig } from './wa-unofficial.config';
import { WaUnofficialNotifier } from './wa-unofficial.notifier';
import { AmqpWaPublisher, WA_QUEUE_PUBLISHER } from './wa-unofficial.publisher';
import { WaUnofficialResultConsumer } from './wa-unofficial-result.consumer';
import { WaUnofficialRetentionJob } from './wa-unofficial-retention.job';

/**
 * Sisi BFF kanal WhatsApp tidak resmi (ADR-044 Proposed, K18-01, docs/04 §28.7/§28.10). Diregistrasi di `AppModule`
 * oleh BFF-A. Ekspor: `WaUnofficialNotifier`. BFF TIDAK memuat library Baileys — pengiriman ada di container
 * terisolasi `services/wa-unofficial`. OTP/pesanan/pembayaran/poin tetap Cloud API resmi (`src/otp/whatsapp`).
 */
@Module({
  providers: [
    { provide: WaUnofficialConfig, useFactory: (): WaUnofficialConfig => WaUnofficialConfig.fromProcessEnv() },
    AmqpWaPublisher,
    { provide: WA_QUEUE_PUBLISHER, useExisting: AmqpWaPublisher },
    WaUnofficialNotifier,
    WaUnofficialResultConsumer,
    WaUnofficialRetentionJob,
  ],
  exports: [WaUnofficialNotifier],
})
export class WaUnofficialModule {}
