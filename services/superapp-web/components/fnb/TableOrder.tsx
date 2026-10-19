'use client';

/**
 * Pesan dari meja (docs/04 §14.A; L-70: satu kiriman = satu order draft; sesi meja bisa punya banyak kiriman).
 *
 * Token sesi meja: dibaca dari fragment `#t=` SEKALI → memori modul (lib/table-token.ts) → fragment dihapus dari address bar.
 * Tidak pernah ke storage/log/analytics/query. Semua panggilan lewat Route Handler `/api/fnb/*` (header `X-Table-Session`).
 *
 * Alur: muat sesi + menu → pilih menu (atribut, catatan, jumlah) → keranjang meja (memori) → "Kirim" (Idempotency-Key per
 * percobaan; isi sama = key sama) → daftar kiriman dengan status (polling 8 dtk, jeda saat tab tersembunyi) → klaim poin
 * opsional (butuh login OTP). Tamu tanpa login boleh memesan. Pembayaran di kasir; harga di layar = indikatif (belum pajak).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AltchaWidget } from '@/components/AltchaWidget';
import { formatRupiah } from '@/lib/format';
import {
  addTableLine,
  buildSubmitBody,
  classifyFnbFailure,
  fnbErrorMessage,
  fnbPollDelayMs,
  formatClock,
  GENERIC_FNB_ERROR,
  itemUnavailableFeedback,
  newIdempotencyKey,
  parseFnbMenu,
  parseFnbOrders,
  parseFnbSession,
  parseSubmitAccepted,
  parseUnavailableIds,
  resolveAttemptKey,
  setTableLineQty,
  submissionStatusLabel,
  submitAcceptedMessage,
  submitFingerprint,
  tableEstimateExcl,
  tableItemCount,
  type Attempt,
  type FnbMenu,
  type FnbSession,
  type FnbSubmission,
  type TableAddInput,
  type TableLine,
} from '@/lib/fnb';
import { clearAttempt, fnbApi, readAttempt, writeAttempt } from '@/lib/fnb-client';
import { captureTableToken, clearTableToken, getTableToken } from '@/lib/table-token';
import { MenuItem } from './MenuItem';
import { PointsClaim } from './PointsClaim';
import styles from './fnb.module.css';

type Phase =
  | { kind: 'init' }
  | { kind: 'no_token' }
  | { kind: 'invalid' }
  | { kind: 'closed' }
  | { kind: 'unavailable' }
  | { kind: 'error'; text: string }
  | { kind: 'ready' };

type Msg = { kind: 'success' | 'error'; text: string } | null;

type Loaded = { ok: true; session: FnbSession; menu: FnbMenu } | { ok: false; phase: Phase };

async function loadAll(): Promise<Loaded> {
  const [s, m] = await Promise.all([fnbApi<unknown>('/api/fnb/session'), fnbApi<unknown>('/api/fnb/menu')]);
  const failed = !s.ok ? s : !m.ok ? m : null;
  if (failed) {
    const c = classifyFnbFailure(failed.code, failed.message);
    if (c.kind === 'invalid') clearTableToken();
    return { ok: false, phase: c };
  }
  const session = s.ok ? parseFnbSession(s.data) : null;
  const menu = m.ok ? parseFnbMenu(m.data) : null;
  if (!session || !menu) return { ok: false, phase: { kind: 'error', text: GENERIC_FNB_ERROR } };
  return { ok: true, session, menu };
}

export function TableOrder({ loggedIn }: { loggedIn: boolean }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'init' });
  const [session, setSession] = useState<FnbSession | null>(null);
  const [menu, setMenu] = useState<FnbMenu | null>(null);
  const [lines, setLines] = useState<TableLine[]>([]);
  const [subs, setSubs] = useState<FnbSubmission[]>([]);
  const [category, setCategory] = useState<number | 'all'>('all');
  const [openId, setOpenId] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [needCaptcha, setNeedCaptcha] = useState(false);
  const [altcha, setAltcha] = useState<string | null>(null);
  const [altchaKey, setAltchaKey] = useState(0);
  const subsRef = useRef<FnbSubmission[]>([]);

  useEffect(() => {
    subsRef.current = subs;
  }, [subs]);

  // Muat sesi + menu. Dijalankan ulang bila status login berubah (klaim poin butuh `claimed_by_me`).
  useEffect(() => {
    // Pindahkan token dari fragment ke memori + bersihkan URL SEBELUM apa pun (idempoten; aman untuk StrictMode).
    captureTableToken(window.location, window.history, window.location.search);
    let active = true;
    void (async () => {
      await Promise.resolve(); // setState tidak boleh sinkron di badan effect
      if (!getTableToken()) {
        if (active) setPhase({ kind: 'no_token' });
        return;
      }
      const r = await loadAll();
      if (!active) return;
      if (r.ok) {
        setSession(r.session);
        setMenu(r.menu);
        setPhase({ kind: 'ready' });
      } else {
        setPhase(r.phase);
      }
    })();
    return () => {
      active = false;
    };
  }, [loggedIn]);

  const reloadSession = useCallback(async () => {
    const r = await fnbApi<unknown>('/api/fnb/session');
    if (r.ok) {
      const s = parseFnbSession(r.data);
      if (s) setSession(s);
    }
  }, []);

  /** Ambil daftar kiriman. Mengembalikan true bila berhasil (untuk backoff polling). */
  const refreshOrders = useCallback(async (): Promise<boolean | 'stop'> => {
    const r = await fnbApi<unknown>('/api/fnb/orders');
    if (r.ok) {
      const items = parseFnbOrders(r.data);
      if (!items) return false;
      setSubs(items);
      return true;
    }
    const c = classifyFnbFailure(r.code, r.message);
    if (c.kind === 'invalid') {
      clearTableToken();
      setPhase(c);
      return 'stop';
    }
    if (c.kind === 'closed') {
      setPhase(c);
      return 'stop';
    }
    return false;
  }, []);

  // Polling status kiriman (tab tersembunyi dilewati; backoff bila gagal; berhenti bila sesi ditutup/tidak valid).
  useEffect(() => {
    if (phase.kind !== 'ready') return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async (failures: number) => {
      if (!active) return;
      let next = failures;
      if (document.visibilityState === 'visible') {
        const ok = await refreshOrders();
        if (!active || ok === 'stop') return;
        next = ok ? 0 : failures + 1;
      }
      timer = setTimeout(() => void tick(next), fnbPollDelayMs(subsRef.current.map((s) => s.status), next));
    };
    void tick(0);
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [phase.kind, refreshOrders]);

  const onAltcha = useCallback((p: string | null) => setAltcha(p), []);

  function addLine(input: TableAddInput): string | null {
    if (!session) return GENERIC_FNB_ERROR;
    const r = addTableLine(lines, input, session.limits);
    if (!r.ok) {
      return r.reason === 'too_many_lines'
        ? `Maksimal ${session.limits.max_lines} jenis menu per kiriman. Kirim dulu pesanan ini, lalu tambah lagi.`
        : 'Menu tidak dapat ditambahkan.';
    }
    setLines(r.lines);
    setMsg({ kind: 'success', text: `${input.product.name} ditambahkan ke pesanan.` });
    return null;
  }

  const unavailable = new Set((menu?.products ?? []).filter((p) => !p.available).map((p) => p.product_id));
  const blocked = lines.some((l) => unavailable.has(l.product_id));

  async function submit() {
    if (sending || lines.length === 0 || !session) return;
    if (blocked) {
      setMsg({ kind: 'error', text: 'Hapus menu yang tidak tersedia dari pesanan, lalu kirim lagi.' });
      return;
    }
    if (needCaptcha && !altcha) {
      setMsg({ kind: 'error', text: 'Selesaikan verifikasi keamanan ("Saya bukan robot") terlebih dahulu.' });
      return;
    }
    setSending(true);
    setMsg(null);
    try {
      let attempt: Attempt;
      try {
        attempt = resolveAttemptKey(readAttempt(), submitFingerprint(lines), newIdempotencyKey);
      } catch {
        setMsg({ kind: 'error', text: 'Peramban ini tidak mendukung pengiriman aman. Silakan pesan ke pelayan.' });
        return;
      }
      writeAttempt(attempt);
      const usedCaptcha = needCaptcha;
      const r = await fnbApi<unknown>('/api/fnb/orders', {
        method: 'POST',
        body: JSON.stringify(buildSubmitBody(lines, usedCaptcha ? altcha : null)),
        headers: { 'idempotency-key': attempt.key },
      });
      if (usedCaptcha) {
        // Challenge ALTCHA sekali pakai: apa pun hasilnya, minta yang baru.
        setAltcha(null);
        setAltchaKey((k) => k + 1);
      }
      if (r.ok) {
        const accepted = parseSubmitAccepted(r.data);
        clearAttempt();
        setLines([]);
        setOpenId(null);
        setMsg({ kind: 'success', text: submitAcceptedMessage(accepted?.status ?? 'accepted') });
        if (accepted) {
          setSubs((prev) =>
            prev.some((s) => s.submission_id === accepted.submission_id)
              ? prev
              : [{ submission_id: accepted.submission_id, created_at: new Date().toISOString(), status: accepted.status, lines: accepted.lines }, ...prev],
          );
        }
        void refreshOrders();
        void reloadSession();
        return;
      }
      if (r.status === 0) {
        // Jaringan putus: key & isi tersimpan -> menekan "Kirim" lagi aman (tidak ada pesanan ganda).
        setMsg({ kind: 'error', text: 'Koneksi terputus. Pesanan belum pasti terkirim. Tekan Kirim lagi (tidak akan terkirim dua kali), atau cek daftar pesanan terkirim.' });
        return;
      }
      const c = classifyFnbFailure(r.code, r.message);
      if (c.kind === 'invalid') {
        clearTableToken();
        setPhase(c);
        return;
      }
      if (c.kind === 'closed') {
        setPhase(c);
        return;
      }
      if (r.code === 'captcha_required') {
        setNeedCaptcha(true);
        setAltchaKey((k) => k + 1);
      }
      if (r.code === 'item_unavailable') {
        // Tandai (menu.available=false -> baris keranjang diberi "Tidak tersedia") hanya id yang ada di keranjang.
        const fb = itemUnavailableFeedback(parseUnavailableIds({ error: r.details }), lines);
        if (fb.marked.length > 0) setMenu((m) => (m ? { ...m, products: m.products.map((p) => (fb.marked.includes(p.product_id) ? { ...p, available: false } : p)) } : m));
        setMsg({ kind: 'error', text: fb.text });
        return;
      }
      if (r.code === 'idempotency_conflict') clearAttempt();
      setMsg({ kind: 'error', text: fnbErrorMessage(r.code) });
    } finally {
      setSending(false);
      // Hasil kirim ada di bagian atas halaman; bilah kirim menempel di bawah.
      setTimeout(() => document.getElementById('meja-status')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 0);
    }
  }

  // ---------- tampilan per keadaan ----------

  if (phase.kind === 'init') {
    return (
      <p role="status" aria-live="polite">
        Memuat menu…
      </p>
    );
  }
  if (phase.kind === 'no_token' || phase.kind === 'invalid') {
    return (
      <section className={`card stack ${styles.page}`} aria-labelledby="meja-judul">
        <h1 id="meja-judul" style={{ marginTop: 0 }}>
          Pindai QR di meja Anda
        </h1>
        <p style={{ margin: 0 }}>
          {phase.kind === 'invalid'
            ? fnbErrorMessage('table_session_invalid')
            : 'Halaman ini dibuka dari kode QR di meja. Pindai ulang QR pada slip dari pelayan untuk mulai memesan.'}
        </p>
        <p className="small muted" style={{ margin: 0 }}>
          Demi keamanan, kode meja tidak disimpan di ponsel. Bila halaman dimuat ulang, pindai QR lagi atau minta bantuan pelayan.
        </p>
      </section>
    );
  }
  if (phase.kind === 'unavailable') {
    return (
      <section className={`card stack ${styles.page}`}>
        <h1 style={{ marginTop: 0 }}>Pemesanan dari ponsel belum tersedia</h1>
        <p style={{ margin: 0 }}>{fnbErrorMessage('ordering_unavailable')}</p>
      </section>
    );
  }
  if (phase.kind === 'error') {
    return (
      <section className={`card stack ${styles.page}`}>
        <p className="alert alert-error" role="alert" style={{ margin: 0 }}>
          {phase.text}
        </p>
        <p style={{ margin: 0 }}>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setPhase({ kind: 'init' });
              void loadAll().then((r) => {
                if (r.ok) {
                  setSession(r.session);
                  setMenu(r.menu);
                  setPhase({ kind: 'ready' });
                } else {
                  setPhase(r.phase);
                }
              });
            }}
          >
            Coba lagi
          </button>
        </p>
      </section>
    );
  }

  const closed = phase.kind === 'closed';
  const shown = (menu?.products ?? []).filter((p) => category === 'all' || p.category_id === category);
  const maxQty = session?.limits.max_line_qty ?? 20;
  const subsAsc = [...subs].reverse();

  return (
    <div className={`stack ${styles.page}`}>
      <header className={styles.head}>
        <h1 id="meja-judul">{session?.outlet_name || 'Pesan dari meja'}</h1>
        <p className="small muted" style={{ margin: 0 }}>
          Meja: <strong>{session?.table_label}</strong> · Anda memesan sebagai tamu; pembayaran di kasir.
        </p>
      </header>

      <div id="meja-status" role="status" aria-live="polite">
        {msg && <p className={`alert ${msg.kind === 'success' ? 'alert-success' : 'alert-error'}`}>{msg.text}</p>}
      </div>

      {closed && (
        <p className="notice" role="note">
          {fnbErrorMessage('table_session_closed')} Terima kasih sudah memesan!
        </p>
      )}

      {!closed && menu && session && (
        <>
          <p className="notice" role="note">
            Harga di layar <strong>belum termasuk pajak &amp; layanan</strong>; tagihan akhir dihitung dan dibayar di kasir.{' '}
            {session.confirm_mode === 'waiter' ? 'Pesanan dikonfirmasi pelayan sebelum masuk dapur.' : 'Pesanan langsung diteruskan ke dapur.'}
            {session.limits.submissions_left !== null && session.limits.submissions_left <= 3 ? ` Sisa kiriman untuk meja ini: ${session.limits.submissions_left}.` : ''}
          </p>

          {menu.categories.length > 0 && (
            <nav aria-label="Kategori menu">
              <ul className={styles.chips}>
                <li>
                  <button type="button" className={styles.chip} aria-pressed={category === 'all'} onClick={() => setCategory('all')}>
                    Semua
                  </button>
                </li>
                {menu.categories.map((c) => (
                  <li key={c.category_id}>
                    <button type="button" className={styles.chip} aria-pressed={category === c.category_id} onClick={() => setCategory(c.category_id)}>
                      {c.name}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
          )}

          <section aria-labelledby="menu-judul" className="stack">
            <h2 id="menu-judul" className="visually-hidden">
              Menu
            </h2>
            {shown.length === 0 ? (
              <p className="card">Belum ada menu di kategori ini.</p>
            ) : (
              <ul className={styles.menu}>
                {shown.map((p) => (
                  <li key={p.product_id}>
                    <MenuItem
                      product={p}
                      open={openId === p.product_id}
                      maxQty={maxQty}
                      onToggle={() => setOpenId((cur) => (cur === p.product_id ? null : p.product_id))}
                      onAdd={addLine}
                    />
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card stack" aria-labelledby="keranjang-judul">
            <h2 id="keranjang-judul" style={{ margin: 0, fontSize: '1.0625rem' }}>
              Pesanan Anda (belum dikirim)
            </h2>
            {lines.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                Belum ada menu dipilih.
              </p>
            ) : (
              <>
                <ul className={styles.lines}>
                  {lines.map((l) => (
                    <li key={l.key} className={styles.line}>
                      <span>
                        <strong>{l.name}</strong>
                        {l.options_text && (
                          <span className="small muted">
                            <br />
                            {l.options_text}
                          </span>
                        )}
                        {l.note && (
                          <span className="small muted">
                            <br />
                            Catatan: {l.note}
                          </span>
                        )}
                        {unavailable.has(l.product_id) && (
                          <span className={styles.lineIssue}>
                            <br />
                            Tidak tersedia — hapus dari pesanan
                          </span>
                        )}
                      </span>
                      <span style={{ fontWeight: 700 }}>{formatRupiah(l.unit_excl * l.qty)}</span>
                      <span className={styles.lineControls}>
                        <span className={styles.qty} role="group" aria-label={`Jumlah ${l.name}`}>
                          <button type="button" disabled={sending} aria-label={l.qty <= 1 ? `Hapus ${l.name}` : `Kurangi ${l.name}`} onClick={() => setLines((ls) => setTableLineQty(ls, l.key, l.qty - 1, maxQty))}>
                            −
                          </button>
                          <span aria-live="polite">{l.qty}</span>
                          <button type="button" disabled={sending || l.qty >= maxQty} aria-label={`Tambah ${l.name}`} onClick={() => setLines((ls) => setTableLineQty(ls, l.key, l.qty + 1, maxQty))}>
                            +
                          </button>
                        </span>
                        <button type="button" className="btn btn-secondary btn-small" disabled={sending} onClick={() => setLines((ls) => setTableLineQty(ls, l.key, 0, maxQty))}>
                          Hapus
                        </button>
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="small muted" style={{ margin: 0 }}>
                  Perkiraan {tableItemCount(lines)} item: {formatRupiah(tableEstimateExcl(lines))} sebelum pajak &amp; layanan.
                </p>
                {needCaptcha && (
                  <div>
                    <p className="hint" style={{ margin: '0 0 0.5rem' }}>
                      Verifikasi keamanan diperlukan untuk mengirim pesanan.
                    </p>
                    <AltchaWidget key={altchaKey} onPayload={onAltcha} />
                  </div>
                )}
              </>
            )}
          </section>
        </>
      )}

      {subs.length > 0 && (
        <section aria-labelledby="terkirim-judul" className="stack">
          <h2 id="terkirim-judul" style={{ margin: 0, fontSize: '1.0625rem' }}>
            Pesanan terkirim
          </h2>
          <p className="small muted" style={{ margin: 0 }}>
            Meja ini bisa mengirim beberapa kali; setiap kiriman punya status sendiri dan dibayar di kasir. Daftar diperbarui otomatis.
          </p>
          <ul className={styles.subs} aria-live="polite" aria-relevant="text">
            {subs.map((s) => {
              const n = subsAsc.findIndex((x) => x.submission_id === s.submission_id) + 1;
              return (
                <li key={s.submission_id} className={styles.sub}>
                  <div className={styles.subHead}>
                    <strong>
                      Kiriman #{n}
                      {s.created_at ? ` · ${formatClock(s.created_at)}` : ''}
                    </strong>
                    <span className="badge">{submissionStatusLabel(s.status)}</span>
                  </div>
                  {s.lines.length > 0 && (
                    <ul className={styles.subLines}>
                      {s.lines.map((l, i) => (
                        <li key={`${l.name}-${i}`}>
                          {l.qty} × {l.name}
                          {l.attributes_text && <span className="small muted"> ({l.attributes_text})</span>}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {session && !closed && <PointsClaim session={session} loggedIn={loggedIn} onChanged={() => void reloadSession()} />}

      {!closed && lines.length > 0 && (
        <div className={styles.sendBar}>
          <span className="small">
            {tableItemCount(lines)} item · ± {formatRupiah(tableEstimateExcl(lines))}
          </span>
          <button type="button" className="btn" disabled={sending || blocked} onClick={() => void submit()}>
            {sending ? 'Mengirim…' : session?.confirm_mode === 'waiter' ? 'Kirim ke pelayan' : 'Kirim ke dapur'}
          </button>
        </div>
      )}
    </div>
  );
}
