import { Logger } from '@nestjs/common';
import { maskMsisdn } from '../../common/pii/pii';
import { WhatsAppSender } from './whatsapp-sender';

/**
 * Sender untuk dev/test saat `WHATSAPP_ENABLED=false`. Tidak mengirim apa pun ke Meta.
 * Kode OTP HANYA dicetak bila `revealCodes` = true, yang di-set hanya untuk NODE_ENV=development.
 * Di lingkungan lain kode tidak pernah masuk log.
 */
export class ConsoleWhatsAppSender implements WhatsAppSender {
  private readonly logger = new Logger('WhatsAppConsole');

  constructor(private readonly revealCodes: boolean) {}

  sendOtp(to: string, code: string): Promise<void> {
    if (this.revealCodes) {
      this.logger.warn(`[DEV ONLY] OTP untuk ${maskMsisdn(to)}: ${code}`);
    } else {
      this.logger.log(`OTP untuk ${maskMsisdn(to)} tidak dikirim (WHATSAPP_ENABLED=false)`);
    }
    return Promise.resolve();
  }

  sendTemplate(to: string, templateName: string, bodyParams: readonly string[]): Promise<void> {
    this.logger.log(`Template ${templateName} untuk ${maskMsisdn(to)} (${bodyParams.length} parameter) tidak dikirim (mode console)`);
    return Promise.resolve();
  }
}
