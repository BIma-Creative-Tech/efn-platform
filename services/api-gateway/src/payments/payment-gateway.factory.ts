import { AppConfig } from '../config/app-config';
import { PublicHttp, createPublicHttp } from '../common/net/public-https';
import { MidtransGateway } from './gateways/midtrans.gateway';
import { MockGateway } from './gateways/mock.gateway';
import { XenditGateway } from './gateways/xendit.gateway';
import { PaymentGateway } from './payment-gateway';

/**
 * Pilih adapter dari `PAYMENT_PROVIDER` (docs/04 §8.3). `null` = `disabled` (default production sampai ADR-016).
 * Egress ke vendor memakai `createPublicHttp` (SEC-23: root store publik saja, bukan CA internal).
 * `payment_provider_api_key` hanya dibaca untuk xendit/midtrans.
 */
export function createPaymentGateway(config: AppConfig, http: PublicHttp = createPublicHttp()): PaymentGateway | null {
  const provider = config.paymentProvider;
  switch (provider) {
    case 'mock':
      return new MockGateway(() => config.webhookPaymentSecret);
    case 'xendit':
      return new XenditGateway(
        {
          apiKey: config.paymentProviderApiKey,
          callbackToken: config.webhookPaymentSecret,
          baseUrl: config.paymentProviderBaseUrl,
          timeoutMs: config.paymentTimeoutMs,
          experimental: config.paymentProviderExperimental,
        },
        http,
      );
    case 'midtrans':
      return new MidtransGateway(
        {
          serverKey: config.paymentProviderApiKey,
          baseUrl: config.paymentProviderBaseUrl,
          acquirer: config.midtransQrisAcquirer,
          timeoutMs: config.paymentTimeoutMs,
          experimental: config.paymentProviderExperimental,
        },
        http,
      );
    default:
      return null;
  }
}
