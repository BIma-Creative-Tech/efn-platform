import { Logger } from '@nestjs/common';
import { PublicHttp, createPublicHttp } from '../common/net/public-https';
import { PushSender } from './promo.service';

/**
 * Pengirim push lewat Expo Push API (docs/04 §18.2; egress hanya ke `EXPO_PUSH_URL`, trust store publik SEC-23).
 * Token dikirim dalam body HTTPS, tidak pernah di-log. Tiket `DeviceNotRegistered` → token dinonaktifkan pemanggil.
 */
export class ExpoPushSender implements PushSender {
  private readonly logger = new Logger('ExpoPush');
  constructor(
    private readonly url: string,
    private readonly http: PublicHttp = createPublicHttp(),
  ) {}

  async send(messages: { to: string; title: string; body: string; data: Record<string, unknown> }[]): Promise<{ ok: number; failed: number; invalidTokens: string[] }> {
    if (messages.length === 0) return { ok: 0, failed: 0, invalidTokens: [] };
    const res = await this.http(this.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(messages.map((m) => ({ to: m.to, title: m.title, body: m.body, data: m.data, sound: 'default', priority: 'default' }))),
      timeoutMs: 10_000,
    });
    if (!res.ok) {
      this.logger.warn(`Expo push HTTP ${res.status}`);
      return { ok: 0, failed: messages.length, invalidTokens: [] };
    }
    const body = (await res.json().catch(() => null)) as { data?: { status?: string; details?: { error?: string } }[] } | null;
    const tickets = Array.isArray(body?.data) ? body!.data : [];
    let ok = 0;
    const invalidTokens: string[] = [];
    messages.forEach((m, i) => {
      const t = tickets[i];
      if (t?.status === 'ok') ok += 1;
      else if (t?.details?.error === 'DeviceNotRegistered') invalidTokens.push(m.to);
    });
    return { ok, failed: messages.length - ok, invalidTokens };
  }
}

/** Dev/test tanpa EAS/FCM: tidak mengirim apa pun, hanya mencatat jumlah (tanpa token). */
export class ConsolePushSender implements PushSender {
  private readonly logger = new Logger('PushConsole');
  readonly sent: { to: string; title: string; body: string; data: Record<string, unknown> }[] = [];
  async send(messages: { to: string; title: string; body: string; data: Record<string, unknown> }[]): Promise<{ ok: number; failed: number; invalidTokens: string[] }> {
    this.sent.push(...messages);
    if (messages.length) this.logger.warn(`[DEV] push promo "${messages[0]!.title}" ke ${messages.length} perangkat (tidak dikirim, mode console)`);
    return { ok: messages.length, failed: 0, invalidTokens: [] };
  }
}
