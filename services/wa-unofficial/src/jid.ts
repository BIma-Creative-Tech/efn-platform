import { E164_RE } from './contract';

/** Pemetaan nomor ↔ JID WhatsApp. Hanya nomor Indonesia (`^62…`) yang dipakai service. */
export function e164ToJid(e164: string): string {
  if (!E164_RE.test(e164)) throw new Error('nomor tujuan tidak valid');
  return `${e164}@s.whatsapp.net`;
}

/** `628123…(:device)@s.whatsapp.net` → `628123…`; selain itu (grup, broadcast, `@lid`) → null. */
export function jidToE164(jid: unknown): string | null {
  if (typeof jid !== 'string') return null;
  const m = /^(\d{8,15})(?::\d+)?@s\.whatsapp\.net$/.exec(jid);
  return m && E164_RE.test(m[1]) ? m[1] : null;
}

/**
 * Pengirim pesan masuk → nomor. WhatsApp kini dapat mengalamatkan pengguna dengan LID (`…@lid`) yang menyembunyikan
 * nomor; Baileys (versi tertentu) menyertakan alternatif berbasis nomor di `key.senderPn` / `key.remoteJidAlt` /
 * `key.participantPn`. Nama field ini BELUM TERVERIFIKASI untuk versi yang di-pin (lihat README) — bila tidak ada,
 * STOP dari alamat LID tidak bisa dipetakan (dicatat metrik `stop_unmapped`).
 */
export function senderE164(key: Readonly<Record<string, unknown>>): string | null {
  const remote = key.remoteJid;
  if (typeof remote === 'string' && (remote.endsWith('@g.us') || remote === 'status@broadcast' || remote.endsWith('@broadcast') || remote.endsWith('@newsletter'))) {
    return null;
  }
  for (const k of ['remoteJid', 'senderPn', 'remoteJidAlt', 'participantPn']) {
    const v = jidToE164(key[k]);
    if (v) return v;
  }
  return null;
}
