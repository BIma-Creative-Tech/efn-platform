/**
 * Pemanggil sisi BROWSER ke route handler `/api/prodplan/*` (same-origin) + logika polling job. Tanpa secret dan tanpa token:
 * identitas aktor dibuat server dari cookie sesi HttpOnly. Fungsi murni (`parseCallBody`, `jobStep`) dipisah agar bisa diuji.
 */
import { PP_ERROR_MESSAGES, jobErrorMessage } from './prodplan-errors';
import { parseBoms, parseJobInfo, type BomItem } from './prodplan-types';

export const POLL_INTERVAL_MS = 2000;
/** Batas polling job (§30.12): 5 menit. */
export const POLL_MAX_MS = 5 * 60_000;

export type CallResult =
  | { ok: true; status: number; data: unknown }
  | { ok: false; status: number; code: string; message: string; fields: string[] };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Badan respons route handler -> hasil. Pesan galat selalu dari tabel Indonesia (bukan teks mentah dari jaringan). */
export function parseCallBody(status: number, body: unknown): CallResult {
  if (status >= 200 && status < 300 && isObj(body) && body.ok === true) return { ok: true, status, data: body.data };
  let code = 'bad_response';
  let message = PP_ERROR_MESSAGES.bad_response;
  let fields: string[] = [];
  if (isObj(body) && isObj(body.error)) {
    const e = body.error;
    if (typeof e.code === 'string' && Object.prototype.hasOwnProperty.call(PP_ERROR_MESSAGES, e.code)) code = e.code;
    // `message` dari server sudah berasal dari tabel yang sama (messageFor); dipakai agar jalur BOM/nomor dokumen ikut tampil.
    if (typeof e.message === 'string' && e.message !== '' && e.message.length <= 400) message = e.message;
    else message = PP_ERROR_MESSAGES[code];
    if (isObj(e.details) && Array.isArray(e.details.fields)) fields = e.details.fields.filter((x): x is string => typeof x === 'string').slice(0, 10);
  }
  return { ok: false, status, code, message, fields };
}

async function readJson(res: Response): Promise<unknown> {
  try {
    return (await res.json()) as unknown;
  } catch {
    return null;
  }
}

const NETWORK_ERROR: CallResult = { ok: false, status: 0, code: 'unreachable', message: PP_ERROR_MESSAGES.unreachable, fields: [] };

/** `POST /api/prodplan/<aksi>` dengan badan JSON. */
export async function callAction(action: string, payload: Record<string, unknown>): Promise<CallResult> {
  try {
    const res = await fetch(`/api/prodplan/${encodeURIComponent(action)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      credentials: 'same-origin',
      cache: 'no-store',
    });
    return parseCallBody(res.status, await readJson(res));
  } catch {
    return NETWORK_ERROR;
  }
}

export interface JobSnapshot {
  status: string;
  errorCode: string | null;
  /** Dari job.result (opsional): status rencana setelah hitung, apakah hasil berubah, jalur siklus BOM. */
  planStatus: 'approved' | 'draft' | null;
  changed: boolean | null;
  path: string[];
}

/** `GET /api/prodplan/jobs/{id}`. */
export async function fetchJob(id: string): Promise<{ ok: true; job: JobSnapshot } | { ok: false; status: number; message: string }> {
  try {
    const res = await fetch(`/api/prodplan/jobs/${encodeURIComponent(id)}`, { credentials: 'same-origin', cache: 'no-store' });
    const parsed = parseCallBody(res.status, await readJson(res));
    if (!parsed.ok) return { ok: false, status: parsed.status, message: parsed.message };
    const d = parsed.data;
    if (!isObj(d) || typeof d.status !== 'string') return { ok: false, status: 502, message: PP_ERROR_MESSAGES.bad_response };
    const info = parseJobInfo(d);
    return {
      ok: true,
      job: { status: d.status, errorCode: typeof d.error_code === 'string' ? d.error_code : null, planStatus: info.plan_status, changed: info.changed, path: info.path },
    };
  } catch {
    return { ok: false, status: 0, message: PP_ERROR_MESSAGES.unreachable };
  }
}

/** `GET /api/prodplan/boms?brand=&q=` (pencarian BOM untuk editor permintaan). */
export async function searchBoms(brand: string, q: string, signal?: AbortSignal): Promise<{ ok: true; items: BomItem[] } | { ok: false; message: string }> {
  try {
    const qs = new URLSearchParams({ brand, q });
    const res = await fetch(`/api/prodplan/boms?${qs.toString()}`, { credentials: 'same-origin', cache: 'no-store', signal });
    const parsed = parseCallBody(res.status, await readJson(res));
    if (!parsed.ok) return { ok: false, message: parsed.message };
    return { ok: true, items: parseBoms(parsed.data) };
  } catch {
    return { ok: false, message: PP_ERROR_MESSAGES.unreachable };
  }
}

export type JobStep ={ kind: 'wait' } | { kind: 'done' } | { kind: 'failed'; message: string } | { kind: 'timeout'; message: string };

/** Keputusan polling dari status job dan waktu berjalan. */
export function jobStep(status: string, errorCode: string | null, elapsedMs: number, path: readonly string[] = []): JobStep {
  if (status === 'done') return { kind: 'done' };
  if (status === 'failed') return { kind: 'failed', message: jobErrorMessage(errorCode, path) };
  if (elapsedMs >= POLL_MAX_MS) {
    return { kind: 'timeout', message: 'Proses belum selesai setelah 5 menit. Muat ulang halaman nanti untuk melihat hasilnya.' };
  }
  return { kind: 'wait' };
}

/**
 * Teks hasil job selesai. `refresh` = "Segarkan stok & hitung ulang" pada rencana disetujui: bila hasil berubah / rencana
 * kembali ke draf, pesan menegaskan bahwa rencana harus diajukan dan disetujui ulang.
 */
export function jobDoneText(snap: Pick<JobSnapshot, 'planStatus' | 'changed'>, refresh: boolean): { text: string; backToDraft: boolean } {
  if (!refresh) return { text: 'Proses selesai.', backToDraft: false };
  const back = snap.planStatus === 'draft' || snap.changed === true;
  return back
    ? { text: 'Stok disegarkan dan hasil hitung BERUBAH: rencana kembali ke Draf. Periksa hasilnya, ajukan, lalu minta persetujuan ulang.', backToDraft: true }
    : { text: 'Stok disegarkan; hasil hitung tidak berubah. Rencana tetap Disetujui.', backToDraft: false };
}

/** `job_id` dari data respons 202 (`{job_id}`); null bila tidak ada. */
export function jobIdOf(data: unknown): string | null {
  return isObj(data) && typeof data.job_id === 'string' && /^[0-9a-f-]{36}$/.test(data.job_id) ? data.job_id : null;
}
