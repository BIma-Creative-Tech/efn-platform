/**
 * Filter halaman persona (spesifikasi UX §3): formulir GET server-rendered (state di URL, divalidasi ulang di server —
 * lib/sm-filters.ts). Label eksplisit, fieldset/legend untuk kelompok outlet, tombol Terapkan + Reset. Kontrol yang tidak
 * berlaku tidak dirender; satu outlet = tanpa kontrol outlet (label statis ada di judul halaman).
 */
import Link from 'next/link';
import type { ReactNode } from 'react';
import type { SmFilters } from '@/lib/sm-filters';

export interface SmFilterShow {
  period: boolean;
  outlets: boolean;
  category: boolean;
}

export function SmFilterForm({
  path,
  filters,
  yesterday,
  outlets,
  categories,
  show,
  adjusted,
}: {
  path: string;
  filters: SmFilters;
  yesterday: string;
  outlets: { id: number; name: string }[];
  categories: { id: number; name: string }[];
  show: SmFilterShow;
  adjusted: boolean;
}): ReactNode {
  const showOutlets = show.outlets && outlets.length > 1;
  const showCategory = show.category && categories.length > 0;
  if (!show.period && !showOutlets && !showCategory) return null;
  return (
    <>
      {adjusted ? (
        <p className="notice" role="status">
          Sebagian filter tidak valid dan dikembalikan ke bawaan.
        </p>
      ) : null}
      <form method="get" action={path} className="sm-filters" aria-label="Filter halaman">
        {show.period ? (
          <>
            <div>
              <label htmlFor="f-from">Dari tanggal</label>
              <input id="f-from" name="from" type="date" className="input" defaultValue={filters.from} max={yesterday} required />
            </div>
            <div>
              <label htmlFor="f-to">Sampai tanggal</label>
              <input id="f-to" name="to" type="date" className="input" defaultValue={filters.to} max={yesterday} required />
            </div>
          </>
        ) : null}
        {showOutlets ? (
          <fieldset>
            <legend>Outlet (kosong = semua)</legend>
            {outlets.map((o) => (
              <label key={o.id} className="check">
                <input type="checkbox" name="wh" value={o.id} defaultChecked={filters.wh.includes(o.id)} />
                {o.name}
              </label>
            ))}
          </fieldset>
        ) : null}
        {showCategory ? (
          <div>
            <label htmlFor="f-cat">Kategori (kosong = semua)</label>
            {/* Input tersembunyi menjaga kunci `cat` selalu terkirim: pengosongan = semua kategori, bukan default `cats`. */}
            <input type="hidden" name="cat" value="" />
            <select id="f-cat" name="cat" multiple size={4} className="input" defaultValue={filters.cat.map(String)}>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <div>
          <button type="submit" className="btn btn-primary">
            Terapkan
          </button>{' '}
          <Link href={path} className="btn">
            Reset
          </Link>
        </div>
      </form>
    </>
  );
}
