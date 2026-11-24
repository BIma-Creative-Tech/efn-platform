/**
 * Format dan label tampilan modul Production Order (G66). Murni (tanpa I/O), dapat diuji. Status tidak pernah hanya warna:
 * setiap status punya kata (label) dan `tone` untuk <StatusPill> (ikon + kata + warna).
 */
import type { Status } from './thresholds';
import type {
  CalcStatus,
  Dec,
  DemandSource,
  DocKind,
  OutboxItem,
  OutboxStatus,
  Plan,
  PlanStatus,
  PostPhase,
  PostingState,
  PreviewDoc,
  Requirement,
  SuggestionStatus,
  TransferStatus,
} from './prodplan-types';

export interface Labeled {
  label: string;
  tone: Status;
}

export const PLAN_STATUS: Readonly<Record<PlanStatus, Labeled>> = {
  draft: { label: 'Draf', tone: 'neutral' },
  submitted: { label: 'Diajukan', tone: 'warn' },
  approved: { label: 'Disetujui', tone: 'good' },
  posted: { label: 'Sudah diposting', tone: 'good' },
  cancelled: { label: 'Dibatalkan', tone: 'bad' },
};

export const CALC_STATUS: Readonly<Record<CalcStatus, Labeled>> = {
  none: { label: 'Belum dihitung', tone: 'warn' },
  stale: { label: 'Perlu hitung ulang', tone: 'warn' },
  fresh: { label: 'Hasil hitung terbaru', tone: 'good' },
  failed: { label: 'Hitung gagal', tone: 'bad' },
};

export const TRANSFER_STATUS: Readonly<Record<TransferStatus, Labeled>> = {
  proposed: { label: 'Menunggu keputusan', tone: 'warn' },
  approved: { label: 'Disetujui', tone: 'good' },
  rejected: { label: 'Ditolak', tone: 'bad' },
  superseded: { label: 'Digantikan', tone: 'neutral' },
};

export const SUGGESTION_STATUS: Readonly<Record<SuggestionStatus, Labeled>> = {
  open: { label: 'Terbuka', tone: 'warn' },
  accepted: { label: 'Diterima', tone: 'good' },
  rejected: { label: 'Ditolak', tone: 'neutral' },
  superseded: { label: 'Gugur (usulan lain diterima)', tone: 'neutral' },
};

export const OUTBOX_STATUS: Readonly<Record<OutboxStatus, Labeled>> = {
  pending: { label: 'Antre dikirim', tone: 'warn' },
  sending: { label: 'Sedang dikirim', tone: 'warn' },
  sent: { label: 'Terkirim, menunggu konfirmasi', tone: 'warn' },
  confirmed: { label: 'Terkonfirmasi di ESB', tone: 'good' },
  unknown: { label: 'Hasil belum pasti (sedang dicocokkan)', tone: 'warn' },
  failed: { label: 'Gagal', tone: 'bad' },
  delete_pending: { label: 'Antre dibatalkan di ESB', tone: 'warn' },
  deleting: { label: 'Sedang dibatalkan di ESB', tone: 'warn' },
  deleted: { label: 'Dibatalkan di ESB', tone: 'neutral' },
  delete_failed: { label: 'Gagal dibatalkan di ESB', tone: 'bad' },
};

export const DOC_KIND_LABEL: Readonly<Record<DocKind, string>> = {
  simple_transfer: 'Transfer sederhana (satu cabang)',
  goods_transfer_request: 'Permintaan transfer barang (antar cabang)',
  simple_manufacturing: 'Produksi sederhana',
};

export const DEMAND_SOURCE_LABEL: Readonly<Record<DemandSource, string>> = {
  pr: 'Impor PR',
  manual: 'Manual',
  suggestion: 'Usulan batch (diterima)',
};

export const UNMAPPED_REASON_LABEL: Readonly<Record<string, string>> = {
  no_assembly_bom: 'Tidak punya BOM Assembly',
  unknown_product: 'Produk tidak dikenal',
  uom_mismatch: 'Satuan tidak cocok',
};

export const REQUIREMENT_FLAG_LABEL: Readonly<Record<string, string>> = {
  uom_mismatch: 'Satuan BOM berbeda dari master produk (dikeluarkan dari kemasan dan transfer)',
  no_stock_data: 'Tidak ada data stok (dianggap 0)',
  negative_stock: 'Stok negatif di ESB (dianggap 0)',
  pack_rule_on_wip: 'Aturan kemasan utuh dipasang pada produk WIP',
};

