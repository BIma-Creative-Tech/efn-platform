/**
 * Status outbox `needs_login`, aksi `plan-resume-posting`, kode galat baru, dan penanganan `session_expired` di sisi browser
 * (docs/04 §30.17.9, §30.17.13). Murni.
 */
import { describe, expect, it } from 'vitest';
import { getAction, roleAllowed } from '@/lib/prodplan-actions';
import { parseCallBody } from '@/lib/prodplan-call';
import { PP_ERROR_MESSAGES, jobErrorMessage, mapPpError } from '@/lib/prodplan-errors';
import { OUTBOX_STATUS, canResend, hasNeedsLogin, isNeedsLogin, outboxErrorLabel, postingBadge, sodBlock } from '@/lib/prodplan-format';
import { parseOutbox } from '@/lib/prodplan-types';

const ID = '0b0c0a0e-1111-4222-8333-444455556666';

describe('needs_login', () => {
  it('status punya label Indonesia; error label khusus; dikenali dari status ATAU last_error_code', () => {
    expect(OUTBOX_STATUS.needs_login.label).toContain('sesi ESB berakhir');
    expect(outboxErrorLabel('needs_login')).toContain('Lanjutkan posting');
    expect(outboxErrorLabel('needs_login')).not.toBe(outboxErrorLabel('kode_tak_dikenal'));
    expect(isNeedsLogin({ status: 'needs_login', last_error_code: 'needs_login' })).toBe(true);
    // sent/unknown/deleting tanpa token: status tetap, hanya last_error_code
    expect(isNeedsLogin({ status: 'unknown', last_error_code: 'needs_login' })).toBe(true);
    expect(isNeedsLogin({ status: 'sent', last_error_code: null })).toBe(false);
    expect(hasNeedsLogin([{ status: 'confirmed', last_error_code: null }, { status: 'deleting', last_error_code: 'needs_login' }])).toBe(true);
    expect(hasNeedsLogin([])).toBe(false);
  });

  it('parseOutbox mempertahankan needs_login; dokumen needs_login tidak bisa "Kirim ulang" manual', () => {
    const o = parseOutbox({ id: 'o1', seq: 1, phase: 'production', doc_kind: 'simple_manufacturing', status: 'needs_login', last_error_code: 'needs_login' });
    expect(o.status).toBe('needs_login');
    expect(canResend(o)).toBe(false);
  });

  it('plan.posting_state needs_attention tetap tampil sebagai lencana', () => {
    expect(postingBadge({ status: 'approved', posting_state: 'needs_attention' })?.tone).toBe('bad');
  });

  it('job gagal needs_login -> pesan khusus (aman diulang setelah masuk lagi)', () => {
    expect(jobErrorMessage('needs_login')).toContain('Masuk lagi');
    expect(jobErrorMessage('needs_login')).not.toBe(jobErrorMessage('aneh'));
  });
});

describe('job error: not_found / forbidden', () => {
  it('punya pesan khusus (bukan teks umum)', () => {
    for (const c of ['not_found', 'forbidden']) expect(jobErrorMessage(c)).not.toBe(jobErrorMessage('aneh'));
  });
});

describe('aksi plan-resume-posting', () => {
  it('peran R (planner ATAU kepala gudang); path + body persis kontrak {row_version}', () => {
    const spec = getAction('plan-resume-posting');
    expect(spec?.role).toBe('R');
    expect(roleAllowed('R', { planner: true, warehouse: false })).toBe(true);
    expect(roleAllowed('R', { planner: false, warehouse: true })).toBe(true);
    expect(roleAllowed('R', { planner: false, warehouse: false })).toBe(false);
    expect(spec?.build({ id: ID, row_version: 7, extra: 'x' })).toEqual({ ok: true, method: 'POST', path: `/v1/plans/${ID}/resume-posting`, body: { row_version: 7 } });
    expect(spec?.build({ id: 'x', row_version: 7 })).toEqual({ ok: false, field: 'id' });
    expect(spec?.build({ id: ID })).toEqual({ ok: false, field: 'row_version' });
  });
});

describe('kode galat sesi ESB', () => {
  const NEW_CODES = [
    'invalid_credentials',
    'login_locked',
    'no_pp_access',
    'esb_company_not_allowed',
    'session_capacity',
    'session_expired',
    'branch_forbidden',
    'esb_access_missing',
    'resume_requires_original_user',
    'nothing_to_resume',
    'admin_account_write_forbidden',
  ];
  it.each(NEW_CODES)('kode %s punya pesan Indonesia dan dipetakan apa adanya', (code) => {
    expect(PP_ERROR_MESSAGES[code]).toMatch(/\S{3,}/);
    expect(mapPpError(403, { error: { code, message: 'SQL bocor' } })).toEqual({ code, message: PP_ERROR_MESSAGES[code] });
  });

  it('pesan tidak lagi menyuruh "buka ulang dari Odoo" untuk sesi Produksi', () => {
    for (const c of ['actor_invalid', 'unauthorized', 'session_expired']) expect(PP_ERROR_MESSAGES[c]).not.toMatch(/Odoo/);
  });

  it('browser: 401 session_expired dikenali dari kodenya (UI menawarkan masuk lagi tanpa kehilangan halaman)', () => {
    const r = parseCallBody(401, { error: { code: 'session_expired', message: 'Sesi ESB berakhir (maksimal 1 jam). Silakan masuk lagi.' } });
    expect(r).toMatchObject({ ok: false, status: 401, code: 'session_expired' });
  });
});

describe('sodBlock tanpa id aktor', () => {
  it('sub tidak diketahui (respons sesi tanpa actor_id) -> tanpa petunjuk; server tetap menegakkan sod_violation', () => {
    expect(sodBlock({ created_by_sub: 5, submitted_by_sub: 6 }, null)).toBeNull();
    expect(sodBlock({ created_by_sub: 1000001, submitted_by_sub: null }, 1000001)).toContain('pembuat');
  });
});
