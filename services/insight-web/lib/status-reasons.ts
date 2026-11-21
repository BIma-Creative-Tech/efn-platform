/**
 * Alasan status (kolom "Keterangan"): fungsi MURNI yang menjelaskan, dengan angka, mengapa sebuah baris berstatus
 * Perlu dicek / Bermasalah / n/a (keputusan pemilik proyek 21 Nov 2026, poin 9). Tidak ada I/O; dipakai loader tabel ESB
 * (sehingga ikut ke CSV) dan halaman supermarket. Ambang selalu dari lib/thresholds.ts / lib/sm-thresholds.ts (satu sumber).
 */
import { fmtAge, fmtNumber, fmtPct, fmtQty } from './format';
import type { SmThresholds } from './sm-thresholds';
import { T, type Status } from './thresholds';

const q = (n: number): string => fmtQty(n);
const p = (n: number): string => fmtPct(n);

export interface SellThroughInput {
  status: Status;
  pct: number | null;
  sold: number;
  /** Pasokan = diterima dari CK + non-CK + produksi toko + transfer masuk - transfer keluar. */
  supply: number;
  stOut?: number;
  stIn?: number;
  /** Cabang berjenis CK (tidak menjual). */
  isCk?: boolean;
}

/** Sell-through (lib/thresholds.ts sellThroughStatus): < 70% bermasalah, 70-85% perlu dicek, >= 85% baik. */
export function sellThroughReason(i: SellThroughInput): string {
  if (i.isCk) return 'Cabang CK: tidak menjual, sell-through tidak berlaku.';
  if (i.pct === null || !Number.isFinite(i.pct)) {
    if (i.supply <= 0 && i.sold > 0) {
      return `n/a: terjual ${q(i.sold)} tetapi tidak ada pasokan tercatat pada periode ini (stok berasal dari sebelum periode).`;
    }
    if (i.supply <= 0) return 'n/a: tidak ada pasokan dan penjualan tercatat pada periode ini.';
    return 'n/a: sell-through tidak dapat dihitung.';
  }
  const out = (i.stOut ?? 0) > 0 ? `; transfer keluar ${q(i.stOut ?? 0)} mengurangi pasokan` : '';
  const basis = `terjual ${q(i.sold)} dari pasokan ${q(i.supply)}`;
  if (i.status === 'bad') return `Sell-through ${p(i.pct)} < batas ${T.sellThroughWarn}%; ${basis}${out}.`;
  if (i.status === 'warn') return `Sell-through ${p(i.pct)} < target ${T.sellThroughGood}% (batas bermasalah ${T.sellThroughWarn}%); ${basis}${out}.`;
  if (i.pct > 100) return `Sell-through ${p(i.pct)} melebihi 100%: ${basis}; sebagian terjual dari stok awal yang tidak tercatat di data.`;
  return `Sell-through ${p(i.pct)} >= target ${T.sellThroughGood}%; ${basis}.`;
}

export interface GapInput {
  status: Status;
  ckOut: number;
  inCk: number;
}

/** Selisih kirim CK vs terima toko per produk-hari (gapStatus: > 10% perlu dicek, > 20% bermasalah). */
export function gapReason(i: GapInput): string {
  const gap = i.ckOut - i.inCk;
  if (i.ckOut <= 0) {
    return `Diterima toko ${q(i.inCk)} tanpa kiriman CK tercatat pada hari ini (kirim hari lain, atau CK beda PT sehingga tidak terlihat).`;
  }
  const pct = (Math.abs(gap) / i.ckOut) * 100;
  const dir = gap > 0 ? 'diterima toko kurang dari dikirim' : 'diterima toko melebihi dikirim';
  const head = `Dikirim CK ${q(i.ckOut)}, diterima toko ${q(i.inCk)}: ${dir} ${q(Math.abs(gap))} (${p(pct)} dari kiriman)`;
  if (i.status === 'bad') return `${head} > batas ${T.gapBadPct}%. Dapat karena lead time lintas hari atau CK/toko beda PT.`;
  if (i.status === 'warn') return `${head} > batas ${T.gapWarnPct}% (bermasalah di atas ${T.gapBadPct}%).`;
  return `${head} <= batas ${T.gapWarnPct}%.`;
}