/** Kode galat dokumen outbox -> penjelasan (mis. `not_found_after_timeout` = boleh dikirim ulang). */
export const OUTBOX_ERROR_LABEL: Readonly<Record<string, string>> = {
  not_found_after_timeout: 'Dokumen tidak ditemukan di ESB setelah batas waktu; aman dikirim ulang',
  esb_validation: 'ESB menolak isi dokumen',
  esb_auth_failed: 'Login ke ESB gagal',
  esb_unavailable: 'ESB tidak dapat dihubungi',
  // Kode yang benar-benar ditulis worker outbox (outbox.py): sebelumnya jatuh ke teks umum "Galat dari ESB".
  esb_rejected: 'ESB menolak isi dokumen; tidak dikirim ulang otomatis (batalkan rencana atau tangani di ESB)',
  not_sent: 'Belum terkirim (ESB tidak terjangkau); dicoba lagi otomatis',
  sent_unknown: 'ESB tidak menjawab setelah dokumen terkirim; sedang dicocokkan ke ESB',
  doc_not_found: 'Dokumen belum terlihat di ESB; sedang dicocokkan ulang',
  marker_mismatch: 'Dokumen di ESB tidak cocok dengan penanda rencana; hubungi administrator',
  cancelled: 'Tidak dikirim karena rencana dibatalkan',
  client_error: 'Permintaan ke ESB dihentikan oleh pengaman; hubungi administrator',
};

export function outboxErrorLabel(code: string | null | undefined): string {
  if (!code) return '';
  return OUTBOX_ERROR_LABEL[code] ?? 'Galat dari ESB (lihat administrator)';
}

export function envLabel(env: string): string {
  if (env === 'production') return 'ESB PRODUCTION';
  if (env === 'staging') return 'ESB STAGING';
  return 'ESB (lingkungan tidak dikenal)';
}

// ---------------------------------------------------------------- angka & satuan

/** Dec -> number; null bila kosong/tidak sah. */
export function toNum(v: Dec | undefined): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Kuantitas ID: sampai 4 desimal, tanpa nol di belakang, pemisah ribuan titik ("1.950", "8,5"). Kosong -> "-". */
export function fmtPpQty(v: Dec | undefined): string {
  const n = toNum(v);
  if (n === null) return '-';
  // Pembulatan ke 4 desimal (selaras keluaran prodplan) sebelum diformat.
  const r = Math.round((n + Number.EPSILON) * 10_000) / 10_000;
  return new Intl.NumberFormat('id-ID', { minimumFractionDigits: 0, maximumFractionDigits: 4 }).format(r === 0 ? 0 : r);
}

export function fmtPpQtyUom(v: Dec | undefined, uom: string | undefined): string {
  const q = fmtPpQty(v);
  return q === '-' || !uom ? q : `${q} ${uom}`;
}

/** Umur setelah dibuka: "48 jam (2 hari)", "36 jam (1,5 hari)", "6 jam". null -> "-". */
export function fmtShelfLife(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || !Number.isFinite(hours)) return '-';
  if (hours < 24) return `${hours} jam`;
  const days = Math.round((hours / 24) * 10) / 10;
  return `${hours} jam (${new Intl.NumberFormat('id-ID', { maximumFractionDigits: 1 }).format(days)} hari)`;
}

/** Kebutuhan yang perlu dipenuhi di lokasi produksi: `max(0, kebutuhan efektif − stok)` (§30.5 butir 6). */
export function needToCover(r: Pick<Requirement, 'need_eff_qty' | 'stock_prod_qty'>): number {
  const need = toNum(r.need_eff_qty) ?? 0;
  const stock = Math.max(0, toNum(r.stock_prod_qty) ?? 0);
  return Math.max(0, Math.round((need - stock) * 10_000) / 10_000);
}

/** "2 kemasan × 1.000 ML"; null bila bahan tidak memakai kemasan utuh. */
export function packSummary(r: Pick<Requirement, 'packs' | 'pack_qty' | 'uom_name'>): string | null {
  const pq = toNum(r.pack_qty);
  if (r.packs === null || r.packs === undefined || pq === null) return null;
  return `${fmtPpQty(r.packs)} kemasan × ${fmtPpQtyUom(pq, r.uom_name)}`;
}

