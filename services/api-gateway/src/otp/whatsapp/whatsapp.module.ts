import { Logger, Module } from '@nestjs/common';
import { AppConfig } from '../../config/app-config';
import { MetricsService } from '../../common/metrics/metrics.service';
import { CloudApiWhatsAppSender } from './cloud-api-whatsapp.sender';
import { ConsoleWhatsAppSender } from './console-whatsapp.sender';
import { WHATSAPP_SENDER, WhatsAppSender } from './whatsapp-sender';

export function createWhatsAppSender(config: AppConfig): WhatsAppSender {
  if (config.whatsappEnabled) {
    return new CloudApiWhatsAppSender({
      baseUrl: config.whatsappGraphBaseUrl,
      apiVersion: config.whatsappApiVersion,
      phoneNumberId: config.whatsappPhoneNumberId,
      token: config.whatsappToken,
      otpTemplate: config.whatsappOtpTemplate,
      languageCode: config.whatsappTemplateLanguage,
      otpCopyCodeButton: config.whatsappOtpCopyCodeButton,
      timeoutMs: config.whatsappTimeoutMs,
    });
  }
  if (!config.isDevelopment) {
    new Logger('WhatsApp').warn(
      `WHATSAPP_ENABLED=false pada NODE_ENV=${config.nodeEnv}: OTP dan notifikasi TIDAK terkirim ke pelanggan`,
    );
  }
  return new ConsoleWhatsAppSender(config.isDevelopment);
}

/** Dekorator yang menghitung `efn_whatsapp_send_total{outcome}` tanpa label nomor HP. */
export class MeteredWhatsAppSender implements WhatsAppSender {
  constructor(
    private readonly inner: WhatsAppSender,
    private readonly metrics: MetricsService,
  ) {}

  sendOtp(to: string, code: string): Promise<void> {
    return this.track(this.inner.sendOtp(to, code));
  }

  sendTemplate(to: string, templateName: string, bodyParams: readonly string[]): Promise<void> {
    return this.track(this.inner.sendTemplate(to, templateName, bodyParams));
  }

  private async track(p: Promise<void>): Promise<void> {
    try {
      await p;
      this.metrics.recordWhatsAppSend('success');
    } catch (err) {
      this.metrics.recordWhatsAppSend('failure');
      throw err;
    }
  }
}

@Module({
  providers: [
    {
      provide: WHATSAPP_SENDER,
      inject: [AppConfig, MetricsService],
      useFactory: (config: AppConfig, metrics: MetricsService): WhatsAppSender =>
        new MeteredWhatsAppSender(createWhatsAppSender(config), metrics),
    },
  ],
  exports: [WHATSAPP_SENDER],
})
export class WhatsAppModule {}
