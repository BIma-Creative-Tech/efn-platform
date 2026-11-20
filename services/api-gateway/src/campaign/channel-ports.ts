import { Logger } from '@nestjs/common';
import { createHmac } from 'node:crypto';
import { Queryable } from '../common/db/db';
import { PII_AAD, PiiCipher } from '../common/pii/crypto';
import type { PushSender } from '../promo/promo.service';

/**
 * Port kanal G50 (docs/04 §28.7–§28.9). Email & WA unofficial diimplementasikan BFF-B
 * (`src/notification/email`, `src/notification/wa-unofficial`); konteks campaign/wishlist hanya bergantung pada
 * antarmuka di bawah (diikat di `channel-bindings.ts`). Kedua notifier memeriksa flag kanal + consent sendiri dan
 * membuat pesan antrean TANPA alamat email/nomor mentah selain yang diizinkan kontrak (§28.7 L-159).
 */
export const EMAIL_NOTIFIER = Symbol('G50_EMAIL_NOTIFIER');
export const WA_UNOFFICIAL_NOTIFIER = Symbol('G50_WA_UNOFFICIAL_NOTIFIER');
/** Pengirim push Expo khusus G50 (instans terpisah dari PromoModule; implementasi sama). */
export const G50_PUSH_SENDER = Symbol('G50_PUSH_SENDER');

export type EmailEnqueueResult = 'queued' | 'skipped_disabled' | 'skipped_no_consent' | 'skipped_no_email';
export type WaEnqueueResult = 'queued' | 'skipped_disabled' | 'skipped_no_consent' | 'skipped_no_phone';

export interface EmailNotifierPort {
  enqueue(input: {
    memberId: string;
    source: 'campaign' | 'restock' | 'price_alert' | 'transactional';
    sourceRef: string;
    template: string;
    params: Record<string, string | number>;
  }): Promise<EmailEnqueueResult>;
}

export interface WaUnofficialNotifierPort {
  enqueue(input: {
    memberId: string;
    category: 'promo' | 'notify_restock' | 'price_alert';
    source: string;
    sourceRef: string;
    template: string;
    params: Record<string, string | number>;
    expiresAt: Date;
  }): Promise<WaEnqueueResult>;
}

/**
 * Template allowlist BFF-B (`email-templates.ts`, `wa-unofficial.contract.ts`; parameter di luar daftar → error):
 * - `promo_generic`      (campaign / promo)          { title, body, valid_to? } — `voucher_code` SENGAJA tidak dikirim
 *                                                    (kode bearer hanya di inbox in-app, tidak ke SMTP/WhatsApp)
 * - `restock_available`  (restock / notify_restock)  { product_name, outlet_name }
 * - `price_drop`         (price_alert / price_alert) { product_name, new_price_idr }
 */
export const G50_TEMPLATES = { campaign: 'promo_generic', restock: 'restock_available', priceAlert: 'price_drop' } as const;

/** Tanggal untuk teks pesan (WIB, mis. "27 November 2026"); gagal format → ISO tanggal. */
export function formatDateWib(iso: string): string {
  const d = new Date(iso);
  try {
    return d.toLocaleDateString('id-ID', { timeZone: 'Asia/Jakarta', day: 'numeric', month: 'long', year: 'numeric' });
  } catch {
    return d.toISOString().slice(0, 10);
  }
}

/** Rupiah tanpa simbol untuk template (`Rp${new_price_idr}`), mis. 9000 → "9.000". */
export function formatIdr(n: number): string {
  return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
}

export interface PushMessage {
  title: string;
  body: string;
  data: Record<string, unknown>;
}

/** Hash token push = rumus `PromoService.tokenHash` (HMAC redisKeyPepper, prefix `efn:push_token:`). */
export function pushTokenHash(pepper: Buffer, token: string): string {
  return createHmac('sha256', pepper).update(`efn:push_token:${token}`, 'utf8').digest('hex');
}

const logger = new Logger('G50Push');

/**
 * Kirim push Expo ke token aktif para member (batch 100). Token tidak pernah di-log; token `DeviceNotRegistered`
 * dinonaktifkan. Kegagalan kirim tidak dilempar (kanal lain tetap jalan) — dikembalikan sebagai hitungan.
 */
export async function sendPushToMembers(
  q: Queryable,
  cipher: PiiCipher,
  pepper: Buffer,
  push: PushSender | null,
  memberIds: readonly string[],
  msg: PushMessage,
): Promise<{ tokens: number; sent: number }> {
  if (!push || memberIds.length === 0) return { tokens: 0, sent: 0 };
  const res = await q.query<{ token_enc: Buffer }>(
    `SELECT token_enc FROM platform.push_token WHERE member_id = ANY($1::uuid[]) AND disabled_at IS NULL`,
    [[...memberIds]],
  );
  const tokens: string[] = [];
  for (const r of res.rows) {
    try {
      tokens.push(cipher.decrypt(r.token_enc, PII_AAD.PUSH_TOKEN));
    } catch {
      // kunci pensiun / baris rusak: lewati
    }
  }
  let sent = 0;
  for (let i = 0; i < tokens.length; i += 100) {
    const batch = tokens.slice(i, i + 100);
    try {
      const r = await push.send(batch.map((to) => ({ to, title: msg.title, body: msg.body, data: msg.data })));
      sent += r.ok;
      if (r.invalidTokens.length > 0) {
        await q.query(`UPDATE platform.push_token SET disabled_at = now() WHERE token_hash = ANY($1::text[])`, [
          r.invalidTokens.map((t) => pushTokenHash(pepper, t)),
        ]);
      }
    } catch (err) {
      logger.warn(`Push G50 gagal (${batch.length} token): ${err instanceof Error ? err.message : 'error'}`);
    }
  }
  return { tokens: tokens.length, sent };
}
