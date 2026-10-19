import { Logger } from '@nestjs/common';
import { GUARDS_METADATA } from '@nestjs/common/constants';
import { MetricsService } from '../common/metrics/metrics.service';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AccessTokenService } from '../common/security/access-token.service';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { computeWebhookSignature } from '../common/security/webhook-signature';
import { FakeClock, FakeKvStore, catchApiError, httpContext, randomSecret, testConfig } from '../common/testing/fakes';
import { MemberService } from '../common/member/member.service';
import { FnbEventHmacGuard } from './fnb-event.guard';
import { FnbJobsService } from './fnb-jobs.service';
import { FnbMetrics } from './fnb-metrics';
import { FnbPosService, parseFnbEvent, parseOpenSession } from './fnb-pos.service';
import { issueTableToken, verifyTableToken } from './fnb-token';
import { FnbPosController } from './fnb.controller';
import { OPEN_KEY_RE, parseSubmitBody, sanitizeNote } from './fnb.model';
import { AltchaVerifier, FnbService } from './fnb.service';
import { MockOdooQr, ODOO_QR_PATHS, OdooQrError, parseQrMenu, parseQrStatus, parseQrSubmit, qrBusinessCode, qrBusinessError } from './odoo-qr';
import { FakeFnbEarn, MemoryFnbRepo } from './testing/fnb-fakes';

/** G22 docs/04 §14.A — QR meja F&B (L-70..L-76). Redis/Odoo/DB di-fake. */

const MEMBER_ID = '5b1d2c3e-4f50-4a61-8b72-9c8d7e6f5a4b';
const MEMBER_HASH = 'a'.repeat(64);
const OTHER_ID = '6c2e3d4f-5061-4b72-9c83-0d9e8f7a6b5c';
const ORDER_UUID = 'e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b';
const KEY = 'k_0123456789abcdef_A'; // gitleaks:allow (fixture idempotency key, bukan secret)

function setup(env: Record<string, string> = {}) {
  const clock = new FakeClock();
  const config = testConfig({
    FNB_QR_ENABLED: 'true',
    FNB_QR_BASE_URL_PB: 'https://pb.efn.example',
    FNB_QR_BASE_URL_BCF: 'https://bacha.efn.example',
    FNB_TABLE_TOKEN_KEY: randomSecret(),
    ODOO_FNB_EVENT_HMAC: randomSecret(),
    FNB_QR_POINTS_ENABLED: 'true',
    ...env,
  });
  const kv = new FakeKvStore(clock.now);
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  const metrics = new FnbMetrics(new MetricsService());
  const repo = new MemoryFnbRepo();
  repo.now = clock.now;
  const odoo = new MockOdooQr();
  odoo.now = clock.now;
  const altcha: AltchaVerifier & { ok: boolean } = { ok: true, verify: async () => altcha.ok };
  const tokens = new AccessTokenService(config);
  tokens.now = clock.now;
  const members = { findActive: async (id: string) => (id === MEMBER_ID || id === OTHER_ID ? { id, memberHash: id === MEMBER_ID ? MEMBER_HASH : 'b'.repeat(64) } : null) } as unknown as MemberService;
  const fnb = new FnbService(config, repo, odoo, limiter, metrics, altcha, tokens, members);
  fnb.now = clock.now;
  const earn = new FakeFnbEarn();
  const pos = new FnbPosService(config, repo, limiter, metrics, earn);
  pos.now = clock.now;
  repo.seedConfig(7, 'PB', odoo.products, 'waiter');
  const open = async (over: Record<string, unknown> = {}) =>
    pos.openSession(
      // bentuk Odoo E-27: fnb_open:<config>:<table>:<n>, tanpa order_uuid
      parseOpenSession({ idempotency_key: 'fnb_open:7:12:1', company_code: 'PB', config_id: 7, table_id: 12, table_label: 'T12 / Teras', reissue: false, ...over }),
    );
  const tokenOf = (qrUrl: string): string => qrUrl.split('#t=')[1];
  const ctx = (token: string, authorization?: string) => ({ tableToken: token, authorization, ip: '203.0.113.7' });
  const bearer = (id = MEMBER_ID) => `Bearer ${tokens.issue(id).accessToken}`;
  return { clock, config, kv, limiter, metrics, repo, odoo, altcha, fnb, pos, earn, open, tokenOf, ctx, bearer };
}

async function code(p: Promise<unknown> | (() => unknown)): Promise<string> {
  const err = await catchApiError(typeof p === 'function' ? p : () => p);
  return `${err.getStatus()} ${(err.body as { error: { code: string } }).error.code}`;
}
function quiet(): jest.SpyInstance {
  return jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
}

describe('token sesi meja fts1 (A.2)', () => {
  const key = Buffer.alloc(32, 7);
  const prev = Buffer.alloc(32, 9);
  const id = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10';

  it('format persis + verifikasi; kunci lama (rotasi) diterima; kedaluwarsa / tanda tangan salah / diubah → null', () => {
    const t = issueTableToken(key, id, 2_000_000_000);
    expect(t).toMatch(/^fts1\.[A-Za-z0-9_-]{22}\.2000000000\.[A-Za-z0-9_-]{43}$/);
    expect(verifyTableToken(t, [key], 1_999_999_999)).toEqual({ sessionId: id, exp: 2_000_000_000 });
    expect(verifyTableToken(issueTableToken(prev, id, 2_000_000_000), [key, prev], 1_999_999_999)?.sessionId).toBe(id);
    expect(verifyTableToken(t, [key], 2_000_000_000)).toBeNull(); // exp ≤ now
    expect(verifyTableToken(t, [prev], 1)).toBeNull();
    expect(verifyTableToken(t.replace('.2000000000.', '.2000000001.'), [key], 1)).toBeNull(); // exp diubah
    expect(verifyTableToken(`${t}x`, [key], 1)).toBeNull();
    expect(verifyTableToken(undefined, [key], 1)).toBeNull();
    expect(verifyTableToken('fts1.'.padEnd(500, 'a'), [key], 1)).toBeNull();
  });
});

