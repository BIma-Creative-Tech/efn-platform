'use client';

/**
 * Alur reservasi meja (docs/04 §15.7): gerai → tanggal → jumlah orang → jam → konfirmasi (login OTP bila belum) → status.
 *
 * - Ketersediaan dihitung Odoo lewat BFF; di sini hanya ditampilkan dan dipilih (slot `available: false` = nonaktif).
 * - Data tamu minimal (L-86): nama panggilan ≤ 40 + catatan ≤ 140, divalidasi (tanpa nomor HP), TIDAK disimpan di
 *   storage browser. Draft yang dibawa melewati login hanya gerai/tanggal/jumlah/jam (sessionStorage).
 * - Idempotency-Key per isi permintaan (sessionStorage): klik ulang/retry jaringan tidak membuat reservasi ganda.
 * - Kill switch BFF (503 `booking_unavailable`) -> pesan "belum tersedia", tanpa form.
 */
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BOOKING_ATTEMPT_KEY,
  BOOKING_DRAFT_KEY,
  BOOKING_RELOAD_CODES,
  bookingErrorMessage,
  bookingFingerprint,
  cleanText,
  dateChipLabel,
  dateOptions,
  formatClockIn,
  formatDateTimeIn,
  GUEST_INPUT_MESSAGES,
  GUEST_LABEL_MAX,
  NOTE_MAX,
  parseAvailability,
  parseBookingAttempt,
  parseBookingDraft,
  parseBookingOutlets,
  parseReservation,
  resolveBookingAttempt,
  serializeBookingDraft,
  todayIn,
  validateGuestInput,
  type Availability,
  type BookingOutlet,
  type CreateBookingBody,
  type Reservation,
} from '@/lib/booking';
import styles from './booking.module.css';

type Phase = { kind: 'loading' } | { kind: 'off' } | { kind: 'error' } | { kind: 'ready' } | { kind: 'done'; reservation: Reservation; tz: string };

const DEFAULT_PARTY = 2;
const DATE_COUNT = 14;

function newKey(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

function readSession(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
function writeSession(key: string, value: string | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, value);
  } catch {
    // storage diblokir -> alur tetap jalan tanpa draft
  }
}

