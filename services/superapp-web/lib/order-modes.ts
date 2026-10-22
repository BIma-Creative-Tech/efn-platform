/**
 * Pemilih mode pesan (docs/04 §15.7, G23): Ambil · Pre-order · Makan di tempat · Reservasi · Pesan-antar.
 * MURNI (dipakai Server Component, client, dan test). Mode hanya tampil bila memang relevan untuk brand/kanal:
 *  - Ambil      : ada kanal katalog dengan `fulfilment.pickup`.
 *  - Pre-order  : ada outlet `slot_enabled` (BFF mengirim hanya bila ONLINE_SLOTS_ENABLED, §15.4).
 *  - Makan di tempat : brand punya PT F&B (PB/BCF) -> panduan pindai QR meja (`/meja` hanya lewat QR; tidak ada
 *    tautan langsung yang menjanjikan pesanan tanpa token meja).
 *  - Reservasi  : brand punya PT F&B. Ketersediaan sebenarnya diputuskan BFF (`BOOKING_ENABLED`); halaman reservasi
 *    menampilkan "belum tersedia" bila BFF mematikannya.
 *  - Pesan-antar: SELALU nonaktif ("segera hadir") — delivery belum ada di kontrak (L-88). Tidak bisa diklik.
 * Tidak ada angka/klaim layanan yang ditulis manual (menit siap, metode bayar) — itu dari katalog di tempat lain.
 */
import { brandHasFnb } from './booking';
import type { Brand } from './brands';
import type { CatalogChannel } from './catalog';

export type OrderModeId = 'pickup' | 'preorder' | 'dinein' | 'reservation' | 'delivery';

export interface OrderMode {
  id: OrderModeId;
  label: string;
  /** Kalimat pendek (≤ 60 karakter) di bawah label. */
  hint: string;
  /** null = tidak bisa diklik (nonaktif). */
  href: string | null;
  /** Label status untuk mode nonaktif, mis. "Segera hadir". */
  badge: string | null;
}

export interface OrderModeContext {
  brand: Pick<Brand, 'companies'>;
  /** Kanal katalog brand (null = katalog gagal dimuat -> mode katalog tetap ditawarkan, halaman tujuan menjelaskan). */
  channels: CatalogChannel[] | null;
  /** Kanal yang dipakai tautan `/belanja?channel=` (hanya bila brand punya > 1 kanal). */
  channelParam?: string | null;
}

/** Ada outlet pre-order berjadwal di salah satu kanal. */
export function hasScheduledOutlet(channels: readonly CatalogChannel[] | null): boolean {
  return (channels ?? []).some((c) => c.fulfilment.scheduled === true || c.outlets.some((o) => o.slot_enabled === true));
}

/** Kanal pertama yang punya outlet pre-order (untuk tautan pre-order langsung ke kanal yang benar). */
export function scheduledChannel(channels: readonly CatalogChannel[] | null): CatalogChannel | null {
  return (channels ?? []).find((c) => c.outlets.some((o) => o.slot_enabled === true)) ?? null;
}

function shopHref(channel: string | null | undefined, extra: Record<string, string> = {}): string {
  const p = new URLSearchParams();
  if (channel) p.set('channel', channel);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  const s = p.toString();
  return s ? `/belanja?${s}` : '/belanja';
}

/** Daftar mode untuk brand ini (urutan tetap; mode yang tidak relevan tidak dimasukkan, delivery selalu terakhir). */
export function orderModes(ctx: OrderModeContext): OrderMode[] {
  const fnb = brandHasFnb(ctx.brand);
  const channels = ctx.channels;
  const pickupOk = channels === null || channels.some((c) => c.fulfilment.pickup);
  const out: OrderMode[] = [];
  if (pickupOk) {
    out.push({ id: 'pickup', label: 'Ambil di gerai', hint: 'Pesan & bayar di aplikasi, ambil saat siap', href: shopHref(ctx.channelParam), badge: null });
  }
  if (hasScheduledOutlet(channels)) {
    const sc = scheduledChannel(channels);
    const multi = (channels?.length ?? 0) > 1;
    out.push({
      id: 'preorder',
      label: 'Pre-order',
      hint: fnb ? 'Kue & hampers, pilih jadwal ambil' : 'Pilih jadwal ambil',
      href: shopHref(multi ? sc?.channel_code : ctx.channelParam, { mode: 'preorder' }),
      badge: null,
    });
  }
  if (fnb) {
    out.push({ id: 'dinein', label: 'Makan di tempat', hint: 'Pindai QR di meja, pesan dari ponsel', href: '/meja/cara', badge: null });
    out.push({ id: 'reservation', label: 'Reservasi meja', hint: 'Pilih gerai, tanggal, dan jam', href: '/reservasi', badge: null });
  }
  out.push({ id: 'delivery', label: 'Pesan-antar', hint: 'Diantar ke alamat Anda', href: null, badge: 'Segera hadir' });
  return out;
}

/** `?mode=` yang dikenali halaman katalog (hanya pre-order mengubah tampilan). */
export function parseModeParam(v: unknown): 'preorder' | null {
  return v === 'preorder' ? 'preorder' : null;
}