describe('catatan & body kiriman (A.3, D-22-6)', () => {
  it('catatan: trim, kontrol/baris baru dibuang, ≤ 140; deret ≥ 8 digit / @ → 400 note_invalid', async () => {
    expect(sanitizeNote('  tanpa\nbawang  ')).toBe('tanpa bawang');
    expect(sanitizeNote('')).toBeNull();
    expect(sanitizeNote('pedas level 3')).toBe('pedas level 3');
    for (const bad of ['telp 0812 3456 789', '081234567890', 'a@b.c', 'x'.repeat(141)]) {
      expect(await code(() => sanitizeNote(bad))).toBe('400 note_invalid');
    }
  });

  it('body ketat: kunci lain, qty di luar batas, PTAV duplikat/> 10 → 400; PTAV diurutkan', async () => {
    const limits = { maxLines: 30, maxLineQty: 20 };
    expect(parseSubmitBody({ lines: [{ product_id: 22, qty: 2, attribute_value_ids: [201, 101] }] }, limits).lines[0].attribute_value_ids).toEqual([101, 201]);
    for (const b of [
      { lines: [{ product_id: 22, qty: 1, price: 1 }] },
      { lines: [{ product_id: 22, qty: 21 }] },
      { lines: [{ product_id: 22, qty: 1, attribute_value_ids: [1, 1] }] },
      { lines: [], altcha: 'x' },
      { lines: [{ product_id: 22, qty: 1 }], table_id: 3 },
    ]) {
      expect(await code(() => parseSubmitBody(b, limits))).toBe('400 invalid_request');
    }
  });
});

