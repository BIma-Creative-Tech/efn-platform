import { Provider } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { EmailModule } from '../notification/email/email.module';
import { EmailNotifier } from '../notification/email/email.notifier';
import { WaUnofficialModule } from '../notification/wa-unofficial/wa-unofficial.module';
import { WaUnofficialNotifier } from '../notification/wa-unofficial/wa-unofficial.notifier';
import type { PushSender } from '../promo/promo.service';
import { ConsolePushSender, ExpoPushSender } from '../promo/push-sender';
import { EMAIL_NOTIFIER, G50_PUSH_SENDER, WA_UNOFFICIAL_NOTIFIER } from './channel-ports';

/**
 * SATU-SATUNYA titik ikat ke implementasi BFF-B (G50-B: `email.notifier.ts`, `wa-unofficial.notifier.ts`; kedua modul
 * mengekspor kelasnya). Tipe kelas BFF-B harus kompatibel dengan port di `channel-ports.ts`.
 * Push: Expo bila bukan dev (egress allowlist §18.2), dev = console (tanpa pengiriman nyata) — pola PromoModule.
 */
export const CHANNEL_BINDING_IMPORTS = [EmailModule, WaUnofficialModule];

export const channelBindingProviders: Provider[] = [
  { provide: EMAIL_NOTIFIER, useExisting: EmailNotifier },
  { provide: WA_UNOFFICIAL_NOTIFIER, useExisting: WaUnofficialNotifier },
  {
    provide: G50_PUSH_SENDER,
    inject: [AppConfig],
    useFactory: (config: AppConfig): PushSender => (config.isDevelopment ? new ConsolePushSender() : new ExpoPushSender(config.expoPushUrl)),
  },
];