export function BookingFlow({ loggedIn, brandName }: { loggedIn: boolean; brandName: string }) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
  const [outlets, setOutlets] = useState<BookingOutlet[]>([]);
  const [outlet, setOutletRaw] = useState<string | null>(null);
  const [date, setDateRaw] = useState<string>(() => todayIn('Asia/Jakarta', Date.now()));
  const [party, setPartyRaw] = useState(DEFAULT_PARTY);
  const [avail, setAvail] = useState<Availability | null>(null);
  const [availState, setAvailState] = useState<'idle' | 'loading' | 'error'>('idle');
  const [slot, setSlot] = useState<string | null>(null);
  const [guest, setGuest] = useState('');
  const [note, setNote] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Waktu acuan daftar tanggal: sekali saat mount (bukan saat render).
  const [mountMs] = useState(() => Date.now());
  const restored = useRef(false);
  // Ganti pilihan = pesan lama tidak relevan lagi (dihapus di handler, bukan di effect).
  const setOutlet = (v: string | null) => {
    setMsg(null);
    setOutletRaw(v);
  };
  const setDate = (v: string) => {
    setMsg(null);
    setDateRaw(v);
  };
  const setParty = (f: (p: number) => number) => {
    setMsg(null);
    setPartyRaw(f);
  };

  // 1) gerai + draft (kembali dari login)
  useEffect(() => {
    let alive = true;
    void (async () => {
      let res: Response;
      try {
        res = await fetch('/api/booking/outlets', { credentials: 'same-origin', cache: 'no-store' });
      } catch {
        if (alive) setPhase({ kind: 'error' });
        return;
      }
      const body: unknown = await res.json().catch(() => null);
      if (!alive) return;
      if (res.status === 503) return setPhase({ kind: 'off' });
      const list = res.ok ? parseBookingOutlets(body) : null;
      if (!list) return setPhase({ kind: 'error' });
      setOutlets(list);
      if (list.length === 0) return setPhase({ kind: 'off' });
      const draft = parseBookingDraft(readSession(BOOKING_DRAFT_KEY));
      const fromDraft = draft && list.some((o) => o.outlet_code === draft.outlet_code) ? draft : null;
      setOutletRaw(fromDraft?.outlet_code ?? (list.length === 1 ? list[0]!.outlet_code : null));
      if (fromDraft) {
        setDateRaw(fromDraft.date);
        setPartyRaw(fromDraft.party_size);
        setSlot(fromDraft.start_at);
        restored.current = true;
      }
      setPhase({ kind: 'ready' });
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 2) ketersediaan per (gerai, tanggal, jumlah)
  const loadAvailability = useCallback(async (o: string, d: string, p: number, keepSlot: boolean) => {
    const q = new URLSearchParams({ outlet: o, date: d, party: String(p) });
    // State "memuat" diset setelah microtask (bukan sinkron di dalam effect; aturan react-hooks).
    await Promise.resolve();
    setAvailState('loading');
    if (!keepSlot) setSlot(null);
    try {
      const res = await fetch(`/api/booking/availability?${q}`, { credentials: 'same-origin' });
      const body: unknown = await res.json().catch(() => null);
      if (res.status === 503) {
        setPhase({ kind: 'off' });
        return;
      }
      if (!res.ok) {
        const code = (body as { error?: { code?: string } } | null)?.error?.code;
        setAvail(null);
        setAvailState('error');
        setMsg(bookingErrorMessage(code));
        return;
      }
      const a = parseAvailability(body);
      setAvail(a);
      setAvailState(a ? 'idle' : 'error');
      if (a && keepSlot) setSlot((s) => (s && a.slots.some((x) => x.start_at === s && x.available) ? s : null));
    } catch {
      setAvail(null);
      setAvailState('error');
      setMsg(bookingErrorMessage('service_unavailable'));
    }
  }, []);

  useEffect(() => {
    if (phase.kind !== 'ready' || !outlet) return;
    const keep = restored.current;
    restored.current = false;
    void loadAvailability(outlet, date, party, keep);
  }, [phase.kind, outlet, date, party, loadAvailability]);

  const tz = avail?.timezone ?? 'Asia/Jakarta';
  const dates = useMemo(() => dateOptions(todayIn(tz, mountMs), Math.min(DATE_COUNT, avail?.horizon_days ?? DATE_COUNT)), [tz, mountMs, avail?.horizon_days]);
  const maxParty = avail?.max_party ?? 8;
  const minParty = avail?.min_party ?? 1;
  const guestErrors = validateGuestInput(guest, note);
  const outletName = outlets.find((o) => o.outlet_code === outlet)?.public_name ?? '';

  function saveDraft() {
    if (!outlet) return;
    writeSession(BOOKING_DRAFT_KEY, serializeBookingDraft({ outlet_code: outlet, date, party_size: party, start_at: slot }));
  }

  async function submit() {
    if (!outlet || !slot || busy || Object.keys(guestErrors).length > 0) return;
    if (!loggedIn) {
      saveDraft();
      router.push(`/masuk?next=${encodeURIComponent('/reservasi')}`);
      return;
    }
    const body: CreateBookingBody = { outlet_code: outlet, start_at: slot, party_size: party };
    const g = cleanText(guest);
    const n = cleanText(note);
    if (g) body.guest_label = g;
    if (n) body.note = n;
    const attempt = resolveBookingAttempt(parseBookingAttempt(readSession(BOOKING_ATTEMPT_KEY)), bookingFingerprint(body), newKey);
    writeSession(BOOKING_ATTEMPT_KEY, JSON.stringify(attempt));
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch('/api/booking/reservations', {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'content-type': 'application/json', accept: 'application/json', 'idempotency-key': attempt.key },
        body: JSON.stringify(body),
      });
      const data: unknown = await res.json().catch(() => null);
      if (res.status === 401) {
        saveDraft();
        router.push(`/masuk?next=${encodeURIComponent('/reservasi')}&expired=1`);
        return;
      }
      if (!res.ok) {
        const code = (data as { error?: { code?: string } } | null)?.error?.code ?? null;
        if (res.status === 503 && code === 'booking_unavailable') return setPhase({ kind: 'off' });
        setMsg(bookingErrorMessage(code));
        if (code && BOOKING_RELOAD_CODES.includes(code)) {
          writeSession(BOOKING_ATTEMPT_KEY, null);
          void loadAvailability(outlet, date, party, false);
        }
        return;
      }
      const r = parseReservation(data);
      if (!r) return setMsg(bookingErrorMessage(null));
      writeSession(BOOKING_ATTEMPT_KEY, null);
      writeSession(BOOKING_DRAFT_KEY, null);
      setGuest('');
      setNote('');
      setPhase({ kind: 'done', reservation: r, tz });
    } catch {
      setMsg(bookingErrorMessage('service_unavailable'));
    } finally {
      setBusy(false);
    }
  }

  if (phase.kind === 'loading') return <p className="muted">Memuat gerai…</p>;
  if (phase.kind === 'off' || phase.kind === 'error') {
    return (
      <section className="card stack" aria-live="polite">
        <h2 style={{ margin: 0 }}>{phase.kind === 'off' ? 'Reservasi belum tersedia' : 'Reservasi belum dapat dimuat'}</h2>
        <p style={{ margin: 0 }}>
          {phase.kind === 'off'
            ? `Reservasi meja online ${brandName} belum dibuka. Anda tetap bisa datang langsung ke gerai atau memesan untuk diambil.`
            : 'Terjadi kendala saat memuat data. Silakan muat ulang halaman.'}
        </p>
        <p style={{ margin: 0 }}>
          <Link href="/belanja" className="btn">
            Pesan untuk diambil
          </Link>
        </p>
      </section>
    );
  }
  if (phase.kind === 'done') {
    const r = phase.reservation;
    return (
      <section className="card stack" aria-live="polite" data-booking-done="">
        <h2 style={{ margin: 0 }}>{r.status === 'confirmed' ? 'Reservasi dikonfirmasi' : 'Permintaan reservasi terkirim'}</h2>
        <p style={{ margin: 0 }}>
          {r.status === 'confirmed'
            ? 'Meja Anda sudah disiapkan. Tunjukkan halaman ini kepada staf saat datang.'
            : 'Gerai akan mengonfirmasi reservasi Anda. Kami kirim pemberitahuan saat statusnya berubah.'}
        </p>
        <dl className={styles.summary}>
          <dt>Gerai</dt>
          <dd>{r.outlet_name || outletName}</dd>
          <dt>Waktu</dt>
          <dd>{formatDateTimeIn(r.start_at, phase.tz)}</dd>
          <dt>Jumlah tamu</dt>
          <dd>{r.party_size} orang</dd>
          {r.table_label && (
            <>
              <dt>Meja</dt>
              <dd>{r.table_label}</dd>
            </>
          )}
        </dl>
        <p style={{ margin: 0, display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <Link href="/reservasi/saya" className="btn">
            Lihat reservasi saya
          </Link>
          <Link href="/belanja" className="btn btn-secondary">
            Pesan menu untuk diambil
          </Link>
        </p>
      </section>
    );
  }

  return (
    <div className={styles.flow} data-booking-flow="">
      <div aria-live="polite" role="status">
        {msg && <p className="alert alert-error">{msg}</p>}
      </div>
      <ol className={styles.steps}>
        <li className={styles.step}>
          <h2 className={styles.stepTitle} id="langkah-gerai">
            <span className={styles.stepNum}>1</span> Pilih gerai
          </h2>
          <ul className={styles.chips} role="radiogroup" aria-labelledby="langkah-gerai">
            {outlets.map((o) => (
              <li key={o.outlet_code}>
                <button type="button" role="radio" aria-checked={outlet === o.outlet_code} className={styles.choice} onClick={() => setOutlet(o.outlet_code)}>
                  {o.public_name}
                </button>
              </li>
            ))}
          </ul>
        </li>

        <li className={styles.step}>
          <h2 className={styles.stepTitle} id="langkah-tanggal">
            <span className={styles.stepNum}>2</span> Tanggal &amp; jumlah tamu
          </h2>
          <ul className={styles.scroller} role="radiogroup" aria-labelledby="langkah-tanggal">
            {dates.map((d, i) => {
              const l = dateChipLabel(d);
              return (
                <li key={d}>
                  <button type="button" role="radio" aria-checked={date === d} className={`${styles.choice} ${styles.dateChoice}`} onClick={() => setDate(d)}>
                    <small>{i === 0 ? 'Hari ini' : l.weekday}</small>
                    <span>{l.day}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div className={styles.party} style={{ marginTop: '0.75rem' }}>
            <span id="label-tamu">Jumlah tamu</span>
            <button
              type="button"
              className={styles.choice}
              aria-label="Kurangi jumlah tamu"
              disabled={party <= minParty}
              onClick={() => setParty((p) => Math.max(minParty, p - 1))}
            >
              −
            </button>
            <output aria-labelledby="label-tamu" aria-live="polite">
              {party}
            </output>
            <button
              type="button"
              className={styles.choice}
              aria-label="Tambah jumlah tamu"
              disabled={party >= maxParty}
              onClick={() => setParty((p) => Math.min(maxParty, p + 1))}
            >
              +
            </button>
          </div>
          {party >= maxParty && <p className="small muted" style={{ margin: '0.5rem 0 0' }}>Untuk rombongan lebih dari {maxParty} orang, hubungi gerai.</p>}
        </li>

        <li className={styles.step}>
          <h2 className={styles.stepTitle} id="langkah-jam">
            <span className={styles.stepNum}>3</span> Pilih jam
          </h2>
          {!outlet ? (
            <p className="small muted" style={{ margin: 0 }}>
              Pilih gerai lebih dulu.
            </p>
          ) : availState === 'loading' ? (
            <p className="small muted" style={{ margin: 0 }}>
              Memeriksa meja tersedia…
            </p>
          ) : !avail || avail.slots.length === 0 ? (
            <p className="small muted" style={{ margin: 0 }}>
              Tidak ada jam reservasi untuk tanggal ini. Coba tanggal lain.
            </p>
          ) : (
            <>
              <ul className={styles.slotGrid} role="radiogroup" aria-labelledby="langkah-jam">
                {avail.slots.map((s) => (
                  <li key={s.start_at}>
                    <button
                      type="button"
                      role="radio"
                      aria-checked={slot === s.start_at}
                      disabled={!s.available}
                      className={styles.choice}
                      onClick={() => setSlot(s.start_at)}
                      aria-label={`${formatClockIn(s.start_at, tz)}${s.available ? '' : ', penuh'}`}
                    >
                      {formatClockIn(s.start_at, tz)}
                    </button>
                  </li>
                ))}
              </ul>
              <p className="small muted" style={{ margin: '0.5rem 0 0' }}>
                Durasi reservasi ± {avail.duration_minutes} menit. Jam yang dicoret sudah penuh.
              </p>
            </>
          )}
        </li>

        <li className={styles.step}>
          <h2 className={styles.stepTitle}>
            <span className={styles.stepNum}>4</span> Konfirmasi
          </h2>
          <div className="field">
            <label htmlFor="bk-nama">Nama panggilan (opsional)</label>
            <input
              id="bk-nama"
              type="text"
              autoComplete="nickname"
              maxLength={GUEST_LABEL_MAX + 10}
              value={guest}
              onChange={(e) => setGuest(e.target.value)}
              aria-invalid={Boolean(guestErrors.guest_label)}
              aria-describedby={guestErrors.guest_label ? 'bk-nama-err' : undefined}
            />
            {guestErrors.guest_label && (
              <span id="bk-nama-err" className="small" role="alert">
                {GUEST_INPUT_MESSAGES[guestErrors.guest_label]}
              </span>
            )}
          </div>
          <div className="field">
            <label htmlFor="bk-catatan">Catatan untuk gerai (opsional)</label>
            <input
              id="bk-catatan"
              type="text"
              maxLength={NOTE_MAX + 10}
              placeholder="mis. kursi bayi, dekat jendela"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              aria-invalid={Boolean(guestErrors.note)}
              aria-describedby={guestErrors.note ? 'bk-catatan-err' : 'bk-catatan-info'}
            />
            {guestErrors.note ? (
              <span id="bk-catatan-err" className="small" role="alert">
                {GUEST_INPUT_MESSAGES[guestErrors.note]}
              </span>
            ) : (
              <span id="bk-catatan-info" className="small muted">
                Jangan tulis nomor HP atau email; gerai menghubungi Anda lewat aplikasi.
              </span>
            )}
          </div>
          {slot && outlet && (
            <dl className={styles.summary}>
              <dt>Gerai</dt>
              <dd>{outletName}</dd>
              <dt>Waktu</dt>
              <dd>{formatDateTimeIn(slot, tz)}</dd>
              <dt>Tamu</dt>
              <dd>{party} orang</dd>
            </dl>
          )}
          <button type="button" className="btn" disabled={!outlet || !slot || busy || Object.keys(guestErrors).length > 0} onClick={() => void submit()}>
            {busy ? 'Mengirim…' : loggedIn ? 'Reservasi sekarang' : 'Masuk & reservasi'}
          </button>
          {!loggedIn && (
            <p className="small muted" style={{ margin: '0.5rem 0 0' }}>
              Anda akan diminta masuk dengan nomor HP. Pilihan gerai, tanggal, dan jam tetap tersimpan.
            </p>
          )}
        </li>
      </ol>
    </div>
  );
}