describe('POST /v1/pos/fnb/table-sessions (A.4)', () => {
  it('201 qr_url token di FRAGMENT; replay key sama → respons sama; body beda → 409; meja punya sesi → 409 / reissue menutup lama', async () => {
    const ctx = setup();
    const a = await ctx.open();
    expect(a.qr_url).toMatch(/^https:\/\/pb\.efn\.example\/meja#t=fts1\./);
    expect(a.qr_url).not.toContain('?');
    expect(await ctx.open()).toEqual(a);
    expect(await code(ctx.open({ table_label: 'T13' }))).toBe('409 idempotency_conflict');
    expect(await code(ctx.open({ idempotency_key: 'fnb_open:7:12:2' }))).toBe('409 table_session_exists');
    const b = await ctx.open({ idempotency_key: 'fnb_open:7:12:2', reissue: true });
    expect(b.table_session_id).not.toBe(a.table_session_id);
    expect(ctx.repo.state.sessions.get(a.table_session_id)).toMatchObject({ status: 'closed', close_reason: 'reissued' });
    // sesi lama dicabut → token lama 409
    expect(await code(ctx.fnb.session(ctx.ctx(ctx.tokenOf(a.qr_url))))).toBe('409 table_session_closed');
  });

  it('QR mati / PT bukan QR / URL tenant kosong → 422 qr_disabled; label mirip HP / key tak sesuai → 400', async () => {
    expect(await code(setup({ FNB_QR_ENABLED: 'false' }).open())).toBe('422 qr_disabled');
    expect(await code(setup({ FNB_QR_COMPANIES: 'BCF' }).open())).toBe('422 qr_disabled');
    expect(await code(setup({ FNB_QR_BASE_URL_PB: '' }).open())).toBe('422 qr_disabled');
    const base = { idempotency_key: 'fnb_open:7:1:1', company_code: 'PB', config_id: 7, table_id: 1 };
    expect(await code(() => parseOpenSession({ ...base, table_label: '0812 3456 7890' }))).toBe('400 invalid_request');
    expect(await code(() => parseOpenSession({ ...base, table_label: 'T1', table_id: 2 }))).toBe('400 invalid_request'); // key ≠ body
    expect(await code(() => parseOpenSession({ ...base, table_label: 'T1', company_code: 'GL' }))).toBe('400 invalid_request');
    // bentuk kontrak awal (key memuat order_uuid) tetap diterima
    expect(parseOpenSession({ ...base, idempotency_key: `fnb_open:${ORDER_UUID}:1`, order_uuid: ORDER_UUID, table_label: 'T1' }).order_uuid).toBe(ORDER_UUID);
  });

  it('controller dilindungi ServiceKeyGuard; events + FnbEventHmacGuard', () => {
    expect(Reflect.getMetadata(GUARDS_METADATA, FnbPosController)).toContain(ServiceKeyGuard);
    const m = Object.getOwnPropertyDescriptor(FnbPosController.prototype, 'events')?.value as object;
    expect(Reflect.getMetadata(GUARDS_METADATA, m)).toContain(FnbEventHmacGuard);
  });
});

describe('API publik /v1/fnb (A.3)', () => {
  it('token salah / tanpa header / kedaluwarsa → 401 table_session_invalid (generik); kill switch → 503', async () => {
    const ctx = setup();
    const s = await ctx.open();
    const t = ctx.tokenOf(s.qr_url);
    expect(await code(ctx.fnb.session(ctx.ctx(`${t.slice(0, -2)}AA`)))).toBe('401 table_session_invalid');
    expect(await code(ctx.fnb.session({ tableToken: undefined, authorization: undefined, ip: null }))).toBe('401 table_session_invalid');
    ctx.clock.advanceSeconds(14_401);
    expect(await code(ctx.fnb.session(ctx.ctx(t)))).toBe('401 table_session_invalid');
    const off = setup({ FNB_QR_ENABLED: 'false' });
    expect(await code(off.fnb.menu(off.ctx(t)))).toBe('503 ordering_unavailable');
  });

  it('GET session: bentuk kontrak; Bearer tidak valid → 401 unauthorized; claimed_by_me hanya dengan Bearer', async () => {
    const ctx = setup();
    const t = ctx.tokenOf((await ctx.open()).qr_url);
    const v = await ctx.fnb.session(ctx.ctx(t));
    expect(v).toEqual({
      outlet_name: 'Outlet Demo',
      table_label: 'T12 / Teras',
      company_code: 'PB',
      brand_code: null,
      expires_at: expect.any(String),
      confirm_mode: 'waiter',
      limits: { max_lines: 30, max_line_qty: 20, submissions_left: 10 }, // L-76 default 10
      points: { claimable: true, claimed: false },
    });
    expect(await code(ctx.fnb.session(ctx.ctx(t, 'Bearer x.y.z')))).toBe('401 unauthorized');
    expect((await ctx.fnb.session(ctx.ctx(t, ctx.bearer()))).points).toEqual({ claimable: true, claimed: false, claimed_by_me: false });
  });

  it('GET menu: harga exclude + label pajak + atribut; produk tanpa salinan katalog tetap dengan nama overlay', async () => {
    const ctx = setup();
    ctx.repo.state.catalog.set('PB:21', { name: 'Croissant Butter', category_id: 5, image_sha: 'a'.repeat(40) });
    ctx.repo.state.categories.set('PB', [{ category_id: 5, name: 'Roti', sequence: 1 }]);
    const t = ctx.tokenOf((await ctx.open()).qr_url);
    const m = await ctx.fnb.menu(ctx.ctx(t));
    expect(m.products.find((p) => p.product_id === 21)).toMatchObject({ name: 'Croissant Butter', image_sha: 'a'.repeat(40), price_excl: 25_000, price_note: 'belum termasuk pajak & layanan' });
    expect(m.products.find((p) => p.product_id === 22)?.attributes[0]).toMatchObject({ display_type: 'radio', required: true });
    expect(m.categories).toEqual([{ category_id: 5, name: 'Roti', sequence: 1, image_sha: null }]);
  });

  it('POST orders: 202 pending_confirmation (L-71 waiter), satu kiriman = order baru (L-70); catatan tidak disimpan; replay sama; body beda → 409', async () => {
    const ctx = setup();
    const t = ctx.tokenOf((await ctx.open()).qr_url);
    const body = { lines: [{ product_id: 22, qty: 2, attribute_value_ids: [201, 101], note: 'hangat' }] };
    const r = await ctx.fnb.submit(ctx.ctx(t), KEY, body);
    expect(r).toMatchObject({ status: 'pending_confirmation', indicative_subtotal_excl: 88_000 });
    expect(r.lines[0]).toEqual({ product_id: 22, name: 'Latte (demo)', qty: 2, attribute_value_ids: [101, 201], attributes_text: 'Gula: Normal, Ekstra: Shot espresso' });
    const sub = ctx.repo.state.subs.get(r.submission_id);
    expect(sub?.order_uuid).toEqual(expect.any(String));
    expect(JSON.stringify(sub)).not.toContain('hangat');
    expect(sub?.lines[0]).toEqual({ product_id: 22, qty: 2, attribute_value_ids: [101, 201], has_note: true });
    expect(ctx.odoo.calls.filter((c) => c.method === 'submit')).toHaveLength(1);
    expect(await ctx.fnb.submit(ctx.ctx(t), KEY, body)).toEqual(r);
    expect(ctx.odoo.calls.filter((c) => c.method === 'submit')).toHaveLength(1);
    expect(await code(ctx.fnb.submit(ctx.ctx(t), KEY, { lines: [{ product_id: 22, qty: 3, attribute_value_ids: [101] }] }))).toBe('409 idempotency_conflict');
    expect(await code(ctx.fnb.submit(ctx.ctx(t), undefined, body))).toBe('400 idempotency_key_required');
    const list = await ctx.fnb.list(ctx.ctx(t));
    expect(list.items).toHaveLength(1);
    expect(await ctx.fnb.one(ctx.ctx(t), r.submission_id)).toMatchObject({ status: 'pending_confirmation' });
    expect(await code(ctx.fnb.one(ctx.ctx(t), OTHER_ID))).toBe('404 not_found');
  });

  it('galat Odoo: item_unavailable → 409 (+product_ids); pos_session_closed → 409 table_session_closed; kuota dikembalikan', async () => {
    const ctx = setup();
    const t = ctx.tokenOf((await ctx.open()).qr_url);
    const err = await catchApiError(() => ctx.fnb.submit(ctx.ctx(t), KEY, { lines: [{ product_id: 23, qty: 1 }] }));
    expect(err.getStatus()).toBe(409);
    // QA P3 r15: Odoo tanpa id → cadangan overlay menu (produk 23 available=false), hanya id yang ada di kiriman
    expect(err.body).toMatchObject({ error: { code: 'item_unavailable' }, product_ids: [23] });
    ctx.odoo.posSessionClosed.add(7);
    expect(await code(ctx.fnb.submit(ctx.ctx(t), 'k_second_attempt_000', { lines: [{ product_id: 21, qty: 1 }] }))).toBe('409 table_session_closed');
    expect([...ctx.repo.state.sessions.values()][0].submissions_count).toBe(0);
    expect([...ctx.repo.state.subs.values()].every((s) => s.status === 'failed')).toBe(true);
  });

  it('timeout ke Odoo: rekonsiliasi efn_qr_status — sudah diterima Odoo → 202 status (tanpa pesanan ganda); absent → failed + 503', async () => {
    const ctx = setup();
    const t = ctx.tokenOf((await ctx.open()).qr_url);
    // Odoo menerima, respons hilang: jalankan submit stub lalu lempar transport pada panggilan BFF
    const realSubmit = ctx.odoo.submit.bind(ctx.odoo);
    ctx.odoo.submit = async (...a) => {
      await realSubmit(...a);
      throw new OdooQrError('transport', null, 'timeout');
    };
    const warn = quiet();
    try {
      const r = await ctx.fnb.submit(ctx.ctx(t), KEY, { lines: [{ product_id: 21, qty: 1 }] });
      expect(r.status).toBe('pending_confirmation');
      expect(ctx.odoo.submissions.size).toBe(1);
      ctx.odoo.submit = async () => {
        throw new OdooQrError('transport');
      };
      expect(await code(ctx.fnb.submit(ctx.ctx(t), 'k_lost_before_odoo_1', { lines: [{ product_id: 21, qty: 1 }] }))).toBe('503 ordering_unavailable');
    } finally {
      warn.mockRestore();
    }
  });

  it('batas: kiriman per sesi (default 10, L-76) → 422 session_limit_reached + anomali; rate limit per sesi → 429', async () => {
    const ctx = setup({ FNB_MAX_SUBMISSIONS_PER_SESSION: '2', FNB_RL_SUBMIT_SESSION_LIMIT: '100' });
    const t = ctx.tokenOf((await ctx.open()).qr_url);
    await ctx.fnb.submit(ctx.ctx(t), 'k_submission_one_01', { lines: [{ product_id: 21, qty: 1 }] });
    await ctx.fnb.submit(ctx.ctx(t), 'k_submission_two_02', { lines: [{ product_id: 21, qty: 1 }] });
    const warn = quiet();
    try {
      expect(await code(ctx.fnb.submit(ctx.ctx(t), 'k_submission_three3', { lines: [{ product_id: 21, qty: 1 }] }))).toBe('422 session_limit_reached');
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"session_limit_hit"'))).toBe(true);
    } finally {
      warn.mockRestore();
    }
    const rl = setup({ FNB_RL_SUBMIT_SESSION_LIMIT: '1' });
    const t2 = rl.tokenOf((await rl.open()).qr_url);
    await rl.fnb.submit(rl.ctx(t2), 'k_submission_one_01', { lines: [{ product_id: 21, qty: 1 }] });
    const w2 = quiet();
    try {
      expect(await code(rl.fnb.submit(rl.ctx(t2), 'k_submission_two_02', { lines: [{ product_id: 21, qty: 1 }] }))).toBe('429 rate_limited');
    } finally {
      w2.mockRestore();
    }
  });

  it('ALTCHA first_submit: kiriman pertama tanpa/salah → 400 captcha_required', async () => {
    const ctx = setup({ FNB_CAPTCHA_MODE: 'first_submit' });
    const t = ctx.tokenOf((await ctx.open()).qr_url);
    ctx.altcha.ok = false;
    expect(await code(ctx.fnb.submit(ctx.ctx(t), KEY, { lines: [{ product_id: 21, qty: 1 }] }))).toBe('400 captcha_required');
    ctx.altcha.ok = true;
    await ctx.fnb.submit(ctx.ctx(t), KEY, { lines: [{ product_id: 21, qty: 1 }], altcha: 'payload' });
    ctx.altcha.ok = false; // kiriman berikutnya tidak diminta lagi
    await ctx.fnb.submit(ctx.ctx(t), 'k_after_first_000', { lines: [{ product_id: 21, qty: 1 }] });
  });

  it('klaim poin: Bearer wajib, idempoten, member lain → 409; akun dihapus → 409; poin QR mati → 422; lepas hanya pengklaim', async () => {
    const ctx = setup();
    const t = ctx.tokenOf((await ctx.open()).qr_url);
    expect(await code(ctx.fnb.claim(ctx.ctx(t)))).toBe('401 unauthorized');
    expect(await ctx.fnb.claim(ctx.ctx(t, ctx.bearer()))).toEqual({ claimed: true });
    expect(await ctx.fnb.claim(ctx.ctx(t, ctx.bearer()))).toEqual({ claimed: true });
    expect(ctx.repo.state.activity.get(MEMBER_HASH)?.kind).toBe('qr_claim');
    expect(await code(ctx.fnb.claim(ctx.ctx(t, ctx.bearer(OTHER_ID))))).toBe('409 points_already_claimed');
    expect(await code(ctx.fnb.unclaim(ctx.ctx(t, ctx.bearer(OTHER_ID))))).toBe('404 not_found');
    await ctx.fnb.unclaim(ctx.ctx(t, ctx.bearer()));
    ctx.repo.state.activeDeletes.add(OTHER_ID);
    expect(await code(ctx.fnb.claim(ctx.ctx(t, ctx.bearer(OTHER_ID))))).toBe('409 account_deleting');
    const off = setup({ FNB_QR_POINTS_ENABLED: 'false' });
    const t2 = off.tokenOf((await off.open()).qr_url);
    expect(await code(off.fnb.claim(off.ctx(t2, off.bearer())))).toBe('422 points_unavailable');
  });
});

describe('POST /v1/pos/fnb/events (A.4, L-70)', () => {
  const ev = (sid: string, seq: number, event: string, extra: Record<string, unknown>) => ({
    schema_version: 1,
    idempotency_key: `fnb:${sid}:${event}:${seq}`,
    seq,
    table_session_id: sid,
    company_code: 'PB',
    config_id: 7,
    event,
    occurred_at: '2026-10-05T03:10:00Z',
    ...extra,
  });
  const raw = (o: unknown) => Buffer.from(JSON.stringify(o));

  it('submission_status monoton; duplikat; seq basi; PT/config beda → 409; event tak dikenal', async () => {
    const ctx = setup();
    const s = await ctx.open();
    const t = ctx.tokenOf(s.qr_url);
    const sub = await ctx.fnb.submit(ctx.ctx(t), KEY, { lines: [{ product_id: 21, qty: 1 }] });
    const e1 = parseFnbEvent(raw(ev(s.table_session_id, 1, 'submission_status', { submission: { submission_ref: sub.submission_id, status: 'preparing' } })));
    expect(await ctx.pos.handleEvent(e1)).toEqual({ ok: true, status: 'applied' });
    expect(await ctx.pos.handleEvent(e1)).toEqual({ ok: true, status: 'duplicate' });
    const back = parseFnbEvent(raw(ev(s.table_session_id, 2, 'submission_status', { submission: { submission_ref: sub.submission_id, status: 'accepted' } })));
    expect(await ctx.pos.handleEvent(back)).toMatchObject({ status: 'ignored_stale' });
    expect((await ctx.fnb.one(ctx.ctx(t), sub.submission_id)).status).toBe('preparing');
    const old = parseFnbEvent(raw(ev(s.table_session_id, 1, 'submission_status', { idempotency_key: `fnb:${s.table_session_id}:submission_status:1`, submission: { submission_ref: sub.submission_id, status: 'ready' } })));
    void old;
    const warn = quiet();
    try {
      const wrong = parseFnbEvent(raw(ev(s.table_session_id, 3, 'submission_status', { company_code: 'BCF', submission: { submission_ref: sub.submission_id, status: 'ready' } })));
      expect(await code(ctx.pos.handleEvent(wrong))).toBe('409 company_mismatch');
    } finally {
      warn.mockRestore();
    }
    expect(await ctx.pos.handleEvent(parseFnbEvent(raw(ev(s.table_session_id, 4, 'table_moved', {}))))).toMatchObject({ status: 'ignored_unknown_event' });
  });

  it('L-80: session_closed paid + orders[] → earn per order `pos_earn:<uuid>` untuk pengklaim; attached=true → tidak earn; PT beda → 409; token mati', async () => {
    const ctx = setup();
    const s = await ctx.open();
    const t = ctx.tokenOf(s.qr_url);
    await ctx.fnb.submit(ctx.ctx(t), KEY, { lines: [{ product_id: 21, qty: 2 }] });
    await ctx.fnb.claim(ctx.ctx(t, ctx.bearer()));
    const ORDER_2 = 'f1e2d3c4-b5a6-4978-8a6b-5c4d3e2f1a0b';
    const warn = quiet();
    try {
      const wrongPt = parseFnbEvent(raw(ev(s.table_session_id, 1, 'session_closed', { close: { reason: 'paid', orders: [{ order_uuid: ORDER_2, eligible_amount: 1, company_code: 'BCF' }] } })));
      expect(await code(ctx.pos.handleEvent(wrongPt))).toBe('409 company_mismatch');
    } finally {
      warn.mockRestore();
    }
    const close = parseFnbEvent(
      raw(
        ev(s.table_session_id, 2, 'session_closed', {
          close: {
            reason: 'paid',
            orders: [
              { order_uuid: ORDER_2, eligible_amount: 50_000.75, company_code: 'PB', pos_member_attached: false },
              { order_uuid: ORDER_UUID, eligible_amount: 30_000, company_code: 'PB', pos_member_attached: true },
            ],
          },
        }),
      ),
    );
    expect(await ctx.pos.handleEvent(close)).toMatchObject({ status: 'applied' });
    expect(ctx.earn.calls).toEqual([{ memberId: MEMBER_ID, orderUuid: ORDER_2, eligibleAmount: 50_000 }]);
    expect(ctx.repo.state.sessions.get(s.table_session_id)).toMatchObject({ status: 'closed', close_reason: 'paid', earn_status: 'earned' });
    expect(await code(ctx.fnb.session(ctx.ctx(t)))).toBe('409 table_session_closed');
  });

  it('body: kunci tidak cocok seq/event/sesi, field tambahan → 400', async () => {
    const sid = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10';
    for (const b of [
      ev(sid, 2, 'order_closed', { idempotency_key: `fnb:${sid}:order_closed:3`, order: { order_uuid: ORDER_UUID, reason: 'paid' } }),
      ev(sid, 2, 'order_closed', { extra: 1, order: { order_uuid: ORDER_UUID, reason: 'paid' } }),
      ev(sid, 2, 'order_closed', { order: { order_uuid: ORDER_UUID, reason: 'refunded' } }),
      { ...ev(sid, 2, 'order_closed', {}), schema_version: 2 },
    ]) {
      expect(await code(() => parseFnbEvent(raw(b)))).toBe('400 invalid_request');
    }
  });

  it('guard HMAC: salah / replay > 300 s → 401; secret belum ada → 503', async () => {
    const config = testConfig({ ODOO_FNB_EVENT_HMAC: randomSecret() });
    const body = raw({ a: 1 });
    const t = Math.floor(Date.UTC(2026, 9, 5, 3) / 1000);
    const g = new FnbEventHmacGuard(config);
    g.now = () => t * 1000;
    const run = (h: string) => g.canActivate(httpContext({ headers: { 'x-efn-signature': h }, rawBody: body }));
    expect(run(`t=${t},v1=${computeWebhookSignature(config.odooFnbEventHmac, t, body)}`)).toBe(true);
    const warn = quiet();
    const err = jest.spyOn(Logger.prototype, 'error').mockImplementation(() => undefined);
    try {
      expect(await code(() => run(`t=${t},v1=${'0'.repeat(64)}`))).toBe('401 invalid_signature');
      g.now = () => (t + 301) * 1000;
      expect(await code(() => run(`t=${t},v1=${computeWebhookSignature(config.odooFnbEventHmac, t, body)}`))).toBe('401 invalid_signature');
      const none = new FnbEventHmacGuard(testConfig());
      expect(await code(() => none.canActivate(httpContext({ headers: {}, rawBody: body })))).toBe('503 ordering_unavailable');
    } finally {
      warn.mockRestore();
      err.mockRestore();
    }
  });
});

describe('Odoo QR json2 (A.5) & job', () => {
  it('path 13–15 persis; galat efn_qr:<kode>; parser menolak state/atribut asing', () => {
    expect(Object.values(ODOO_QR_PATHS)).toEqual(['efn.pos.qr.order/efn_qr_menu', 'efn.pos.qr.order/efn_qr_submit', 'efn.pos.qr.order/efn_qr_status']);
    expect(qrBusinessCode({ message: 'efn_qr:item_unavailable' })).toBe('item_unavailable');
    expect(qrBusinessCode({ message: 'efn_qr:apa_saja' })).toBeNull();
    expect(() => parseQrSubmit({ submission_ref: 'x', state: 'cooking' })).toThrow(OdooQrError);
    expect(() =>
      parseQrMenu({ config: { config_id: 7, company_code: 'PB', outlet_name: 'X' }, items: [{ product_id: 1, price_excl: 1, attributes: [{ attribute_id: 1, name: 'a', display_type: 'free_text', values: [] }] }], server_time: 'x' }),
    ).toThrow(OdooQrError);
  });

  it('bentuk respons Odoo E-27 apa adanya (QA G22): submit tanpa order_uuid + modifier L-78; status tanpa order_uuid / absent; galat bentuk ditolak', () => {
    // Persis bentuk `efn.pos.qr.order.efn_qr_submit` (services/odoo/addons/efn_pos_bff_order): tanpa order_uuid.
    const ref = '0A1B2C3D-4E5F-4A6B-8C7D-9E0F1A2B3C4D';
    const r = parseQrSubmit({
      submission_ref: ref,
      state: 'pending_confirmation',
      lines: [
        { product_id: 22, name: 'Latte', qty: 2, attribute_value_ids: [201, 101], attributes_text: 'Gula: Normal; Ekstra: Shot espresso' },
        { product_id: 21, name: 'Croissant', qty: 1, attribute_value_ids: [], attributes_text: null },
      ],
      amount_indicative_excl: 100000.0,
    });
    expect(r.submission_ref).toBe(ref.toLowerCase());
    expect(r.order_uuid).toBeNull();
    expect(r.lines[0]).toEqual({ product_id: 22, name: 'Latte', qty: 2, attribute_value_ids: [101, 201], attributes_text: 'Gula: Normal; Ekstra: Shot espresso' });
    expect(r.lines[1].attribute_value_ids).toEqual([]);
    expect(r.lines[1].attributes_text).toBeNull();
    // Odoo lama (sebelum L-78) tanpa kedua field: tetap diterima, bukan invalid_response.
    expect(parseQrSubmit({ submission_ref: ref, state: 'accepted', lines: [{ product_id: 21, name: 'C', qty: 1 }], amount_indicative_excl: 1 }).lines[0].attribute_value_ids).toEqual([]);
    // PTAV ganda / lebih dari 10 / nol → respons tidak valid (API10), bukan diteruskan ke pelanggan.
    for (const bad of [[5, 5], [0], Array.from({ length: 11 }, (_x, i) => i + 1)]) {
      expect(() => parseQrSubmit({ submission_ref: ref, state: 'accepted', lines: [{ product_id: 1, name: 'x', qty: 1, attribute_value_ids: bad }] })).toThrow(OdooQrError);
    }
    // efn_qr_status: [{submission_ref, state, seq}] — `absent` memakai seq 0, tanpa order_uuid.
    const rows = parseQrStatus([
      { submission_ref: ref, state: 'ready', seq: 1790000000 },
      { submission_ref: 'f'.repeat(8) + '-0000-4000-8000-' + 'a'.repeat(12), state: 'absent', seq: 0 },
    ]);
    expect(rows.map((x) => [x.state, x.seq, x.order_uuid])).toEqual([['ready', 1790000000, null], ['absent', 0, null]]);
    expect(() => parseQrStatus([{ submission_ref: ref, state: 'READY!', seq: 1 }])).toThrow(OdooQrError);
    expect(() => parseQrStatus({ submission_ref: ref })).toThrow(OdooQrError);
  });

  it('sweeper: sesi lewat expires_at → expired; menu sync mengisi overlay & config; heartbeat EFN_FNB_JOB', async () => {
    const ctx = setup();
    const s = await ctx.open();
    const jobs = new FnbJobsService(ctx.config, ctx.repo, ctx.odoo, ctx.fnb, ctx.metrics);
    jobs.now = ctx.clock.now;
    ctx.clock.advanceSeconds(14_401);
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    try {
      expect((await jobs.sweepOnce()).expired).toBe(1);
      expect(ctx.repo.state.sessions.get(s.table_session_id)?.status).toBe('expired');
      ctx.repo.state.menu.clear();
      expect(await jobs.menuOnce()).toEqual({ synced: 1, failed: 0 });
      expect((await ctx.repo.menuItems(7)).map((i) => i.product_id)).toEqual([21, 22, 23]);
      expect(log.mock.calls.some((c) => String(c[0]).startsWith('EFN_FNB_JOB {"status":"ok"'))).toBe(true);
    } finally {
      log.mockRestore();
    }
  });
});

describe('security review r15 (SEC-165/167/168/173, K1–K3, QA P3)', () => {
  // Karakter non-ASCII dibangun dari kode titik (sumber tetap ASCII murni).
  const ch = (...cps: number[]): string => String.fromCharCode(...cps);
  const fullwidth = (digits: string): string => [...digits].map((d) => ch(0xff10 + Number(d))).join('');
  const ZWSP = ch(0x200b);
  const RLO = ch(0x202e);
  const PDF = ch(0x202c);
  const LRI = ch(0x2066);
  const PDI = ch(0x2069);
  const BOM = ch(0xfeff);
  const ev = (sid: string, seq: number, event: string, extra: Record<string, unknown>) => ({
    schema_version: 1,
    idempotency_key: `fnb:${sid}:${event}:${seq}`,
    seq,
    table_session_id: sid,
    company_code: 'PB',
    config_id: 7,
    event,
    occurred_at: '2026-10-05T03:10:00Z',
    ...extra,
  });
  const raw = (o: unknown) => Buffer.from(JSON.stringify(o));
  const ORDER_X = '7d3c2b1a-0f9e-4d8c-9b7a-6f5e4d3c2b1a';
  const ORDER_Y = '8e4d3c2b-1a0f-4e9d-8c7b-7a6f5e4d3c2b';

  it('SEC-168 vektor bersama BFF↔Odoo: NFKC, karakter format dibuang SEBELUM aturan digit/@', async () => {
    const reject = [
      fullwidth('081234567890'), // angka fullwidth → ASCII setelah NFKC
      `0812${ZWSP}3456${ZWSP}789`, // zero-width di antara digit (dihapus, bukan spasi)
      `${RLO}0987654321${PDF}`, // bidi override membalik tampilan nomor
      `${LRI}08 12 34${PDI} 56 78`, // isolate + spasi
      '0812-3456 / 789', // pemisah "/" (lolos di aturan lama)
      'meja 0812, kode 3456, 7', // total digit ≥ 8 walau tersebar
      ch(0x0660, 0x0668, 0x0661, 0x0662, 0x0663, 0x0664, 0x0665, 0x0666), // digit Arab-Indik (Nd, tidak diubah NFKC)
      `nama${ch(0xff20)}mail.com`, // '@' fullwidth → '@'
      `${BOM}a@b.c`,
    ];
    for (const bad of reject) expect(await code(() => sanitizeNote(bad))).toBe('400 note_invalid');
    // karakter format dibuang dari teks yang lolos (tidak sampai ke KDS/printer)
    expect(sanitizeNote(`${RLO}pedas${PDF} level 3`)).toBe('pedas level 3');
    expect(sanitizeNote(`tanpa${ZWSP}${BOM} es`)).toBe('tanpa es');
    expect(sanitizeNote(`${ch(0x3000)}gula ${ch(0x00a0)} sedikit`)).toBe('gula sedikit'); // spasi lebar/NBSP → spasi
    expect(sanitizeNote('2 piring, 3 sendok, level 1')).toBe('2 piring, 3 sendok, level 1'); // 3 digit
    expect(sanitizeNote('1234567')).toBe('1234567'); // 7 digit masih boleh
    expect(await code(() => sanitizeNote('12345678'))).toBe('400 note_invalid');
    // panjang dihitung code point: 139 + emoji (2 unit UTF-16) = 140 → lolos; 141 → tolak
    const emoji = ch(0xd83d, 0xde00);
    expect(sanitizeNote(`${'x'.repeat(139)}${emoji}`)).toBe(`${'x'.repeat(139)}${emoji}`);
    expect(await code(() => sanitizeNote(`${'x'.repeat(140)}${emoji}`))).toBe('400 note_invalid');
    // hanya karakter format → kosong → null
    expect(sanitizeNote(`${ZWSP}${BOM}${RLO}`)).toBeNull();
  });

  it('SEC-167: nomor cetak buka sesi hingga 99 999 999', () => {
    expect(OPEN_KEY_RE.test('fnb_open:7:12:99999999')).toBe(true);
    expect(OPEN_KEY_RE.test('fnb_open:7:12:100000000')).toBe(false);
    expect(parseOpenSession({ idempotency_key: 'fnb_open:7:12:10000', company_code: 'PB', config_id: 7, table_id: 12, table_label: 'T12' }).print_seq).toBe(10_000);
  });

  it('K1: order_uuid Odoo harus UUID (submit/status/event); null diterima; event mengisi kolom kosong, uuid lain → anomali', async () => {
    const ref = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d';
    expect(parseQrSubmit({ submission_ref: ref, state: 'accepted', order_uuid: ORDER_X }).order_uuid).toBe(ORDER_X);
    expect(parseQrSubmit({ submission_ref: ref, state: 'accepted', order_uuid: null }).order_uuid).toBeNull();
    expect(() => parseQrSubmit({ submission_ref: ref, state: 'accepted', order_uuid: 'Order-123' })).toThrow(OdooQrError);
    expect(parseQrStatus([{ submission_ref: ref, state: 'ready', seq: 2, order_uuid: ORDER_X }])[0].order_uuid).toBe(ORDER_X);
    expect(() => parseQrStatus([{ submission_ref: ref, state: 'ready', seq: 2, order_uuid: 'x' }])).toThrow(OdooQrError);
    const sid = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10';
    expect(await code(() => parseFnbEvent(raw(ev(sid, 1, 'submission_status', { submission: { submission_ref: ref, status: 'ready', order_uuid: 'abc' } }))))).toBe('400 invalid_request');

    const ctx = setup();
    const s = await ctx.open();
    const sub = await ctx.fnb.submit(ctx.ctx(ctx.tokenOf(s.qr_url)), KEY, { lines: [{ product_id: 21, qty: 1 }] });
    // mock = bentuk Odoo nyata: hasil submit memuat order_uuid (UUID)
    const stored = ctx.repo.state.subs.get(sub.submission_id);
    expect(stored?.order_uuid).toMatch(/^[0-9a-f-]{36}$/);
    // simulasi kiriman yang hasil submit-nya hilang (order_uuid kosong) → event mengisi
    if (stored) stored.order_uuid = null;
    const e1 = parseFnbEvent(raw(ev(s.table_session_id, 1, 'submission_status', { submission: { submission_ref: sub.submission_id, status: 'preparing', order_uuid: ORDER_X } })));
    await ctx.pos.handleEvent(e1);
    expect(ctx.repo.state.subs.get(sub.submission_id)).toMatchObject({ order_uuid: ORDER_X, order_state: 'draft', status: 'preparing' });
    const warn = quiet();
    try {
      const e2 = parseFnbEvent(raw(ev(s.table_session_id, 2, 'submission_status', { submission: { submission_ref: sub.submission_id, status: 'ready', order_uuid: ORDER_Y } })));
      await ctx.pos.handleEvent(e2);
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"order_uuid_mismatch"'))).toBe(true);
    } finally {
      warn.mockRestore();
    }
    expect(ctx.repo.state.subs.get(sub.submission_id)?.order_uuid).toBe(ORDER_X);
  });

  it('K2/SEC-165: pos_member_attached=true → earn_status pos_member tanpa earn BFF; field absen → tidak earn + anomali critical', async () => {
    const ctx = setup();
    const s = await ctx.open();
    const t = ctx.tokenOf(s.qr_url);
    const sub = await ctx.fnb.submit(ctx.ctx(t), KEY, { lines: [{ product_id: 21, qty: 1 }] });
    await ctx.fnb.claim(ctx.ctx(t, ctx.bearer()));
    const orderOfSub = ctx.repo.state.subs.get(sub.submission_id)?.order_uuid as string;
    const warn = quiet();
    try {
      const close = parseFnbEvent(
        raw(
          ev(s.table_session_id, 1, 'session_closed', {
            close: {
              reason: 'paid',
              orders: [
                { order_uuid: orderOfSub, eligible_amount: 40_000, company_code: 'PB', pos_member_attached: true },
                { order_uuid: ORDER_X, eligible_amount: 90_000, company_code: 'PB' }, // field hilang
              ],
            },
          }),
        ),
      );
      expect(await ctx.pos.handleEvent(close)).toMatchObject({ status: 'applied' });
      expect(warn.mock.calls.some((c) => String(c[0]).includes('pos_member_attached_missing') && String(c[0]).includes('"severity":"critical"'))).toBe(true);
    } finally {
      warn.mockRestore();
    }
    expect(ctx.earn.calls).toEqual([]);
    expect(ctx.repo.state.subs.get(sub.submission_id)).toMatchObject({ order_state: 'paid', earn_status: 'pos_member', points_earned: 0 });
  });

  it('K3: close diproses → close_event_at (transaksi yang sama); sesi pendahulu yang di-reissue ikut ditandai', async () => {
    const ctx = setup();
    const a = await ctx.open();
    await ctx.fnb.submit(ctx.ctx(ctx.tokenOf(a.qr_url)), KEY, { lines: [{ product_id: 21, qty: 1 }] });
    const b = await ctx.open({ idempotency_key: 'fnb_open:7:12:2', reissue: true });
    expect(ctx.repo.state.sessions.get(a.table_session_id)).toMatchObject({ status: 'closed', close_reason: 'reissued', close_event_at: null });
    const other = await ctx.pos.openSession(parseOpenSession({ idempotency_key: 'fnb_open:7:13:1', company_code: 'PB', config_id: 7, table_id: 13, table_label: 'T13' }));
    await ctx.pos.handleEvent(
      parseFnbEvent(raw(ev(b.table_session_id, 1, 'session_closed', { close: { reason: 'paid', orders: [{ order_uuid: ORDER_X, eligible_amount: 1, company_code: 'PB', pos_member_attached: false }] } }))),
    );
    expect(ctx.repo.state.sessions.get(b.table_session_id)?.close_event_at).toBeInstanceOf(Date);
    expect(ctx.repo.state.sessions.get(a.table_session_id)?.close_event_at).toBeInstanceOf(Date);
    expect(ctx.repo.state.sessions.get(other.table_session_id)?.close_event_at).toBeNull(); // meja lain tidak tersentuh
    // event close yang gagal (409) → rollback: tidak ada penanda
    const c2 = await ctx.pos.openSession(parseOpenSession({ idempotency_key: 'fnb_open:7:14:1', company_code: 'PB', config_id: 7, table_id: 14, table_label: 'T14' }));
    const warn = quiet();
    try {
      const bad = parseFnbEvent(raw(ev(c2.table_session_id, 1, 'session_closed', { close: { reason: 'paid', orders: [{ order_uuid: ORDER_Y, eligible_amount: 1, company_code: 'BCF', pos_member_attached: false }] } })));
      expect(await code(ctx.pos.handleEvent(bad))).toBe('409 company_mismatch');
    } finally {
      warn.mockRestore();
    }
    expect(ctx.repo.state.sessions.get(c2.table_session_id)?.close_event_at).toBeNull();
  });

  it('SEC-173: FNB_RL_EVENTS per config_id setelah guard → 429 (klaim tidak terpakai, retry berikutnya diterapkan)', async () => {
    const ctx = setup({ FNB_RL_EVENTS_LIMIT: '1', FNB_RL_EVENTS_WINDOW_SECONDS: '60' });
    const s = await ctx.open();
    const sub = await ctx.fnb.submit(ctx.ctx(ctx.tokenOf(s.qr_url)), KEY, { lines: [{ product_id: 21, qty: 1 }] });
    const mk = (seq: number, status: string) =>
      parseFnbEvent(raw(ev(s.table_session_id, seq, 'submission_status', { submission: { submission_ref: sub.submission_id, status } })));
    expect(await ctx.pos.handleEvent(mk(1, 'accepted'))).toMatchObject({ status: 'applied' });
    const warn = quiet();
    try {
      expect(await code(ctx.pos.handleEvent(mk(2, 'preparing')))).toBe('429 rate_limited');
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"events_rate_limited"'))).toBe(true);
    } finally {
      warn.mockRestore();
    }
    ctx.clock.advanceSeconds(61);
    expect(await ctx.pos.handleEvent(mk(2, 'preparing'))).toMatchObject({ status: 'applied' }); // bukan `duplicate`
  });

  it('QA P3: orders[] ≤ 100 & body ≤ 16 KB; 101 → 400', async () => {
    const sid = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10';
    const orders = (n: number) =>
      Array.from({ length: n }, (_x, i) => ({ order_uuid: `${String(i).padStart(8, '0')}-0000-4000-8000-000000000000`, eligible_amount: 1000, company_code: 'PB', pos_member_attached: false }));
    const ok = raw(ev(sid, 1, 'session_closed', { close: { reason: 'paid', orders: orders(100) } }));
    expect(ok.length).toBeLessThanOrEqual(16 * 1024);
    expect(parseFnbEvent(ok).close?.orders).toHaveLength(100);
    expect(await code(() => parseFnbEvent(raw(ev(sid, 1, 'session_closed', { close: { reason: 'paid', orders: orders(101) } }))))).toBe('400 invalid_request');
    expect(await code(() => parseFnbEvent(Buffer.alloc(16 * 1024 + 1, 0x20)))).toBe('400 invalid_request');
  });

  it('QA P3: item_unavailable membawa product_ids dari Odoo (efn_qr:item_unavailable:<id>,…) hanya yang ada di kiriman', async () => {
    expect(qrBusinessError({ message: 'efn_qr:item_unavailable:22,21,22' })).toEqual({ code: 'item_unavailable', productIds: [21, 22] });
    expect(qrBusinessError({ message: 'efn_qr:item_unavailable' })).toEqual({ code: 'item_unavailable', productIds: [] });
    expect(qrBusinessError({ message: 'efn_qr:limit_exceeded:21' })).toBeNull(); // id hanya untuk item_unavailable
    expect(qrBusinessError({ message: 'efn_qr:item_unavailable:0' })).toBeNull();
    expect(qrBusinessCode({ error: { message: 'x', data: { message: 'efn_qr:item_unavailable:5' } } })).toBe('item_unavailable');
    const ctx = setup();
    const t = ctx.tokenOf((await ctx.open()).qr_url);
    // Odoo menyebut 21 (habis setelah sinkron menu) + 99 (bukan bagian kiriman → dibuang)
    ctx.odoo.failNext.set('submit', new OdooQrError('business', 'item_unavailable', undefined, [21, 99]));
    const err = await catchApiError(() => ctx.fnb.submit(ctx.ctx(t), KEY, { lines: [{ product_id: 21, qty: 1 }, { product_id: 22, qty: 1, attribute_value_ids: [101] }] }));
    expect(err.body).toMatchObject({ error: { code: 'item_unavailable' }, product_ids: [21] });
  });
});
