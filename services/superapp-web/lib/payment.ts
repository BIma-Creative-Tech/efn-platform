/**
 * Metode bayar checkout & "Bayar di kasir" (docs/04 §12.2, W-30, E-45) — helper MURNI (client, Route Handler, test).
 *
 * - `payment_method` klien: `qris` (default) | `counter`. Server/BFF tetap sumber validasi.
 * - Kode bayar (`counter_code`) adalah RAHASIA ringan (bukan kode ambil): tidak pernah ke URL, storage, log, atau analytics.
 *   Hanya hidup di memori komponen halaman pesanan.
 * - ASUMSI kontrak: data kanal dari BFF boleh membawa `payment_modes: string[]`. Bila belum ada (undefined),
 *   `parsePaymentModes` mengembalikan null = "tidak diketahui" dan UI tetap menawarkan opsi untuk keranjang satu PT;
 *   keputusan akhir ada di BFF (`422 payment_method_unavailable`).
 */

export type PaymentMethod = 'qris' | 'counter';
export const PAYMENT_METHODS: readonly PaymentMethod[] = ['qris', 'counter'];

export function isPaymentMethod(v: unknown): v is PaymentMethod {
  return v === 'qris' || v === 'counter';
}

/** `payment_modes` kanal: nilai tak dikenal dibuang; bukan array -> null (tidak diketahui). */
export function parsePaymentModes(v: unknown): PaymentMethod[] | null {
  if (!Array.isArray(v)) return null;
  const out: PaymentMethod[] = [];
  for (const m of v) if (isPaymentMethod(m) && !out.includes(m)) out.push(m);
  return out;
}

export interface MethodOption {
  method: PaymentMethod;
  enabled: boolean;
  /** Penjelasan Bahasa Indonesia bila `enabled` false (atau catatan). */
  reason: string | null;
}

export const COUNTER_MULTI_PART_REASON =
  'Bayar di kasir hanya untuk pesanan dari satu PT. Keranjang Anda berisi barang dari beberapa PT, jadi akan menjadi beberapa pesanan terpisah. Pisahkan pesanan per PT untuk membayar di kasir.';
export const COUNTER_UNAVAILABLE_REASON = 'Bayar di kasir belum tersedia untuk toko ini.';
export const QRIS_UNAVAILABLE_REASON = 'Pembayaran QRIS belum tersedia. Saat ini pembayaran dilakukan di kasir.';

/**
 * Opsi metode bayar untuk keranjang. `groupModes` = `payment_modes` tiap kanal dalam keranjang
 * (null = kanal tidak mengirim field). Aturan:
 *  - qris: aktif bila tidak ada kanal yang SECARA EKSPLISIT tidak memuat `qris`.
 *  - counter: hanya bila TEPAT satu kelompok/PT dan kanalnya tidak SECARA EKSPLISIT menolak `counter`.
 */
export function paymentOptions(groupModes: ReadonlyArray<readonly PaymentMethod[] | null>): MethodOption[] {
  const parts = groupModes.length;
  const qrisDenied = groupModes.some((m) => m !== null && !m.includes('qris'));
  const counterDenied = groupModes.some((m) => m !== null && !m.includes('counter'));
  const counter: MethodOption =
    parts > 1
      ? { method: 'counter', enabled: false, reason: COUNTER_MULTI_PART_REASON }
      : counterDenied
        ? { method: 'counter', enabled: false, reason: COUNTER_UNAVAILABLE_REASON }
        : { method: 'counter', enabled: parts === 1, reason: null };
  const qris: MethodOption = qrisDenied ? { method: 'qris', enabled: false, reason: QRIS_UNAVAILABLE_REASON } : { method: 'qris', enabled: true, reason: null };
  return [qris, counter];
}

/** Metode awal: QRIS bila boleh, jika tidak "counter" bila boleh, jika tidak null (checkout tidak mungkin). */
export function defaultPaymentMethod(options: readonly MethodOption[]): PaymentMethod | null {
  return options.find((o) => o.method === 'qris' && o.enabled)?.method ?? options.find((o) => o.enabled)?.method ?? null;
}

/** Pilihan tersimpan tetap dipakai hanya bila masih aktif; selain itu jatuh ke default. */
export function resolvePaymentMethod(chosen: PaymentMethod | null, options: readonly MethodOption[]): PaymentMethod | null {
  if (chosen && options.some((o) => o.method === chosen && o.enabled)) return chosen;
  return defaultPaymentMethod(options);
}

// ---------- kode bayar ----------

const COUNTER_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
const COUNTER_CODE_RE = /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$/;

/**
 * Kode bayar 8 karakter alfabet §10.2 (tanpa I, L, O, 0, 1). Menerima `ABCD-EFGH`, `abcdefgh`, `ABCD EFGH`;
 * mengembalikan format tampil `ABCD-EFGH` atau null bila tidak valid.
 */
export function formatCounterCode(v: unknown): string | null {
  if (typeof v !== 'string' || v.length > 16) return null;
  const compact = v.replace(/[\s-]/g, '').toUpperCase();
  if (!COUNTER_CODE_RE.test(compact)) return null;
  return `${compact.slice(0, 4)}-${compact.slice(4)}`;
}

/** Untuk pembacaan layar: tiap karakter dieja, mis. "A B C D, E F G H". */
export function spellCounterCode(display: string): string {
  const compact = display.replace(/-/g, '');
  if (![...compact].every((c) => COUNTER_ALPHABET.includes(c))) return '';
  return `${[...compact.slice(0, 4)].join(' ')}, ${[...compact.slice(4)].join(' ')}`;
}

/**
 * Pengumuman hitung mundur untuk region aria-live: teks berubah hanya tiap menit penuh (> 1 menit), lalu satu kali
 * "kurang dari satu menit", lalu "habis" — supaya pembaca layar tidak membacakan tiap detik.
 */
export function countdownAnnouncement(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s <= 0) return 'Waktu pembayaran habis.';
  if (s <= 60) return 'Sisa waktu kurang dari satu menit.';
  return `Sisa waktu sekitar ${Math.ceil(s / 60)} menit.`;
}

/** Teks hitung mundur ramah pembaca layar: "12 menit 5 detik". */
export function spokenDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m} menit ${s % 60} detik` : `${s} detik`;
}
