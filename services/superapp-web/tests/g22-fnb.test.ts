/**
 * G22 — pesan dari meja (docs/04 §14.A): token fragment → memori, catatan, kiriman, idempotensi, parser, polling.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addTableLine,
  buildSubmitBody,
  classifyFnbFailure,
  fingerprint,
  FNB_ERROR_MESSAGES,
  FNB_IDEMPOTENCY_KEY_RE,
  fnbErrorMessage,
  fnbPollDelayMs,
  isTableToken,
  ITEM_UNAVAILABLE_GENERIC,
  itemUnavailableFeedback,
  newIdempotencyKey,
  normalizeNote,
  parseFnbMenu,
  parseFnbOrders,
  parseFnbSession,
  parseStoredAttempt,
  parseSubmitAccepted,
  parseUnavailableIds,
  resolveAttemptKey,
  sanitizeSubmitBody,
  setTableLineQty,
  submissionStatusLabel,
  submitFingerprint,
  tableEstimateExcl,
  tableItemCount,
  tableTokenFromHash,
  validateNote,
  type TableLine,
} from '@/lib/fnb';
import { captureTableToken, clearTableToken, getTableToken } from '@/lib/table-token';

const TOKEN = `fts1.${'A'.repeat(22)}.1790000000.${'B'.repeat(43)}`;
const SID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';
const LIMITS = { max_lines: 3, max_line_qty: 5 };

describe('token sesi meja (fragment → memori)', () => {
  beforeEach(() => clearTableToken());
  afterEach(() => vi.unstubAllGlobals());

  it('tableTokenFromHash: hanya kunci t, bentuk token harus valid', () => {
    expect(tableTokenFromHash(`#t=${TOKEN}`)).toBe(TOKEN);
    expect(tableTokenFromHash(`t=${TOKEN}`)).toBe(TOKEN);
    expect(tableTokenFromHash(`#x=1&t=${TOKEN}`)).toBe(TOKEN);
    expect(tableTokenFromHash(`#ts=${TOKEN}`)).toBeNull();
    expect(tableTokenFromHash('#t=abc')).toBeNull();
    expect(tableTokenFromHash(`#t=${TOKEN}x y`)).toBeNull();
    expect(tableTokenFromHash(`#t=${TOKEN}%2e`)).toBeNull();
    expect(tableTokenFromHash('#')).toBeNull();
    expect(tableTokenFromHash('')).toBeNull();
    expect(tableTokenFromHash(`#t=${'A'.repeat(500)}`)).toBeNull();
  });

  it('isTableToken menolak token dengan prefix/versi lain dan terlalu panjang', () => {
    expect(isTableToken(TOKEN)).toBe(true);
    expect(isTableToken(TOKEN.replace('fts1', 'fts2'))).toBe(false);
    expect(isTableToken(`${TOKEN}.extra`)).toBe(false);
    expect(isTableToken(`${TOKEN}${'C'.repeat(300)}`)).toBe(false);
    expect(isTableToken(null)).toBe(false);
  });

  it('captureTableToken: token masuk memori, URL dibersihkan ke pathname, tidak menyentuh storage', () => {
    const touched = vi.fn(() => {
      throw new Error('storage tidak boleh dipakai untuk token');
    });
    const trap = new Proxy({}, { get: touched, set: touched });
    vi.stubGlobal('localStorage', trap);
    vi.stubGlobal('sessionStorage', trap);
    const replaceState = vi.fn();
    const hist = { state: { k: 1 }, replaceState };
    const t = captureTableToken({ hash: `#t=${TOKEN}`, pathname: '/meja' }, hist);
    expect(t).toBe(TOKEN);
    expect(getTableToken()).toBe(TOKEN);
    expect(replaceState).toHaveBeenCalledTimes(1);
    expect(replaceState).toHaveBeenCalledWith({ k: 1 }, '', '/meja');
    expect(JSON.stringify(replaceState.mock.calls)).not.toContain('fts1');
    expect(touched).not.toHaveBeenCalled();
  });

  it('panggilan kedua (StrictMode / navigasi klien) tetap mengembalikan token yang ditahan', () => {
    const hist = { state: null, replaceState: vi.fn() };
    captureTableToken({ hash: `#t=${TOKEN}`, pathname: '/meja' }, hist);
    expect(captureTableToken({ hash: '', pathname: '/meja' }, hist)).toBe(TOKEN);
    expect(hist.replaceState).toHaveBeenCalledTimes(1); // tidak ada fragment lagi → tidak perlu bersihkan
  });

  it('fragment tak berisi token valid tetap dibersihkan; query ?ts= tidak pernah diterima', () => {
    const hist = { state: null, replaceState: vi.fn() };
    expect(captureTableToken({ hash: '#t=bukan-token', pathname: '/meja' }, hist)).toBeNull();
    expect(hist.replaceState).toHaveBeenCalledTimes(1);
    const hist2 = { state: null, replaceState: vi.fn() };
    expect(captureTableToken({ hash: '', pathname: '/meja' }, hist2, `?ts=${TOKEN}`)).toBeNull();
    expect(getTableToken()).toBeNull();
    expect(hist2.replaceState).toHaveBeenCalledWith(null, '', '/meja'); // query dibuang dari address bar
  });

  it('clearTableToken membuang token; replaceState melempar tidak membocorkan error', () => {
    const hist = {
      state: null,
      replaceState: () => {
        throw new Error('x');
      },
    };
    expect(captureTableToken({ hash: `#t=${TOKEN}`, pathname: '/meja' }, hist)).toBe(TOKEN);
    clearTableToken();
    expect(getTableToken()).toBeNull();
  });
});

describe('catatan item (≤ 140, tanpa nomor HP / email)', () => {
  it('normalisasi: kontrol & baris baru → spasi, rapikan', () => {
    expect(normalizeNote('  tanpa\n\tes   ')).toBe('tanpa es');
  });
  it('lolos / ditolak', () => {
    expect(validateNote('tanpa es, gula sedikit')).toEqual({ ok: true, value: 'tanpa es, gula sedikit' });
    expect(validateNote('')).toEqual({ ok: true, value: '' });
    expect(validateNote('a'.repeat(140)).ok).toBe(true);
    expect(validateNote('a'.repeat(141))).toEqual({ ok: false, reason: 'too_long' });
    expect(validateNote('hubungi 0812 3456 7890')).toEqual({ ok: false, reason: 'looks_like_phone' });
    expect(validateNote('0812-3456-7890')).toEqual({ ok: false, reason: 'looks_like_phone' });
    expect(validateNote('a@b.com')).toEqual({ ok: false, reason: 'has_at' });
    expect(validateNote('meja 12, 2 orang').ok).toBe(true); // deret digit pendek tidak dianggap nomor HP
  });
});

describe('keranjang meja', () => {
  const kopi = { product_id: 5, name: 'Kopi', price_excl: 25000, attributes: [] };
  const input = (over: Partial<Parameters<typeof addTableLine>[1]> = {}) => ({
    product: kopi,
    qty: 1,
    attribute_value_ids: [],
    options_text: '',
    note: '',
    ...over,
  });

  it('baris sama (produk + atribut + catatan) digabung dan dibatasi max qty', () => {
    let lines: TableLine[] = [];
    for (let i = 0; i < 7; i += 1) {
      const r = addTableLine(lines, input(), LIMITS);
      expect(r.ok).toBe(true);
      if (r.ok) lines = r.lines;
    }
    expect(lines).toHaveLength(1);
    expect(lines[0]!.qty).toBe(5);
  });

  it('atribut/catatan berbeda = baris berbeda; urutan id atribut tidak berpengaruh', () => {
    let lines: TableLine[] = [];
    const add = (over: Parameters<typeof input>[0]) => {
      const r = addTableLine(lines, input(over), LIMITS);
      if (r.ok) lines = r.lines;
      return r;
    };
    add({ attribute_value_ids: [3, 1] });
    add({ attribute_value_ids: [1, 3] });
    add({ attribute_value_ids: [1, 3], note: 'tanpa es' });
    expect(lines).toHaveLength(2);
    expect(lines[0]!.qty).toBe(2);
    expect(lines[0]!.attribute_value_ids).toEqual([1, 3]);
    expect(add({ attribute_value_ids: [9] }).ok).toBe(true);
    expect(add({ attribute_value_ids: [10] })).toEqual({ ok: false, reason: 'too_many_lines' });
  });

  it('input tidak valid ditolak; setQty ≤ 0 menghapus; total & estimasi', () => {
    expect(addTableLine([], input({ qty: 0 }), LIMITS)).toEqual({ ok: false, reason: 'invalid' });
    expect(addTableLine([], input({ attribute_value_ids: Array.from({ length: 11 }, (_, i) => i + 1) }), LIMITS)).toEqual({ ok: false, reason: 'invalid' });
    const r = addTableLine([], input({ qty: 2 }), LIMITS);
    const lines = r.ok ? r.lines : [];
    expect(tableItemCount(lines)).toBe(2);
    expect(tableEstimateExcl(lines)).toBe(50000);
    expect(setTableLineQty(lines, lines[0]!.key, 0, 5)).toEqual([]);
    expect(setTableLineQty(lines, lines[0]!.key, 99, 5)[0]!.qty).toBe(5);
  });

  it('estimasi menambahkan tambahan atribut (indikatif, sebelum pajak)', () => {
    const product = {
      ...kopi,
      attributes: [{ attribute_id: 1, name: 'Ukuran', display_type: 'radio' as const, required: true, values: [{ id: 11, name: 'Large', price_extra_excl: 5000 }] }],
    };
    const r = addTableLine([], { product, qty: 2, attribute_value_ids: [11], options_text: 'Ukuran: Large', note: '' }, LIMITS);
    expect(r.ok && tableEstimateExcl(r.lines)).toBe(60000);
  });

  it('buildSubmitBody: tanpa harga/meja/member; catatan hanya bila ada; altcha opsional', () => {
    const r = addTableLine([], input({ qty: 2, attribute_value_ids: [2, 1], note: 'tanpa es' }), LIMITS);
    const lines = r.ok ? r.lines : [];
    const body = buildSubmitBody(lines);
    expect(body).toEqual({ lines: [{ product_id: 5, qty: 2, attribute_value_ids: [1, 2], note: 'tanpa es' }] });
    expect(JSON.stringify(body)).not.toMatch(/price|harga|table|config|member|company/i);
    expect(buildSubmitBody(lines, 'abc=')).toHaveProperty('altcha', 'abc=');
    const noNote = addTableLine([], input(), LIMITS);
    expect(buildSubmitBody(noNote.ok ? noNote.lines : []).lines[0]).not.toHaveProperty('note');
  });
});

describe('sanitizeSubmitBody (Route Handler)', () => {
  it('allowlist: field di luar kontrak dibuang, id atribut diurutkan, catatan dinormalisasi', () => {
    const r = sanitizeSubmitBody({
      price: 1,
      lines: [{ product_id: 5, qty: 2, attribute_value_ids: [9, 3, 3], note: '  tanpa   es ', price_excl: 5, member_id: 'x' }],
    });
    expect(r).toEqual({ ok: true, body: { lines: [{ product_id: 5, qty: 2, attribute_value_ids: [3, 9], note: 'tanpa es' }] } });
  });
  it('catatan bermasalah → note_invalid; bentuk salah → invalid_request', () => {
    expect(sanitizeSubmitBody({ lines: [{ product_id: 5, qty: 1, note: 'telp 081234567890' }] })).toEqual({ ok: false, code: 'note_invalid' });
    expect(sanitizeSubmitBody({ lines: [{ product_id: 5, qty: 1, note: 'a@b' }] })).toEqual({ ok: false, code: 'note_invalid' });
    expect(sanitizeSubmitBody({ lines: [{ product_id: 5, qty: 1, note: 5 }] })).toEqual({ ok: false, code: 'invalid_request' });
    for (const bad of [null, [], {}, { lines: [] }, { lines: 'x' }, { lines: [{ product_id: 0, qty: 1 }] }, { lines: [{ product_id: 5, qty: 0 }] }, { lines: [{ product_id: 5, qty: 1.5 }] }, { lines: [{ product_id: 5, qty: 100 }] }, { lines: [{ product_id: 5, qty: 1, attribute_value_ids: ['a'] }] }, { lines: [{ product_id: 5, qty: 1, attribute_value_ids: Array.from({ length: 11 }, (_, i) => i + 1) }] }]) {
      expect(sanitizeSubmitBody(bad)).toEqual({ ok: false, code: 'invalid_request' });
    }
    expect(sanitizeSubmitBody({ lines: Array.from({ length: 101 }, () => ({ product_id: 5, qty: 1 })) })).toEqual({ ok: false, code: 'invalid_request' });
  });
  it('altcha: base64 wajar diterima, selain itu ditolak', () => {
    const base = { lines: [{ product_id: 5, qty: 1 }] };
    expect(sanitizeSubmitBody({ ...base, altcha: 'eyJhbGc+/=' })).toMatchObject({ ok: true, body: { altcha: 'eyJhbGc+/=' } });
    expect(sanitizeSubmitBody({ ...base, altcha: '<script>' })).toEqual({ ok: false, code: 'invalid_request' });
    expect(sanitizeSubmitBody({ ...base, altcha: 5 })).toEqual({ ok: false, code: 'invalid_request' });
  });
});

describe('Idempotency-Key kiriman', () => {
  it('newIdempotencyKey cocok kontrak 16–128 [A-Za-z0-9_-]; fallback tanpa randomUUID', () => {
    expect(newIdempotencyKey()).toMatch(FNB_IDEMPOTENCY_KEY_RE);
    const fallback = newIdempotencyKey({ getRandomValues: (a: ArrayBufferView) => { (a as Uint8Array).fill(171); return a; } } as unknown as Crypto);
    expect(fallback).toBe('ab'.repeat(16));
    expect(fallback).toMatch(FNB_IDEMPOTENCY_KEY_RE);
    expect(() => newIdempotencyKey(undefined as unknown as Crypto)).not.toThrow(); // default parameter → globalThis.crypto
  });
  it('key dipakai ulang bila isi sama (retry aman), baru bila isi berubah', () => {
    const a = addTableLine([], { product: { product_id: 5, name: 'Kopi', price_excl: 1, attributes: [] }, qty: 1, attribute_value_ids: [], options_text: '', note: '' }, LIMITS);
    const lines = a.ok ? a.lines : [];
    const fp = submitFingerprint(lines);
    const first = resolveAttemptKey(null, fp, () => 'K'.repeat(20));
    expect(first).toEqual({ key: 'K'.repeat(20), fp });
    expect(resolveAttemptKey(first, fp, () => 'Z'.repeat(20)).key).toBe('K'.repeat(20));
    const b = addTableLine(lines, { product: { product_id: 6, name: 'Teh', price_excl: 1, attributes: [] }, qty: 1, attribute_value_ids: [], options_text: '', note: '' }, LIMITS);
    const fp2 = submitFingerprint(b.ok ? b.lines : []);
    expect(fp2).not.toBe(fp);
    expect(resolveAttemptKey(first, fp2, () => 'Z'.repeat(20)).key).toBe('Z'.repeat(20));
  });
  it('fingerprint deterministik; parseStoredAttempt menolak data rusak', () => {
    expect(fingerprint('abc')).toBe(fingerprint('abc'));
    expect(fingerprint('abc')).not.toBe(fingerprint('abd'));
    expect(parseStoredAttempt(JSON.stringify({ key: 'K'.repeat(20), fp: 'x.1' }))).toEqual({ key: 'K'.repeat(20), fp: 'x.1' });
    for (const bad of [null, '', '{rusak', JSON.stringify({ key: 'pendek', fp: 'x' }), JSON.stringify({ key: 'K'.repeat(20) }), JSON.stringify({ key: 'bukan valid!'.padEnd(20, 'x'), fp: 'x' })]) {
      expect(parseStoredAttempt(bad)).toBeNull();
    }
  });
});

describe('parser respons (allowlist)', () => {
  it('session: default aman (mode waiter), batas default, poin default', () => {
    const s = parseFnbSession({ outlet_name: 'PB Kemang', table_label: 'T12', secret: 'x' })!;
    expect(s.confirm_mode).toBe('waiter');
    expect(s.limits).toEqual({ max_lines: 30, max_line_qty: 20, submissions_left: null });
    expect(s.points).toEqual({ claimable: false, claimed: false, claimed_by_me: false });
    expect(JSON.stringify(s)).not.toContain('secret');
    expect(parseFnbSession({ outlet_name: 'x' })).toBeNull();
    expect(parseFnbSession({ table_label: 'T1', confirm_mode: 'auto', points: { claimable: true, claimed: true, claimed_by_me: true } })).toMatchObject({
      confirm_mode: 'auto',
      points: { claimable: true, claimed: true, claimed_by_me: true },
    });
  });
  it('menu: kategori terurut, atribut di-parse, produk rusak/duplikat dibuang', () => {
    const m = parseFnbMenu({
      categories: [{ category_id: 2, name: 'Minuman', sequence: 2 }, { category_id: 1, name: 'Roti', sequence: 1 }, { name: 'tanpa id' }],
      products: [
        { product_id: 5, name: 'Kopi', category_id: 2, price_excl: 25000, tax_labels: ['PB1 10%'], price_note: 'belum termasuk pajak & layanan', available: true, attributes: [{ attribute_id: 1, name: 'Gula', display_type: 'radio', required: true, values: [{ id: 11, name: 'Normal', price_extra_excl: 0 }] }] },
        { product_id: 5, name: 'duplikat', price_excl: 1 },
        { product_id: 6, name: 'Habis', price_excl: 1000, available: false },
        { product_id: 7, name: 'Tanpa harga' },
        { product_id: 8, name: 'Negatif', price_excl: -1 },
      ],
    })!;
    expect(m.categories.map((c) => c.name)).toEqual(['Roti', 'Minuman']);
    expect(m.products.map((p) => p.product_id)).toEqual([5, 6]);
    expect(m.products[0]!.attributes[0]!.values[0]!.id).toBe(11);
    expect(m.products[1]!.available).toBe(false);
    expect(m.products[1]!.price_note).toBe('belum termasuk pajak & layanan');
    expect(parseFnbMenu({ products: 'x' })).toBeNull();
  });
  it('orders: banyak kiriman per sesi, terbaru di atas, status tak dikenal dinormalkan', () => {
    const items = parseFnbOrders({
      items: [
        { submission_id: SID, created_at: '2026-10-19T10:00:00Z', status: 'preparing', lines: [{ name: 'Kopi', qty: 2 }, { name: 'x', qty: 0 }] },
        { submission_id: 'bukan-uuid', status: 'accepted' },
        { submission_id: '9b1d2c3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e', created_at: '2026-10-19T10:30:00Z', status: 'ASING!' },
      ],
    })!;
    expect(items.map((i) => i.status)).toEqual(['unknown', 'preparing']);
    expect(items[1]!.lines).toEqual([{ name: 'Kopi', qty: 2, attributes_text: '', attribute_value_ids: [] }]);
    expect(parseFnbOrders(null)).toBeNull();
  });
  it('orders: attributes_text & attribute_value_ids disimpan per baris (G22 r15)', () => {
    const items = parseFnbOrders({
      items: [
        {
          submission_id: SID,
          status: 'accepted',
          lines: [
            { name: 'Kopi', qty: 1, attributes_text: 'Less sugar, Es', attribute_value_ids: [9, 3, 3] },
            { name: 'Teh', qty: 1, attributes_text: 5, attribute_value_ids: ['x'] },
            { name: 'Roti', qty: 1, attributes_text: 'a\nb', attribute_value_ids: 'bukan-array' },
          ],
        },
      ],
    })!;
    expect(items[0]!.lines).toEqual([
      { name: 'Kopi', qty: 1, attributes_text: 'Less sugar, Es', attribute_value_ids: [3, 9] },
      { name: 'Teh', qty: 1, attributes_text: '', attribute_value_ids: [] },
      { name: 'Roti', qty: 1, attributes_text: 'a b', attribute_value_ids: [] },
    ]);
  });
  it('item_unavailable: tandai hanya id yang ada di keranjang; id kosong -> pesan umum tanpa klaim penandaan', () => {
    const cart = [{ product_id: 5 }, { product_id: 6 }];
    const marked = itemUnavailableFeedback([6, 99], cart);
    expect(marked.marked).toEqual([6]);
    expect(marked.text).toBe(FNB_ERROR_MESSAGES.item_unavailable);
    expect(marked.text).toMatch(/ditandai/);
    for (const ids of [[], [99]]) {
      const g = itemUnavailableFeedback(ids, cart);
      expect(g.marked).toEqual([]);
      expect(g.text).toBe(ITEM_UNAVAILABLE_GENERIC);
      expect(g.text).not.toMatch(/ditandai/);
    }
  });
  it('202 submit: allowlist', () => {
    expect(parseSubmitAccepted({ submission_id: SID, status: 'pending_confirmation', lines: [{ product_id: 5, name: 'Kopi', qty: 1 }], indicative_subtotal_excl: 25000, odoo_ref: 'S0001' })).toEqual({
      submission_id: SID,
      status: 'pending_confirmation',
      lines: [{ name: 'Kopi', qty: 1, attributes_text: '', attribute_value_ids: [] }],
      indicative_subtotal_excl: 25000,
    });
    expect(parseSubmitAccepted({ status: 'accepted' })).toBeNull();
  });
  it('product_ids item_unavailable: hanya integer positif unik, dari error.* atau top-level', () => {
    expect(parseUnavailableIds({ error: { product_ids: [3, 'x', 3, 7, -1, 1.5] } })).toEqual([3, 7]);
    expect(parseUnavailableIds({ product_ids: [4] })).toEqual([4]);
    expect(parseUnavailableIds(null)).toEqual([]);
  });
});

describe('status, polling, pesan', () => {
  it('label status & status tak dikenal', () => {
    expect(submissionStatusLabel('pending_confirmation')).toBe('Menunggu konfirmasi pelayan');
    expect(submissionStatusLabel('served')).toBe('Sudah diantar');
    expect(submissionStatusLabel('aneh')).toBe('Status tidak dikenal');
    expect(submissionStatusLabel('constructor')).toBe('Status tidak dikenal');
  });
  it('jeda polling: 8 dtk bila ada kiriman berjalan, 30 dtk bila tidak, backoff sampai 2 menit', () => {
    expect(fnbPollDelayMs(['preparing'], 0)).toBe(8_000);
    expect(fnbPollDelayMs(['served', 'cancelled'], 0)).toBe(30_000);
    expect(fnbPollDelayMs([], 0)).toBe(30_000);
    expect(fnbPollDelayMs(['accepted'], 2)).toBe(32_000);
    expect(fnbPollDelayMs(['accepted'], 10)).toBe(120_000);
    expect(fnbPollDelayMs(['served'], 4)).toBe(120_000);
  });
  it('classifyFnbFailure', () => {
    expect(classifyFnbFailure('table_session_invalid', 'm')).toEqual({ kind: 'invalid' });
    expect(classifyFnbFailure('table_session_closed', 'm')).toEqual({ kind: 'closed' });
    expect(classifyFnbFailure('ordering_unavailable', 'm')).toEqual({ kind: 'unavailable' });
    expect(classifyFnbFailure('rate_limited', 'pesan')).toEqual({ kind: 'error', text: 'pesan' });
  });
  it('peta pesan: Bahasa Indonesia, kode asing → generik, tanpa kunci prototype', () => {
    for (const code of ['table_session_invalid', 'table_session_closed', 'ordering_unavailable', 'note_invalid', 'item_unavailable', 'session_limit_reached', 'points_already_claimed', 'account_deleting', 'points_unavailable', 'captcha_required', 'idempotency_conflict']) {
      expect(FNB_ERROR_MESSAGES[code]).toBeTruthy();
    }
    expect(fnbErrorMessage('ordering_unavailable')).toMatch(/pesan ke pelayan/);
    expect(fnbErrorMessage('kode_asing')).toMatch(/Terjadi kendala/);
    expect(fnbErrorMessage('toString')).toMatch(/Terjadi kendala/);
    expect(fnbErrorMessage(null)).toMatch(/Terjadi kendala/);
  });
});
