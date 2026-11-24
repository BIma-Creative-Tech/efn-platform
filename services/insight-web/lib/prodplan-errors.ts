/**
 * Pemetaan galat prodplan -> pesan Indonesia untuk pengguna (docs/04 §30.8). Murni. Pesan HANYA dari tabel ini: teks `message`
 * dari prodplan tidak diteruskan (menghindari kebocoran detail internal bila implementasinya berubah). `details` disaring
 * ketat: hanya bidang yang diizinkan kontrak (nama field, row_version, jalur kode produk, nomor dokumen).
 */
export const PP_ERROR_MESSAGES: Readonly<Record<string, string>> = {
  invalid_request: 'Permintaan tidak valid. Periksa isian lalu coba lagi.',
  actor_invalid: 'Sesi Anda tidak dapat diverifikasi oleh layanan produksi. Buka ulang Insight dari Odoo.',
  forbidden: 'Anda tidak berwenang untuk brand atau tindakan ini.',
  not_found: 'Data tidak ditemukan.',
  conflict_version: 'Rencana sudah diubah oleh orang lain. Muat ulang halaman, periksa perubahannya, lalu ulangi.',
  invalid_state: 'Tindakan ini tidak dapat dilakukan pada status rencana saat ini. Muat ulang halaman.',
  bom_cycle: 'Resep (BOM) memuat siklus: produk menjadi bahan bagi dirinya sendiri.',
  bom_too_deep: 'Resep (BOM) terlalu bertingkat (maksimum 10 tingkat).',
  bom_not_assembly: 'Produk ini tidak punya resep (BOM) bertipe Assembly, sehingga tidak bisa diproduksi dari halaman ini.',
  invalid_branch: 'Cabang atau lokasi produksi tidak termasuk daftar Central Kitchen yang diizinkan.',
  job_running: 'Masih ada proses impor atau hitung yang berjalan untuk rencana ini. Tunggu hingga selesai.',
  stale_calculation: 'Hasil hitung sudah kedaluwarsa karena permintaan berubah. Hitung ulang sebelum mengajukan.',
  stock_too_old:
    'Data stok terlalu lama. Segarkan stok dari ESB: pakai tombol "Hitung ulang" (rencana draf) atau "Segarkan stok & hitung ulang" (rencana disetujui), lalu ulangi.',
  empty_plan: 'Rencana belum punya baris produksi. Tambahkan permintaan lalu hitung sebelum mengajukan.',
  sod_violation: 'Pemisahan tugas: pembuat atau pengaju rencana tidak boleh menyetujuinya.',
  transfers_undecided: 'Masih ada usulan transfer yang belum disetujui atau ditolak.',
  transfers_not_authorized: 'Transfer di ESB belum di-authorize oleh petugas. Selesaikan di ESB, lalu coba lagi.',
  write_disabled: 'Penulisan ke ESB sedang dimatikan. Rencana tetap bisa dibuat dan disetujui, tetapi belum bisa diposting.',
  digest_mismatch: 'Isi rencana atau stok berubah sejak pratinjau. Muat ulang pratinjau lalu konfirmasi lagi.',
  shortage_open: 'Masih ada kekurangan bahan yang belum tertutup transfer. Centang persetujuan kekurangan bila tetap ingin posting.',
  too_many_docs: 'Jumlah dokumen ESB melebihi batas per rencana.',
  date_out_of_window: 'Tanggal produksi di luar jendela yang diizinkan untuk posting.',
  rate_limited: 'Terlalu banyak permintaan. Tunggu sebentar lalu coba lagi.',
  esb_unavailable: 'ESB tidak dapat dihubungi. Coba lagi beberapa saat lagi.',
  disabled: 'Modul perencanaan produksi belum aktif.',
  // Kode lokal insight-web (bukan dari prodplan).
  timeout: 'Layanan perencanaan produksi tidak merespons tepat waktu. Muat ulang halaman untuk memeriksa hasilnya sebelum mengulang.',
  unreachable: 'Layanan perencanaan produksi tidak dapat dihubungi.',
  bad_response: 'Respons layanan perencanaan produksi tidak dikenali.',
  bad_input: 'Isian tidak valid.',
  unauthorized: 'Sesi berakhir. Buka ulang Insight dari Odoo.',
  forbidden_origin: 'Permintaan ditolak karena asalnya tidak sama dengan halaman ini.',
  unknown_action: 'Tindakan tidak dikenal.',
};

/** Pesan job gagal (`job.error_code`, §30.8). */
export const PP_JOB_ERROR_MESSAGES: Readonly<Record<string, string>> = {
  esb_unavailable: 'ESB tidak dapat dihubungi. Coba lagi beberapa saat lagi.',
  esb_auth_failed: 'Login ke ESB gagal. Hubungi administrator.',
  call_budget_exceeded: 'Proses melebihi batas panggilan ke ESB. Persempit rentang tanggal atau hubungi administrator.',
  bom_cycle: 'Resep (BOM) memuat siklus.',
  bom_too_deep: 'Resep (BOM) terlalu bertingkat.',
  parse_error: 'Data dari ESB tidak dapat dibaca.',
  conflict_version: 'Rencana diubah orang lain saat proses berjalan. Muat ulang halaman lalu ulangi.',
  invalid_state: 'Status rencana berubah saat proses berjalan. Muat ulang halaman.',
  bom_missing: 'Ada resep (BOM) yang tidak ditemukan lagi di ESB. Periksa baris permintaan, lalu hitung ulang.',
  bom_not_assembly: 'Ada produk yang resepnya bukan tipe Assembly sehingga tidak bisa dihitung.',
  interrupted: 'Proses terhenti (layanan dimulai ulang). Jalankan lagi.',
  internal: 'Terjadi galat di layanan. Coba lagi atau hubungi administrator.',
};

