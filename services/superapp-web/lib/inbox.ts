/**
 * Kotak masuk & voucher kampanye (G50; BFF `GET /v1/me/inbox`, src/campaign/campaign-inbox.controller.ts). MURNI.
 * Bentuk: `{ items: [{ id, title, body, valid_to, created_at, voucher: { code, mode } | null }] }`.
 * Kode voucher = data sensitif ringan: tidak pernah di-log; di UI hanya ke pemilik (BFF sudah menyaring).
 */
export type VoucherMode = 'anonymous_code' | 'public_code';

export interface InboxItem {
  id: string;
  title: string;
  body: string;
  valid_to: string;
  created_at: string;
  voucher: { code: string; mode: VoucherMode } | null;
}

const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const CODE_RE = /^[\x21-\x7E]{1,64}$/; // ASCII tercetak tanpa spasi

function isoOrNull(v: unknown): string | null {
  if (typeof v !== 'string' || v.length > 40 || Number.isNaN(Date.parse(v))) return null;
  return v;
}

export function parseInbox(data: unknown): InboxItem[] {
  const list = data && typeof data === 'object' ? (data as { items?: unknown }).items : null;
  if (!Array.isArray(list)) return [];
  const out: InboxItem[] = [];
  for (const it of list.slice(0, 50)) {
    if (!it || typeof it !== 'object') continue;
    const r = it as Record<string, unknown>;
    const validTo = isoOrNull(r.valid_to);
    const createdAt = isoOrNull(r.created_at);
    if (typeof r.id !== 'string' || !ID_RE.test(r.id) || typeof r.title !== 'string' || typeof r.body !== 'string' || !validTo || !createdAt) continue;
    let voucher: InboxItem['voucher'] = null;
    const v = r.voucher;
    if (v && typeof v === 'object') {
      const vv = v as Record<string, unknown>;
      if (typeof vv.code === 'string' && CODE_RE.test(vv.code) && (vv.mode === 'anonymous_code' || vv.mode === 'public_code')) {
        voucher = { code: vv.code, mode: vv.mode };
      }
    }
    out.push({ id: r.id, title: r.title.slice(0, 200), body: r.body.slice(0, 2000), valid_to: validTo, created_at: createdAt, voucher });
  }
  return out;
}

/** Jumlah item lebih baru dari penanda terakhir dilihat (ISO). Penanda tidak valid -> semua dianggap baru. */
export function countNew(items: InboxItem[], lastSeenIso: string | null): number {
  const seen = lastSeenIso ? Date.parse(lastSeenIso) : Number.NaN;
  if (Number.isNaN(seen)) return items.length;
  return items.filter((i) => Date.parse(i.created_at) > seen).length;
}

/** Item yang `valid_to`-nya sudah lewat (klien memfilter lagi karena halaman bisa terbuka lama). */
export function isExpired(item: InboxItem, nowMs: number): boolean {
  return Date.parse(item.valid_to) <= nowMs;
}

export const INBOX_SEEN_KEY = 'efn_inbox_seen';