/** Bahan memakai kemasan utuh bila `pack_qty` dan `packs` terisi. */
export const usesPack = (r: Pick<Requirement, 'packs' | 'pack_qty'>): boolean => r.packs !== null && toNum(r.pack_qty) !== null;

/** Ada kekurangan yang belum tertutup transfer (merah di UI; tidak memblokir simpan/submit, memblokir posting). */
export const hasOpenShortage = (reqs: readonly Pick<Requirement, 'shortage_qty'>[]): boolean => reqs.some((r) => (toNum(r.shortage_qty) ?? 0) > 0);

// ---------------------------------------------------------------- SoD & aksi

/** Alasan aktor tidak boleh menyetujui (pembuat atau pengaju rencana, §30.6 CHECK + §30.8 403). null = boleh. */
export function sodBlock(plan: Pick<Plan, 'created_by_sub' | 'submitted_by_sub'>, sub: number): string | null {
  if (plan.created_by_sub === sub) return 'Anda pembuat rencana ini; pemisahan tugas melarang pembuat menyetujui.';
  if (plan.submitted_by_sub === sub) return 'Anda pengaju rencana ini; pemisahan tugas melarang pengaju menyetujui.';
  return null;
}

/** Kode galat dokumen `failed` (tanpa nomor ESB) yang boleh dikirim ulang oleh planner (§30.8, revisi G66). */
export const RESEND_ERROR_CODES: readonly string[] = ['esb_rejected', 'not_sent', 'client_error', 'not_found_after_timeout'];

/** Kirim ulang manual: dokumen `failed`, belum punya nomor ESB, dan `last_error_code` termasuk RESEND_ERROR_CODES. */
export const canResend = (o: { status: OutboxItem['status']; last_error_code: string | null; esb_doc_number?: string | null }): boolean =>
  o.status === 'failed' && !o.esb_doc_number && o.last_error_code !== null && RESEND_ERROR_CODES.includes(o.last_error_code);

/** Rencana `approved`: planner ATAU kepala gudang boleh "Segarkan stok & hitung ulang" (mungkin mengembalikan ke draf). */
export const canRefreshStock = (status: PlanStatus, caps: { planner: boolean; warehouse: boolean }): boolean =>
  status === 'approved' && (caps.planner || caps.warehouse);

/** Tombol usulan batch untuk satu usulan (hanya planner, hanya rencana draf). */
export function suggestionButtons(status: SuggestionStatus, planStatus: PlanStatus, planner: boolean): { accept: boolean; reject: boolean; cancelAccept: boolean } {
  const ok = planner && planStatus === 'draft';
  return { accept: ok && status === 'open', reject: ok && status === 'open', cancelAccept: ok && status === 'accepted' };
}

/** Alasan tombol "Ajukan" nonaktif (null = boleh): hasil hitung harus terbaru dan ada baris produksi. */
export function submitBlock(plan: Pick<Plan, 'calc_status' | 'production_lines'>): string | null {
  if (plan.calc_status !== 'fresh') return 'Hitung kebutuhan lebih dulu: hasil hitung harus terbaru sebelum diajukan.';
  if (plan.production_lines.length === 0) return 'Belum ada baris produksi: tambahkan permintaan lalu hitung sebelum diajukan.';
  return null;
}

export const POSTING_STATE: Readonly<Record<PostingState, Labeled>> = {
  none: { label: 'Belum diposting', tone: 'neutral' },
  in_progress: { label: 'Posting berjalan', tone: 'warn' },
  needs_attention: { label: 'Perlu perhatian: ada dokumen ESB bermasalah', tone: 'bad' },
  complete: { label: 'Posting selesai', tone: 'good' },
};

/** Lencana posting hanya bila ada artinya: rencana `posted`, atau state selain `none`. null = tidak ditampilkan. */
export function postingBadge(plan: { status: PlanStatus; posting_state?: PostingState }): Labeled | null {
  const s = plan.posting_state;
  if (s === undefined) return null;
  if (s === 'none' && plan.status !== 'posted') return null;
  return POSTING_STATE[s];
}

/** Keterbatasan dari API ("L-253: stok WIP ...") -> kalimat tanpa awalan kode, huruf awal besar. */
export function cleanLimitation(s: string): string {
  const t = s.replace(/^\s*L-\d+\s*[:.\-–—]?\s*/i, '').trim();
  return t === '' ? s.trim() : t.charAt(0).toUpperCase() + t.slice(1);
}

