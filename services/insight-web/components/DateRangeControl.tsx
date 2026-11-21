'use client';
/**
 * Tombol rentang tanggal dengan popover: preset (diterapkan langsung) + input tanggal kustom (diterapkan setelah kedua
 * tanggal sah, debounce ~400 ms). Dipakai toolbar ESB (FilterBar) dan toolbar Supermarket (SmFilterForm). Popover = <details>
 * (keyboard dan pembaca layar bekerja tanpa pustaka); Esc / klik di luar menutupnya.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { detectPreset, isValidRange, presetRange, PRESETS } from '@/lib/date-presets';
import { fmtRange } from '@/lib/format';
import { Icon } from './ui';

const DEBOUNCE_MS = 400;

export function DateRangeControl({
  from,
  to,
  yesterday,
  onChange,
}: {
  from: string;
  to: string;
  yesterday: string;
  /** Dipanggil saat rentang berubah dan sah. */
  onChange: (range: { from: string; to: string }) => void;
}): React.ReactNode {
  const uid = useId();
  const box = useRef<HTMLDetailsElement>(null);
  const timer = useRef<number | null>(null);
  const [df, setDf] = useState(from);
  const [dt, setDt] = useState(to);
  const [seen, setSeen] = useState(`${from}|${to}`);
  // Sinkron dari luar (URL berubah, mis. Kembali/Maju atau hapus chip): setState saat render, pola resmi React.
  if (seen !== `${from}|${to}`) {
    setSeen(`${from}|${to}`);
    setDf(from);
    setDt(to);
  }

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  useEffect(() => {
    const onDown = (e: PointerEvent): void => {
      const el = box.current;
      if (el?.open && e.target instanceof Node && !el.contains(e.target)) el.open = false;
    };
    const onKey = (e: KeyboardEvent): void => {
      const el = box.current;
      if (e.key === 'Escape' && el?.open) {
        el.open = false;
        el.querySelector('summary')?.focus();
      }
    };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, []);

  const preset = detectPreset(from, to, yesterday);
  const presetLabel = PRESETS.find((p) => p.id === preset)?.label;

  const custom = (nf: string, nt: string): void => {
    setDf(nf);
    setDt(nt);
    if (timer.current !== null) window.clearTimeout(timer.current);
    if (!isValidRange(nf, nt, yesterday)) return;
    timer.current = window.setTimeout(() => onChange({ from: nf, to: nt }), DEBOUNCE_MS);
  };

  const invalid = (df !== '' || dt !== '') && !isValidRange(df, dt, yesterday);

  return (
    <details className="dr" ref={box}>
      <summary className="fb-btn" aria-label={`Rentang tanggal: ${fmtRange(from, to)}${presetLabel ? ` (${presetLabel})` : ''}. Buka untuk mengubah.`}>
        <Icon name="calendar" size={16} />
        <span className="fb-btn-text">
          <span className="fb-btn-label">Rentang tanggal</span>
          <span className="fb-btn-value">{fmtRange(from, to)}</span>
        </span>
        {presetLabel ? <span className="fb-btn-sub">{presetLabel}</span> : null}
        <Icon name="chevron-down" size={14} />
      </summary>
      <div className="dr-panel">
        <div role="group" aria-label="Pilihan cepat rentang" className="dr-presets">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              className="btn btn-sm"
              aria-pressed={preset === p.id}
              onClick={() => {
                if (timer.current !== null) window.clearTimeout(timer.current);
                onChange(presetRange(p.id, yesterday));
                if (box.current) box.current.open = false;
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="dr-custom">
          <div>
            <label htmlFor={`${uid}-from`}>Dari tanggal</label>
            <input id={`${uid}-from`} type="date" className="input" value={df} max={yesterday} onChange={(e) => custom(e.target.value, dt)} />
          </div>
          <div>
            <label htmlFor={`${uid}-to`}>Sampai tanggal</label>
            <input id={`${uid}-to`} type="date" className="input" value={dt} max={yesterday} onChange={(e) => custom(df, e.target.value)} />
          </div>
        </div>
        <p className={`small ${invalid ? 'dr-error' : 'muted'}`} role={invalid ? 'alert' : undefined}>
          {invalid
            ? 'Rentang tidak valid: tanggal awal harus sebelum tanggal akhir, tidak melewati kemarin, dan paling lama 366 hari.'
            : 'Rentang kustom diterapkan otomatis setelah kedua tanggal terisi. Data harian lengkap sampai kemarin.'}
        </p>
      </div>
    </details>
  );
}