/** Pesan job gagal; `bom_cycle` menampilkan jalur kode produk (job.result.path) bila ada. */
export function jobErrorMessage(code: string | null | undefined, path: readonly string[] = []): string {
  const base = (code && Object.prototype.hasOwnProperty.call(PP_JOB_ERROR_MESSAGES, code) && PP_JOB_ERROR_MESSAGES[code]) || 'Proses gagal. Coba lagi atau hubungi administrator.';
  return code === 'bom_cycle' && path.length > 0 ? `${base} Jalur: ${path.join(' → ')}.` : base;
}

const SAFE_TOKEN = /^[A-Za-z0-9._ -]{1,40}$/;
const SAFE_FIELD = /^[a-z_][a-z0-9_.[\]]{0,63}$/;

export interface PpErrorBody {
  code: string;
  message: string;
  details?: { fields?: string[]; row_version?: number; path?: string[]; docs?: string[] };
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Saring `details` ke bidang yang diizinkan; apa pun di luar itu dibuang. */
export function sanitizeDetails(raw: unknown): PpErrorBody['details'] | undefined {
  if (!isObj(raw)) return undefined;
  const out: NonNullable<PpErrorBody['details']> = {};
  const fieldsRaw: unknown[] = Array.isArray(raw.fields) ? raw.fields : typeof raw.field === 'string' ? [raw.field] : [];
  const fields = fieldsRaw.filter((x): x is string => typeof x === 'string' && SAFE_FIELD.test(x)).slice(0, 10);
  if (fields.length > 0) out.fields = fields;
  if (typeof raw.row_version === 'number' && Number.isSafeInteger(raw.row_version)) out.row_version = raw.row_version;
  const pathRaw: unknown[] = Array.isArray(raw.path) ? raw.path : Array.isArray(raw.cycle) ? raw.cycle : [];
  const path = pathRaw.filter((x): x is string => typeof x === 'string' && SAFE_TOKEN.test(x)).slice(0, 12);
  if (path.length > 0) out.path = path;
  // Service prodplan mengirim `details.doc_numbers` (service.py `transfers_not_authorized`); `docs`/`doc_nums` = nama lama.
  const docsRaw: unknown[] = Array.isArray(raw.doc_numbers)
    ? raw.doc_numbers
    : Array.isArray(raw.docs)
      ? raw.docs
      : Array.isArray(raw.doc_nums)
        ? raw.doc_nums
        : [];
  const docs = docsRaw.filter((x): x is string => typeof x === 'string' && SAFE_TOKEN.test(x)).slice(0, 20);
  if (docs.length > 0) out.docs = docs;
  return Object.keys(out).length > 0 ? out : undefined;
}

/** Pesan lengkap untuk satu kode (jalur BOM / nomor dokumen ditambahkan bila ada). */
export function messageFor(code: string, details?: PpErrorBody['details']): string {
  const base = PP_ERROR_MESSAGES[code] ?? PP_ERROR_MESSAGES.bad_response;
  if (code === 'bom_cycle' && details?.path && details.path.length > 0) return `${base} Jalur: ${details.path.join(' → ')}.`;
  if (code === 'transfers_not_authorized' && details?.docs && details.docs.length > 0) return `${base} Dokumen: ${details.docs.join(', ')}.`;
  return base;
}

/** Pesan umum menurut status HTTP bila kode tidak dikenal. */
function codeForStatus(status: number): string {
  if (status === 400 || status === 422) return 'invalid_request';
  if (status === 401) return 'actor_invalid';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 409) return 'invalid_state';
  if (status === 429) return 'rate_limited';
  if (status === 502) return 'esb_unavailable';
  if (status === 503) return 'disabled';
  return 'bad_response';
}

/**
 * Peta (status HTTP, badan galat prodplan `{error:{code,message,details?}}`) -> galat aman untuk browser.
 * Kode yang tidak ada di tabel dipetakan dari status HTTP.
 */
export function mapPpError(status: number, body: unknown): PpErrorBody {
  let code = '';
  let details: unknown;
  if (isObj(body) && isObj(body.error)) {
    if (typeof body.error.code === 'string') code = body.error.code;
    details = body.error.details;
  }
  if (!Object.prototype.hasOwnProperty.call(PP_ERROR_MESSAGES, code)) code = codeForStatus(status);
  const d = sanitizeDetails(details);
  return d ? { code, message: messageFor(code, d), details: d } : { code, message: messageFor(code) };
}

/** Status HTTP yang boleh diteruskan ke browser; selain itu 502. */
export function publicStatus(status: number): number {
  return [400, 401, 403, 404, 409, 422, 429, 502, 503].includes(status) ? status : 502;
}