// ---------------------------------------------------------------- nama cabang/lokasi (nama dulu, ID cadangan)

/** "CK Paris - WH Produksi"; tanpa nama: "cabang 4, lokasi 7". Hanya bagian yang ada. */
export function fmtPlace(branchName: string | null | undefined, branchId: number | null | undefined, locName: string | null | undefined, locId: number | null | undefined): string {
  const bn = branchName?.trim() || '';
  const ln = locName?.trim() || '';
  if (bn || ln) return [bn, ln].filter(Boolean).join(' - ');
  const parts: string[] = [];
  if (branchId !== null && branchId !== undefined && branchId > 0) parts.push(`cabang ${branchId}`);
  if (locId !== null && locId !== undefined && locId > 0) parts.push(`lokasi ${locId}`);
  return parts.length > 0 ? parts.join(', ') : '-';
}

/** Label opsi cabang CK di form buat rencana: "PB - CK Paris - WH Produksi (ID 4 / 7)"; tanpa nama: "PB - cabang 4, lokasi 7". */
export function ckLabel(
  companyCode: string,
  c: { branch_id: number; production_location_id: number; branch_name?: string | null; production_location_name?: string | null },
): string {
  const place = fmtPlace(c.branch_name, c.branch_id, c.production_location_name, c.production_location_id);
  const hint = placeHint(c.branch_name, c.branch_id, c.production_location_name, c.production_location_id);
  return `${companyCode} - ${place}${hint ? ` (${hint})` : ''}`;
}

/** Keterangan kecil "(ID 4 / 7)" hanya bila nama tampil (ID sebagai cadangan); kosong bila nama tidak ada. */
export function placeHint(branchName: string | null | undefined, branchId: number | null | undefined, locName: string | null | undefined, locId: number | null | undefined): string {
  if (!(branchName?.trim() || locName?.trim())) return '';
  const ids = [branchId, locId].filter((x): x is number => typeof x === 'number' && x > 0);
  return ids.length === 0 ? '' : `ID ${ids.join(' / ')}`;
}

// ---------------------------------------------------------------- tab, fase posting, pratinjau

export const PLAN_TABS = ['permintaan', 'kebutuhan', 'usulan', 'transfer', 'posting', 'riwayat'] as const;
export type PlanTab = (typeof PLAN_TABS)[number];
export const PLAN_TAB_LABEL: Readonly<Record<PlanTab, string>> = {
  permintaan: 'Permintaan',
  kebutuhan: 'Kebutuhan',
  usulan: 'Usulan batch',
  transfer: 'Transfer',
  posting: 'Posting',
  riwayat: 'Riwayat',
};

/** Parameter `tab` dari URL -> tab sah (bawaan: permintaan). */
export function parseTab(v: string | undefined): PlanTab {
  return PLAN_TABS.find((t) => t === v) ?? 'permintaan';
}

/** Jumlah usulan transfer yang belum diputuskan (status `proposed`). */
export const undecidedTransfers = (transfers: readonly { status: TransferStatus }[]): number => transfers.filter((t) => t.status === 'proposed').length;

/**
 * Fase posting bawaan: `transfers` bila ada transfer disetujui yang belum punya dokumen outbox fase transfer; selain itu
 * `production` (rencana tanpa transfer langsung ke produksi, §30.8).
 */
export function defaultPhase(plan: Pick<Plan, 'transfers' | 'outbox'>): PostPhase {
  const hasApproved = plan.transfers.some((t) => t.status === 'approved');
  const hasTransferDocs = plan.outbox.some((o) => o.phase === 'transfers');
  return hasApproved && !hasTransferDocs ? 'transfers' : 'production';
}

