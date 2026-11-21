'use client';
/**
 * Pencarian + dropdown filter untuk tabel yang dirender SERVER (SmTable): baris membawa `data-q` (teks pencarian) dan
 * `data-f-<kunci>` (nilai filter); komponen ini hanya mengatur atribut `hidden` baris di <table id={targetId}>. Tanpa
 * JavaScript tabel tetap lengkap (peningkatan progresif). Menampilkan "n dari N baris".
 */
import { useEffect, useId, useState } from 'react';
import { countText, matchesQuery, matchesSelects } from '@/lib/table-filter';
import { Icon } from './ui';

export interface ToolFilter {
  key: string;
  label: string;
  options: string[];
}

export function TableTools({ targetId, caption, filters }: { targetId: string; caption: string; filters: ToolFilter[] }): React.ReactNode {
  const uid = useId();
  const [q, setQ] = useState('');
  const [sel, setSel] = useState<Record<string, string>>({});
  const [counts, setCounts] = useState<{ shown: number; total: number } | null>(null);

  useEffect(() => {
    const table = document.getElementById(targetId);
    if (!table) return;
    const rows = Array.from(table.querySelectorAll<HTMLTableRowElement>('tbody > tr'));
    let shown = 0;
    for (const tr of rows) {
      const values: Record<string, string> = {};
      for (const f of filters) values[f.key] = tr.dataset[`f${f.key}`] ?? '';
      const ok = matchesQuery(tr.dataset.q ?? '', q) && matchesSelects(values, sel);
      tr.hidden = !ok;
      if (ok) shown += 1;
    }
    // setState lewat microtask: hasil hitung hanya diketahui setelah membaca DOM.
    const id = window.setTimeout(() => setCounts({ shown, total: rows.length }), 0);
    return () => window.clearTimeout(id);
  }, [targetId, q, sel, filters]);

  const active = q !== '' || Object.values(sel).some((v) => v !== '');
  return (
    <div className="table-tools" role="search" aria-label={`Cari dan saring: ${caption}`}>
      <div className="tt-search">
        <Icon name="search" size={16} />
        <label className="sr-only" htmlFor={`${uid}-q`}>
          Cari di tabel {caption}
        </label>
        <input id={`${uid}-q`} type="search" className="input" placeholder="Cari..." value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {filters.map((f) => (
        <div key={f.key} className="tt-select">
          <label htmlFor={`${uid}-f-${f.key}`}>{f.label}</label>
          <select id={`${uid}-f-${f.key}`} className="input" value={sel[f.key] ?? ''} onChange={(e) => setSel((s) => ({ ...s, [f.key]: e.target.value }))}>
            <option value="">Semua</option>
            {f.options.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
      ))}
      {counts ? (
        <span className="muted small tt-count" aria-live="polite">
          {countText(counts.shown, counts.total)}
        </span>
      ) : null}
      {active ? (
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => {
            setQ('');
            setSel({});
          }}
        >
          Hapus penyaring
        </button>
      ) : null}
    </div>
  );
}
