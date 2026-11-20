import type { Queryable } from '../../common/db/db';
import { PiiCipher } from '../../common/pii/crypto';
import { maskMsisdn } from '../../common/pii/pii';
import { decryptMemberEmail } from './email.constants';

/**
 * DSAR export kanal G50-B (SEC-G50-05; docs/04 §8.4/§28.9; ADR-044/048). Dipanggil `src/privacy/dsar.service.ts`.
 * Erasure TIDAK di sini: semua tabel ber-FK `ON DELETE CASCADE` ke `platform.member` (baris member dihapus di akhir
 * erasure). Aman pada DB tanpa migrasi `21-platform-g50b.sql` (cek `to_regclass`).
 *
 * Isi (hanya milik subjek sendiri):
 * - `email`: alamat DIDEKRIPSI (subjek berhak atas datanya), status verifikasi & status kirim. Alamat tidak bisa
 *   didekripsi (kunci/AAD) → `address: null, readable: false` (export tidak gagal).
 * - `email_deliveries`: jejak antre/kirim (sumber, ref, template, status, waktu) — tanpa isi pesan.
 * - `wa_unofficial`: `recipients` = pseudonim `to_hash` yang dipetakan ke akun ini + nomor SUBJEK sendiri (dari
 *   `member_ref`, sudah diambil DsarService) dalam bentuk utuh & mask; tidak ada nomor pihak lain (tabel hanya menyimpan
 *   hash). `deliveries` = kategori, sumber, template, status, alasan, waktu — tanpa nomor/isi.
 */
const EXPORT_LIMIT = 1000;

export interface ChannelExport {
  email: {
    address: string | null;
    readable: boolean;
    verified: boolean;
    verified_at: string | null;
    status: string;
    status_reason: string | null;
    updated_at: string | null;
  } | null;
  email_deliveries: { source: string; source_ref: string; template: string; status: string; queued_at: string | null; done_at: string | null }[];
  wa_unofficial: {
    recipients: { phone: string | null; phone_masked: string | null; to_hash: string; last_queued_at: string | null }[];
    deliveries: {
      category: string;
      source: string;
      source_ref: string;
      template: string;
      status: string;
      reason: string | null;
      queued_at: string | null;
      expires_at: string | null;
      result_at: string | null;
    }[];
  };
  truncated: boolean;
}

export function emptyChannelExport(): ChannelExport {
  return { email: null, email_deliveries: [], wa_unofficial: { recipients: [], deliveries: [] }, truncated: false };
}

async function ready(q: Queryable): Promise<boolean> {
  const r = await q.query<{ ok: boolean }>(`SELECT to_regclass('platform.member_email') IS NOT NULL AS ok`);
  return r.rows[0]?.ok === true;
}

function iso(v: Date | string | null | undefined): string | null {
  if (v === null || v === undefined) return null;
  return (v instanceof Date ? v : new Date(v)).toISOString();
}

/**
 * @param subjectPhone nomor HP subjek (member_ref yang sudah didekripsi DsarService) — ditampilkan hanya ke subjek
 *   sendiri pada baris penerima WA miliknya; `null` → hanya hash.
 */
export async function exportChannelMember(q: Queryable, cipher: PiiCipher, memberId: string, subjectPhone: string | null): Promise<ChannelExport> {
  if (!(await ready(q))) return emptyChannelExport();
  const e = await q.query<{
    email_enc: Buffer;
    verified_at: Date | string | null;
    status: string;
    status_reason: string | null;
    updated_at: Date | string | null;
  }>(`SELECT email_enc, verified_at, status, status_reason, updated_at FROM platform.member_email WHERE member_id = $1`, [memberId]);
  let email: ChannelExport['email'] = null;
  const row = e.rows[0];
  if (row) {
    let address: string | null = null;
    try {
      address = decryptMemberEmail(cipher, row.email_enc, memberId).email;
    } catch {
      address = null;
    }
    email = {
      address,
      readable: address !== null,
      verified: !!row.verified_at,
      verified_at: iso(row.verified_at),
      status: row.status,
      status_reason: row.status_reason,
      updated_at: iso(row.updated_at),
    };
  }
  const d = await q.query<{ source: string; source_ref: string; template: string; status: string; queued_at: Date | string; done_at: Date | string | null }>(
    `SELECT source, source_ref, template, status, queued_at, done_at FROM platform.email_outbound
      WHERE member_id = $1 ORDER BY queued_at DESC LIMIT $2`,
    [memberId, EXPORT_LIMIT + 1],
  );
  const r = await q.query<{ to_hash: string; last_queued_at: Date | string }>(
    `SELECT to_hash, last_queued_at FROM platform.wa_unofficial_recipient WHERE member_id = $1 ORDER BY last_queued_at DESC LIMIT $2`,
    [memberId, EXPORT_LIMIT],
  );
  const w = await q.query<{
    category: string;
    source: string;
    source_ref: string;
    template: string;
    status: string;
    reason: string | null;
    queued_at: Date | string;
    expires_at: Date | string;
    result_at: Date | string | null;
  }>(
    `SELECT category, source, source_ref, template, status, reason, queued_at, expires_at, result_at
       FROM platform.wa_unofficial_outbound WHERE member_id = $1 ORDER BY queued_at DESC LIMIT $2`,
    [memberId, EXPORT_LIMIT + 1],
  );
  // Satu akun = satu nomor (`member_ref` tidak berubah, `member_hash` unik): semua baris recipient milik member ini adalah
  // nomor subjek sendiri (beberapa baris hanya mungkin bila kunci hash dirotasi). Nomor pihak lain tidak pernah tersimpan.
  const phoneMasked = subjectPhone ? maskMsisdn(subjectPhone) : null;
  return {
    email,
    email_deliveries: d.rows.slice(0, EXPORT_LIMIT).map((x) => ({
      source: x.source,
      source_ref: x.source_ref,
      template: x.template,
      status: x.status,
      queued_at: iso(x.queued_at),
      done_at: iso(x.done_at),
    })),
    wa_unofficial: {
      recipients: r.rows.map((x) => ({
        phone: subjectPhone,
        phone_masked: phoneMasked,
        to_hash: x.to_hash,
        last_queued_at: iso(x.last_queued_at),
      })),
      deliveries: w.rows.slice(0, EXPORT_LIMIT).map((x) => ({
        category: x.category,
        source: x.source,
        source_ref: x.source_ref,
        template: x.template,
        status: x.status,
        reason: x.reason,
        queued_at: iso(x.queued_at),
        expires_at: iso(x.expires_at),
        result_at: iso(x.result_at),
      })),
    },
    truncated: d.rows.length > EXPORT_LIMIT || w.rows.length > EXPORT_LIMIT,
  };
}