export function parsePhase(v: string | undefined): PostPhase | null {
  return v === 'transfers' || v === 'production' ? v : null;
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Asal/tujuan dokumen pratinjau (bentuk belum pasti): angka/teks apa adanya, objek -> "cabang X, lokasi Y"; lainnya "-". */
export function describeEndpoint(v: unknown): string {
  if (typeof v === 'number' && Number.isFinite(v)) return `lokasi ${v}`;
  if (typeof v === 'string' && v.length <= 80) return v;
  if (isObj(v)) {
    const named = fmtPlace(
      typeof v.branch_name === 'string' ? v.branch_name : null,
      null,
      typeof v.location_name === 'string' ? v.location_name : null,
      null,
    );
    if (named !== '-') return named;
    const parts: string[] = [];
    if (typeof v.branch_id === 'number') parts.push(`cabang ${v.branch_id}`);
    if (typeof v.location_id === 'number') parts.push(`lokasi ${v.location_id}`);
    if (parts.length > 0) return parts.join(', ');
  }
  return '-';
}

/** Satu baris dokumen pratinjau -> "KODE - Nama: 1.000 ML"; bentuk tak dikenal -> null (tidak ditampilkan). */
export function describeLine(v: unknown): string | null {
  if (!isObj(v)) return null;
  const code = typeof v.product_code === 'string' ? v.product_code : null;
  const name = typeof v.product_name === 'string' ? v.product_name : null;
  const qtyRaw = v.qty ?? v.total_qty ?? v.manufacturing_qty;
  const qty = typeof qtyRaw === 'string' || typeof qtyRaw === 'number' ? fmtPpQty(qtyRaw) : null;
  const uom = typeof v.uom_name === 'string' ? ` ${v.uom_name}` : '';
  const label = code ? (name ? `${code} - ${name}` : code) : name;
  if (!label) return null;
  return qty === null ? label : `${label}: ${qty}${uom}`;
}

export interface ProductionDocView {
  /** "KODE - Nama" produk hasil. */
  product: string;
  qty: string;
  level: number | null;
  /** Bahan, tiap butir "KODE - Nama: 1.000 ML". */
  materials: string[];
}

const nameCode = (code: unknown, name: unknown): string => {
  const c = typeof code === 'string' ? code : '';
  const n = typeof name === 'string' ? name : '';
  return c && n ? `${c} - ${n}` : c || n;
};

function viewOf(o: Record<string, unknown>): ProductionDocView | null {
  const product = nameCode(o.product_code, o.product_name);
  if (!product) return null;
  const qtyRaw = o.qty ?? o.manufacturing_qty ?? o.result_qty;
  const qty = typeof qtyRaw === 'string' || typeof qtyRaw === 'number' ? fmtPpQtyUom(qtyRaw, typeof o.uom_name === 'string' ? o.uom_name : undefined) : '-';
  const mats = Array.isArray(o.materials) ? o.materials : [];
  return {
    product,
    qty,
    level: typeof o.level === 'number' && Number.isFinite(o.level) ? o.level : null,
    materials: mats.map(describeLine).filter((x): x is string => x !== null),
  };
}

/**
 * Dokumen produksi di pratinjau (bentuk belum pasti): produk hasil (kode + nama), jumlah, level, lalu bahannya. Dibaca dari
 * field tingkat dokumen bila ada; bila tidak, dari `lines[]` yang memuat produk + `materials[]`. Kosong = tampilkan ringkasan biasa.
 */
export function productionViews(d: Pick<PreviewDoc, 'product_code' | 'product_name' | 'uom_name' | 'qty' | 'level' | 'materials' | 'lines'>): ProductionDocView[] {
  const top = viewOf({ ...d });
  if (top) {
    // Bahan boleh datang sebagai `lines[]` pada dokumen yang produknya ada di tingkat dokumen.
    if (top.materials.length === 0) top.materials = (d.lines ?? []).map(describeLine).filter((x): x is string => x !== null);
    return [top];
  }
  const out: ProductionDocView[] = [];
  for (const l of d.lines ?? []) {
    if (isObj(l) && Array.isArray(l.materials)) {
      const v = viewOf(l);
      if (v) out.push(v);
    }
  }
  return out;
}

/** Tanggal hari ini di WIB (UTC+7) sebagai YYYY-MM-DD. */
export function todayWib(now: Date): string {
  return new Date(now.getTime() + 7 * 3_600_000).toISOString().slice(0, 10);
}

/** Tanggal ISO (YYYY-MM-DD) sah dan nyata. */
export function isIsoDate(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

/** Waktu ISO -> "24 Nov 2026 14:05 WIB". Tidak sah -> "-". */
export function fmtWibDateTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '-';
  const d = new Date(t + 7 * 3_600_000);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${d.getUTCDate()} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()} ${hh}:${mm} WIB`;
}
