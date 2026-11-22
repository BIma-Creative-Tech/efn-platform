/**
 * Pengelompokan kanal pembayaran untuk Marketing F&B (docs/04 §28.15.9, Q-INS-F4 default): ESB tidak punya kolom kanal,
 * jadi kanal ditebak dari nama `payment_method` (dan `payment_type`). Pola di bawah adalah DEFAULT yang harus ditinjau
 * bersama data nyata saat build; tidak cocok = "Lainnya". Semua hasil di UI dilabeli "perkiraan". Murni (tanpa I/O).
 */
export type ChannelId = 'delivery' | 'qris' | 'card' | 'cash' | 'other';

export const CHANNEL_LABEL: Readonly<Record<ChannelId, string>> = {
  delivery: 'Pesan-antar online (GoFood, GrabFood, ShopeeFood)',
  qris: 'QRIS dan dompet digital',
  card: 'Kartu debit/kredit',
  cash: 'Tunai',
  other: 'Lainnya',
};

/** Urutan tampil (online di atas karena itu fokus analisis kanal). */
export const CHANNEL_ORDER: readonly ChannelId[] = ['delivery', 'qris', 'card', 'cash', 'other'];

// Urutan pemeriksaan penting: pesan-antar dulu (nama aplikasi bisa memuat kata "pay"/"wallet"), lalu QRIS/dompet, kartu, tunai.
const RULES: readonly { id: ChannelId; re: RegExp }[] = [
  { id: 'delivery', re: /(go\s*-?\s*food|gojek|grab\s*-?\s*food|grab\b|shopee\s*-?\s*food|online\s*(order|delivery)|delivery)/i },
  { id: 'qris', re: /(qris|ovo|gopay|go\s*-?\s*pay|dana|link\s*aja|shopee\s*pay|e-?\s*wallet|dompet)/i },
  { id: 'card', re: /(debit|credit|kredit|card|kartu|edc|visa|master\s*card)/i },
  { id: 'cash', re: /(cash|tunai)/i },
];

export function classifyChannel(paymentType: string, paymentMethod: string): ChannelId {
  const text = `${paymentMethod} ${paymentType}`;
  for (const r of RULES) {
    if (r.re.test(text)) return r.id;
  }
  return 'other';
}

export interface ChannelRowIn {
  payment_type: string;
  method: string;
  cnt: number;
  amount_sum: number;
}

export interface ChannelTotal {
  id: ChannelId;
  label: string;
  cnt: number;
  amount: number;
  /** Persentase terhadap total semua kanal; null bila total <= 0. */
  sharePct: number | null;
}

/** Jumlahkan baris metode bayar per kanal; kanal tanpa baris tetap dikembalikan (nol) agar tabel stabil. */
export function groupChannels(rows: readonly ChannelRowIn[]): ChannelTotal[] {
  const acc = new Map<ChannelId, { cnt: number; amount: number }>(CHANNEL_ORDER.map((id) => [id, { cnt: 0, amount: 0 }]));
  for (const r of rows) {
    const a = acc.get(classifyChannel(r.payment_type, r.method));
    if (!a) continue;
    a.cnt += Number(r.cnt) || 0;
    a.amount += Number(r.amount_sum) || 0;
  }
  const total = Array.from(acc.values()).reduce((s, v) => s + v.amount, 0);
  return CHANNEL_ORDER.map((id) => {
    const v = acc.get(id) as { cnt: number; amount: number };
    return { id, label: CHANNEL_LABEL[id], cnt: v.cnt, amount: v.amount, sharePct: total > 0 ? (v.amount / total) * 100 : null };
  });
}
