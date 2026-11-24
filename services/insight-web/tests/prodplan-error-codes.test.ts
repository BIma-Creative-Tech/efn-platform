/**
 * Kecocokan kode galat prodplan -> insight-web (QA G66b, docs/04 §30.17.13 + §30.8). Daftar di bawah = semua kode yang dilempar
 * `services/prodplan/efn_prodplan/{api,service,session,workflow}.py` (`ApiError(status, "kode")`). Kode yang tidak punya pesan
 * sendiri jatuh ke pesan generik per status HTTP (mis. 422 -> "Permintaan tidak valid") yang menyesatkan pengguna.
 * Menambah kode di prodplan tanpa pesan di sini harus membuat test ini gagal: perbarui daftar + `PP_ERROR_MESSAGES`.
 */
import { describe, expect, it } from 'vitest';
import { PP_ERROR_MESSAGES, mapPpError, publicStatus } from '@/lib/prodplan-errors';
import { loginErrorFor } from '@/lib/prodplan-session';

// [kode, status HTTP dari prodplan]
const PRODPLAN_CODES: Array<[string, number]> = [
  ['invalid_request', 400],
  ['actor_invalid', 401],
  ['session_expired', 401],
  ['invalid_credentials', 401],
  ['forbidden', 403],
  ['branch_forbidden', 403],
  ['esb_access_missing', 403],
  ['resume_requires_original_user', 403],
  ['no_pp_access', 403],
  ['esb_company_not_allowed', 403],
  ['sod_violation', 403],
  ['not_found', 404],
  ['conflict_version', 409],
  ['invalid_state', 409],
  ['job_running', 409],
  ['stale_calculation', 409],
  ['stock_too_old', 409],
  ['empty_plan', 409],
  ['transfers_undecided', 409],
  ['transfers_not_authorized', 409],
  ['write_disabled', 409],
  ['digest_mismatch', 409],
  ['shortage_open', 409],
  ['too_many_docs', 409],
  ['date_out_of_window', 409],
  ['nothing_to_resume', 409],
  ['reconcile_incomplete', 409],
  ['bom_not_assembly', 422],
  ['invalid_branch', 422],
  ['invalid_quantity', 422],
  ['bad_input', 422],
  ['rate_limited', 429],
  ['login_locked', 429],
  ['esb_unavailable', 502],
  ['session_capacity', 503],
  ['disabled', 503],
];

describe('kode galat prodplan punya pesan Indonesia sendiri', () => {
  it.each(PRODPLAN_CODES)('%s (%i): kode dipertahankan, pesan dari tabel, status diteruskan', (code, status) => {
    expect(PP_ERROR_MESSAGES[code], `pesan untuk ${code}`).toBeTruthy();
    const mapped = mapPpError(status, { error: { code, message: 'SQL bocor: SELECT * FROM prodplan.plan' } });
    expect(mapped.code).toBe(code);
    expect(mapped.message).toBe(PP_ERROR_MESSAGES[code]);
    expect(mapped.message).not.toMatch(/SELECT|prodplan\./);
    expect(publicStatus(status)).toBe(status);
  });

  it('kode di luar tabel tidak pernah meneruskan teks prodplan; jatuh ke kode menurut status', () => {
    const r = mapPpError(422, { error: { code: 'kode_baru_belum_dikenal', message: 'detail internal' } });
    expect(r.code).toBe('invalid_request');
    expect(r.message).not.toContain('detail internal');
    expect(mapPpError(500, { error: { code: 'internal', message: 'stack' } }).code).toBe('bad_response');
  });

  it('galat login prodplan -> ?e= : tidak membedakan user tidak ada vs password salah; 502/503 = unavailable', () => {
    expect(loginErrorFor('invalid_credentials', 401)).toBe('invalid');
    expect(loginErrorFor('login_locked', 429)).toBe('locked');
    expect(loginErrorFor('no_pp_access', 403)).toBe('no_access');
    expect(loginErrorFor('esb_company_not_allowed', 403)).toBe('no_access');
    expect(loginErrorFor('esb_unavailable', 502)).toBe('unavailable');
    expect(loginErrorFor('session_capacity', 503)).toBe('unavailable');
    expect(loginErrorFor('disabled', 503)).toBe('unavailable');
    expect(loginErrorFor('actor_invalid', 401)).toBe('unavailable');
    expect(loginErrorFor(undefined, 504)).toBe('unavailable');
  });
});
