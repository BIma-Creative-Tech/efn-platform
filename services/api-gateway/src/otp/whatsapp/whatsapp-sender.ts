/**
 * Port pengiriman WhatsApp (D-11: hanya WhatsApp Cloud API resmi; Baileys dilarang).
 * Implementasi: `CloudApiWhatsAppSender` (produksi) dan `ConsoleWhatsAppSender` (dev, WHATSAPP_ENABLED=false).
 * `to` = member_ref E.164 tanpa `+`. Implementasi tidak boleh me-log nomor utuh maupun kode OTP
 * (kecuali Console di NODE_ENV=development).
 */
export const WHATSAPP_SENDER = Symbol('WHATSAPP_SENDER');

export interface WhatsAppSender {
  sendOtp(to: string, code: string): Promise<void>;
  sendTemplate(to: string, templateName: string, bodyParams: readonly string[]): Promise<void>;
}
