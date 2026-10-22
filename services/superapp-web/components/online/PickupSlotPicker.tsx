'use client';

/**
 * Jadwal ambil pre-order per grup keranjang (docs/04 §15.4). Hanya tampil untuk outlet `slot_enabled`.
 * Pilihan: "Secepatnya" (default, tanpa slot) atau tanggal + jam dari BFF (`/api/catalog/slots`, sisa kapasitas live).
 * Perubahan dikirim lewat `onChange(slot | null)` -> keranjang di-PUT ulang -> quote dihitung ulang. Kapasitas final
 * diperiksa saat checkout (`409 slot_unavailable`).
 */
import { useEffect, useMemo, useState } from 'react';
import { addDays, dateChipLabel, todayIn } from '@/lib/booking';
import { parseSlotDay, remainingLabel, slotLongLabel, slotRangeLabel, slotSelectable, type SlotDay } from '@/lib/slots';
import styles from '../booking/booking.module.css';

const DAYS = 7;

export function PickupSlotPicker({
  channel,
  outlet,
  value,
  disabled,
  onChange,
}: {
  channel: string;
  outlet: string;
  value: string | null;
  disabled: boolean;
  onChange: (slot: string | null) => void;
}) {
  const [scheduled, setScheduled] = useState(value !== null);
  // "Hari ini" diambil sekali saat mount (bukan saat render).
  const [today] = useState(() => todayIn('Asia/Jakarta', Date.now()));
  const [date, setDate] = useState(() => (value ? todayIn('Asia/Jakarta', Date.parse(value)) : today));
  // Hasil terakhir per kunci (kanal|gerai|tanggal); "memuat" = kunci aktif belum punya hasil (tanpa setState di effect).
  const [loaded, setLoaded] = useState<{ key: string; day: SlotDay | null; state: 'idle' | 'error' | 'off' } | null>(null);
  const dates = useMemo(() => Array.from({ length: DAYS }, (_, i) => addDays(today, i)), [today]);
  const id = `slot-${channel}`;
  const key = `${channel}|${outlet}|${date}`;
  const current = loaded?.key === key ? loaded : null;
  const state: 'idle' | 'loading' | 'error' | 'off' = current ? current.state : 'loading';
  const day = current?.day ?? null;

  useEffect(() => {
    if (!scheduled) return;
    let alive = true;
    const q = new URLSearchParams({ channel, outlet, date });
    const k = `${channel}|${outlet}|${date}`;
    fetch(`/api/catalog/slots?${q}`, { credentials: 'same-origin' })
      .then(async (res) => {
        const body: unknown = await res.json().catch(() => null);
        if (!alive) return;
        if (res.status === 503) return setLoaded({ key: k, day: null, state: 'off' });
        const d = res.ok ? parseSlotDay(body) : null;
        setLoaded({ key: k, day: d, state: d ? 'idle' : 'error' });
      })
      .catch(() => {
        if (alive) setLoaded({ key: k, day: null, state: 'error' });
      });
    return () => {
      alive = false;
    };
  }, [scheduled, channel, outlet, date]);

  return (
    <fieldset className={styles.slotBox} data-slot-picker={channel}>
      <legend>Waktu ambil</legend>
      <div className={styles.slotMode} role="radiogroup" aria-label="Waktu ambil">
        <button
          type="button"
          role="radio"
          aria-checked={!scheduled}
          className={styles.choice}
          disabled={disabled}
          onClick={() => {
            setScheduled(false);
            if (value !== null) onChange(null);
          }}
        >
          Secepatnya
        </button>
        <button type="button" role="radio" aria-checked={scheduled} className={styles.choice} disabled={disabled} onClick={() => setScheduled(true)}>
          Pre-order (pilih jadwal)
        </button>
      </div>
      {value && (
        <p className="small" style={{ margin: 0 }}>
          Jadwal terpilih: <strong>{slotLongLabel(value, day?.timezone)}</strong>
        </p>
      )}
      {scheduled && (
        <>
          <ul className={styles.scroller} role="radiogroup" aria-label="Tanggal ambil">
            {dates.map((d, i) => {
              const l = dateChipLabel(d);
              return (
                <li key={d}>
                  <button type="button" role="radio" aria-checked={date === d} className={`${styles.choice} ${styles.dateChoice}`} disabled={disabled} onClick={() => setDate(d)}>
                    <small>{i === 0 ? 'Hari ini' : l.weekday}</small>
                    <span>{l.day}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          {state === 'loading' ? (
            <p className="small muted" style={{ margin: 0 }}>
              Memuat jadwal…
            </p>
          ) : state === 'off' ? (
            <p className="small muted" style={{ margin: 0 }}>
              Pesan terjadwal sedang tidak tersedia. Pesanan akan disiapkan secepatnya.
            </p>
          ) : state === 'error' || !day ? (
            <p className="small muted" style={{ margin: 0 }}>
              Jadwal belum dapat dimuat. Coba tanggal lain.
            </p>
          ) : day.slots.length === 0 ? (
            <p className="small muted" style={{ margin: 0 }}>
              Tidak ada jadwal ambil di tanggal ini. Pilih tanggal lain.
            </p>
          ) : (
            <ul className={styles.slotGrid} role="radiogroup" aria-labelledby={`${id}-jam`}>
              <span id={`${id}-jam`} hidden>
                Jam ambil
              </span>
              {day.slots.map((s) => (
                <li key={s.start_at}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={value === s.start_at}
                    disabled={disabled || !slotSelectable(s)}
                    className={styles.choice}
                    onClick={() => onChange(s.start_at)}
                    title={remainingLabel(s.remaining)}
                    aria-label={`${slotRangeLabel(s, day.timezone)}, ${remainingLabel(s.remaining)}`}
                  >
                    {slotRangeLabel(s, day.timezone)}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </fieldset>
  );
}