/** Kesegaran data (freshnessStatus): <= 26 jam baik, <= 48 jam perlu dicek, lebih dari itu bermasalah. */
export function freshnessReason(hours: number | null, status: Status): string {
  if (hours === null || !Number.isFinite(hours)) return 'Umur data tidak diketahui: belum ada pembaruan yang tercatat.';
  const age = fmtAge(hours);
  if (status === 'bad') return `Data terakhir ${age} (${fmtNumber(hours, 0)} jam) > batas ${T.freshnessBadHours} jam: ekstraksi kemungkinan terhenti.`;
  if (status === 'warn') return `Data terakhir ${age} (${fmtNumber(hours, 0)} jam) > ${T.freshnessWarnHours} jam: perlu dicek, bermasalah di atas ${T.freshnessBadHours} jam.`;
  return `Data terakhir ${age} (${fmtNumber(hours, 0)} jam) <= ${T.freshnessWarnHours} jam.`;
}

/** Ambang "makin besar makin buruk" (OOS, waste, WAPE, bias mutlak, ...). */
function upIsBadReason(name: string, v: number | null, status: Status, warn: number, bad: number, abs = false): string {
  if (v === null || !Number.isFinite(v)) return `n/a: ${name} tidak dapat dihitung (data pembanding belum ada).`;
  const val = abs ? Math.abs(v) : v;
  const shown = `${name} ${abs ? '(mutlak) ' : ''}${p(val)}`;
  if (status === 'bad') return `${shown} > batas bermasalah ${p(bad)}.`;
  if (status === 'warn') return `${shown} > batas perlu dicek ${p(warn)} (bermasalah di atas ${p(bad)}).`;
  return `${shown} <= batas ${p(warn)}.`;
}

export const wapeReason = (v: number | null, status: Status, t: SmThresholds): string => upIsBadReason('WAPE', v, status, t.wapeWarn, t.wapeBad);
export const biasReason = (v: number | null, status: Status, t: SmThresholds): string => {
  const base = upIsBadReason('Bias', v, status, t.biasWarn, t.biasBad, true);
  if (v === null || status === 'good') return base;
  return `${base} ${v > 0 ? 'Forecast cenderung terlalu tinggi (risiko stok menumpuk).' : 'Forecast cenderung terlalu rendah (risiko stok habis).'}`;
};
export const oosReason = (v: number | null, status: Status, t: SmThresholds): string => upIsBadReason('OOS rate', v, status, t.oosWarn, t.oosBad);
export const wasteReason = (v: number | null, status: Status, t: SmThresholds): string => upIsBadReason('Waste', v, status, t.wasteWarn, t.wasteBad);

/**
 * Status outlet pada halaman Eksekutif (lib/sm-vm.ts buildOutletRows): terburuk dari status waste dan penurunan omzet
 * (<= batas, mis. -5%) dibanding periode lalu. `dropPct` = ambang negatif (OUTLET_DROP_PCT).
 */
export function outletReason(
  o: { status: Status; wastePct: number | null; revDeltaPct: number | null },
  t: SmThresholds,
  wasteStatus: Status,
  dropPct: number,
): string {
  const parts: string[] = [];
  if (o.wastePct !== null && (wasteStatus === 'warn' || wasteStatus === 'bad')) parts.push(wasteReason(o.wastePct, wasteStatus, t));
  if (o.revDeltaPct !== null && o.revDeltaPct <= dropPct) {
    parts.push(`Omzet turun ${p(Math.abs(o.revDeltaPct))} dibanding periode lalu (batas penurunan ${p(Math.abs(dropPct))}).`);
  }
  if (parts.length > 0) return parts.join(' ');
  if (o.wastePct === null && o.revDeltaPct === null) return 'n/a: belum ada data waste dan pembanding omzet.';
  return 'Tidak ada sinyal masalah: waste dalam batas dan omzet tidak turun melewati batas.';
}

