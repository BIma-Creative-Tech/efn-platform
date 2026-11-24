'use client';
/**
 * Pencarian BOM (combobox aksesibel) untuk baris permintaan: kolom cari (debounce 300 ms, minimal 2 karakter), daftar pilihan
 * (role listbox; panah atas/bawah, Enter memilih, Esc menutup), jumlah hasil lewat aria-live, status kosong/galat.
 * Data dari `GET /api/prodplan/boms` (lib/prodplan-call.ts searchBoms). Semua setState berada di event handler / callback async.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { BOM_Q_MIN, BOM_LIMIT } from '@/lib/prodplan-actions';
import { searchBoms } from '@/lib/prodplan-call';
import type { BomItem } from '@/lib/prodplan-types';

const DEBOUNCE_MS = 300;

type Status = { kind: 'idle' } | { kind: 'loading' } | { kind: 'ok'; items: BomItem[] } | { kind: 'error'; message: string };

export function BomPicker({ brand, label, onPick }: { brand: string; label: string; onPick: (b: BomItem) => void }): React.ReactNode {
  const uid = useId();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const ctl = useRef<AbortController | null>(null);
  const seq = useRef(0);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      ctl.current?.abort();
    },
    [],
  );

  const run = (q: string): void => {
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    seq.current += 1;
    const mine = seq.current;
    void searchBoms(brand, q, c.signal).then((r) => {
      if (mine !== seq.current) return; // hasil basi
      if (r.ok) {
        setStatus({ kind: 'ok', items: r.items });
        setActive(r.items.length > 0 ? 0 : -1);
      } else {
        setStatus({ kind: 'error', message: r.message });
        setActive(-1);
      }
    });
  };

  const onChange = (v: string): void => {
    setQuery(v);
    setOpen(true);
    if (timer.current) clearTimeout(timer.current);
    const q = v.trim();
    if (Array.from(q).length < BOM_Q_MIN) {
      ctl.current?.abort();
      seq.current += 1;
      setStatus({ kind: 'idle' });
      setActive(-1);
      return;
    }
    setStatus({ kind: 'loading' });
    timer.current = setTimeout(() => run(q), DEBOUNCE_MS);
  };

  const choose = (b: BomItem): void => {
    setOpen(false);
    setQuery('');
    setStatus({ kind: 'idle' });
    onPick(b);
  };

  const items = status.kind === 'ok' ? status.items : [];
  const listId = `${uid}-list`;
  const showList = open && items.length > 0;

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'ArrowDown' && items.length > 0) {
      e.preventDefault();
      setOpen(true);
      setActive((a) => (a + 1) % items.length);
    } else if (e.key === 'ArrowUp' && items.length > 0) {
      e.preventDefault();
      setOpen(true);
      setActive((a) => (a <= 0 ? items.length - 1 : a - 1));
    } else if (e.key === 'Enter' && showList && active >= 0 && active < items.length) {
      e.preventDefault(); // jangan kirim form induk
      choose(items[active]);
    } else if (e.key === 'Escape' && open) {
      e.preventDefault();
      setOpen(false);
    }
  };

  const live =
    status.kind === 'loading'
      ? 'Mencari BOM...'
      : status.kind === 'ok'
        ? items.length === 0
          ? 'Tidak ada BOM yang cocok. Coba kata kunci lain.'
          : `${items.length} hasil${items.length >= BOM_LIMIT ? ' (dibatasi; persempit kata kunci)' : ''}. Gunakan panah atas atau bawah, lalu Enter untuk memilih.`
        : status.kind === 'idle' && query.trim() !== ''
          ? `Ketik minimal ${BOM_Q_MIN} karakter.`
          : '';

  return (
    <div className="pp-bom">
      <label htmlFor={`${uid}-q`} className="sr-only">
        {label}
      </label>
      <input
        id={`${uid}-q`}
        type="text"
        className="input"
        role="combobox"
        autoComplete="off"
        placeholder="Cari nama BOM atau produk..."
        maxLength={64}
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList && active >= 0 ? `${uid}-o${active}` : undefined}
        aria-describedby={`${uid}-live`}
        value={query}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKey}
      />
      <p id={`${uid}-live`} className="muted small" role="status" aria-live="polite">
        {live}
      </p>
      {status.kind === 'error' ? (
        <p role="alert" className="pp-msg pp-msg-error">
          <strong>Gagal mencari:</strong> {status.message}
        </p>
      ) : null}
      <ul id={listId} role="listbox" aria-label={`Hasil pencarian BOM, ${label}`} className="pp-bom-list" hidden={!showList}>
        {items.map((b, i) => (
          <li
            key={b.bom_id}
            id={`${uid}-o${i}`}
            role="option"
            aria-selected={i === active}
            className={i === active ? 'pp-bom-opt pp-bom-active' : 'pp-bom-opt'}
            onMouseDown={(e) => {
              e.preventDefault(); // pertahankan fokus input
              choose(b);
            }}
          >
            <strong>{b.product_name || b.product_code}</strong>
            <span className="muted small">
              {' '}
              ({b.product_code}) - BOM: {b.bom_name || b.bom_code}
              {b.uom_name ? ` - satuan ${b.uom_name}` : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
