'use client';
/**
 * Pilihan ganda dengan pencarian (combobox sederhana berbasis <details> + kotak centang): dapat dioperasikan
 * dengan keyboard dan pembaca layar tanpa pustaka UI. Perubahan baru diterapkan saat tombol "Terapkan" FilterBar ditekan.
 */
import { useId, useMemo, useState } from 'react';

export interface Option {
  value: string;
  label: string;
  group?: string;
}

const RENDER_LIMIT = 200;

export function MultiSelect({
  label,
  options,
  selected,
  onChange,
  max,
  disabledReason,
  summaryNoun,
}: {
  label: string;
  options: Option[];
  selected: string[];
  onChange: (next: string[]) => void;
  max: number;
  disabledReason?: string;
  summaryNoun: string;
}): React.ReactNode {
  const uid = useId();
  const [q, setQ] = useState('');
  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = needle ? options.filter((o) => o.label.toLowerCase().includes(needle)) : options;
    return list.slice(0, RENDER_LIMIT);
  }, [options, q]);
  const groups = useMemo(() => {
    const m = new Map<string, Option[]>();
    for (const o of matches) {
      const g = o.group ?? '';
      m.set(g, [...(m.get(g) ?? []), o]);
    }
    return Array.from(m.entries());
  }, [matches]);

  if (disabledReason) {
    return (
      <div className="ms ms-disabled">
        <span className="ms-label" id={`${uid}-l`}>
          {label}
        </span>
        <button type="button" className="ms-summary" aria-disabled="true" aria-describedby={`${uid}-d`} aria-labelledby={`${uid}-l`}>
          Tidak berlaku
        </button>
        <span id={`${uid}-d`} className="sr-only">
          {disabledReason}
        </span>
      </div>
    );
  }

  const set = new Set(selected);
  const toggle = (v: string, on: boolean): void => {
    if (on) onChange(set.has(v) ? selected : [...selected, v].slice(0, max));
    else onChange(selected.filter((x) => x !== v));
  };

  return (
    <details className="ms">
      <summary className="ms-summary">
        <span className="ms-label">{label}</span>
        <span>{selected.length === 0 ? `Semua ${summaryNoun}` : `${selected.length} ${summaryNoun}`}</span>
      </summary>
      <div className="ms-panel">
        <label className="sr-only" htmlFor={`${uid}-q`}>
          Cari {label}
        </label>
        <input id={`${uid}-q`} type="search" className="input" placeholder="Cari..." value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="ms-list">
          {groups.map(([g, opts]) => (
            <fieldset key={g} className="ms-group">
              {g ? <legend>{g}</legend> : <legend className="sr-only">{label}</legend>}
              {opts.map((o) => {
                const on = set.has(o.value);
                return (
                  <label key={o.value} className="ms-item">
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={!on && selected.length >= max}
                      onChange={(e) => toggle(o.value, e.target.checked)}
                    />
                    <span>{o.label}</span>
                  </label>
                );
              })}
            </fieldset>
          ))}
          {matches.length === 0 ? <p className="muted small">Tidak ada yang cocok.</p> : null}
          {options.length > RENDER_LIMIT && matches.length === RENDER_LIMIT ? (
            <p className="muted small">Menampilkan {RENDER_LIMIT} pertama; persempit dengan pencarian.</p>
          ) : null}
        </div>
        <div className="ms-foot">
          <span className="muted small">Maks {max} pilihan</span>
          <button type="button" className="btn btn-sm" onClick={() => onChange([])} disabled={selected.length === 0}>
            Hapus pilihan
          </button>
        </div>
      </div>
    </details>
  );
}