/** Harga rak vs median pasar (sm-kpi priceStatus): jelaskan selisih, ambang, dan jumlah sampel. */
export function priceReason(diffPct: number | null, sampleN: number, status: Status, t: SmThresholds): string {
  if (diffPct === null || !Number.isFinite(diffPct) || sampleN < t.priceMinSamples) {
    return `Sampel harga pasar ${sampleN} < minimum ${t.priceMinSamples}: status belum bisa ditentukan.`;
  }
  const d = p(Math.abs(diffPct));
  if (status === 'bad') return `Harga rak ${d} di atas median pasar > batas bermasalah ${p(t.priceBad)} (${sampleN} sampel).`;
  if (status === 'warn') return `Harga rak ${d} di atas median pasar > batas perlu dicek ${p(t.priceWarn)} (${sampleN} sampel).`;
  if (diffPct < -t.priceBelow) return `Harga rak ${d} di bawah median pasar (batas ${p(t.priceBelow)}): margin mungkin terbuang (${sampleN} sampel).`;
  return `Harga rak dalam kisaran median pasar (${diffPct >= 0 ? '+' : '-'}${d}; ${sampleN} sampel).`;
}

export type ExpiryKind = 'kedaluwarsa' | 'markdown_sekarang' | 'markdown_aktif' | 'pantau';

/** Lot near-expiry: `daysLeft` null = tidak diketahui. */
export function expiryReason(kind: ExpiryKind, daysLeft: number | null, valueCost: number): string {
  const left = daysLeft === null ? 'sisa umur tidak diketahui' : daysLeft < 0 ? `lewat ${fmtNumber(Math.abs(daysLeft), 0)} hari` : `sisa ${fmtNumber(daysLeft, 0)} hari`;
  const value = `nilai HPP Rp ${fmtNumber(Math.round(valueCost), 0)}`;
  switch (kind) {
    case 'kedaluwarsa':
      return `Lot sudah melewati tanggal kedaluwarsa (${left}); ${value} berisiko menjadi susut.`;
    case 'markdown_sekarang':
      return `Sudah masuk jendela markdown (${left}) tetapi markdown belum aktif: perlu diputuskan sekarang; ${value}.`;
    case 'markdown_aktif':
      return `Markdown sudah aktif (${left}); ${value} masih di stok.`;
    default:
      return `Belum masuk jendela markdown (${left}); dipantau; ${value}.`;
  }
}

/** Status usulan order terhadap batas (cut-off) persetujuan (sm-kpi cutoffInfo). `urgentMin` = ambang "Segera" (menit). */
export function cutoffReason(i: { status: Status; countdown: string; uncertain: boolean }, state: string, urgentMin: number): string {
  if (i.uncertain) return 'Data pembaruan usulan sudah melewati batas usang: status tidak dapat dipastikan.';
  switch (state) {
    case 'approved':
      return 'Usulan sudah disetujui.';
    case 'rejected':
      return 'Usulan ditolak: perlu ditinjau ulang sebelum hari kirim.';
    case 'expired':
      return 'Batas persetujuan terlewat tanpa persetujuan: usulan tidak dikirim otomatis.';
    case 'draft':
    case 'proposed':
      return i.status === 'bad'
        ? `Belum disetujui dan batas persetujuan tinggal <= ${urgentMin} menit${i.countdown ? ` (${i.countdown})` : ''}.`
        : `Belum disetujui; batas persetujuan ${i.countdown || 'belum diketahui'}.`;
    default:
      return 'Status usulan tidak diketahui.';
  }
}

/** PO supplier: terlambat = tanggal terjanji sudah lewat dan belum diterima. */
export function poReason(late: boolean, plannedDate: string | null): string {
  if (late) return `Tanggal terjanji ${plannedDate ?? '(tidak diketahui)'} sudah lewat dan barang belum diterima penuh.`;
  return plannedDate ? `Belum jatuh tempo: tanggal terjanji ${plannedDate}.` : 'Belum ada tanggal terjanji.';
}

/** Fill rate: naik = baik (>= good baik, >= warn perlu dicek, di bawahnya bermasalah). */
export function fillReason(v: number | null, status: Status, t: SmThresholds): string {
  if (v === null || !Number.isFinite(v)) return 'n/a: belum ada usulan disetujui pada periode ini.';
  if (status === 'bad') return `Fill rate ${p(v)} < batas bermasalah ${p(t.fillWarn)}: kiriman jauh di bawah qty yang disetujui.`;
  if (status === 'warn') return `Fill rate ${p(v)} < target ${p(t.fillGood)} (bermasalah di bawah ${p(t.fillWarn)}).`;
  return `Fill rate ${p(v)} >= target ${p(t.fillGood)}.`;
}
