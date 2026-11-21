'use client';
/**
 * Toolbar filter halaman persona (spesifikasi UX §3; review 21 Nov 2026): state di URL, divalidasi ulang di server
 * (lib/sm-filters.ts). Filter DIPERLAKUKAN LANGSUNG (tanpa tombol Terapkan): rentang tanggal lewat popover (preset langsung,
 * kustom debounce ~400 ms), outlet dan kategori debounce ~500 ms. Rentang tanggal dibawa eksplisit ke URL begitu dipilih
 * agar konsisten dengan halaman lain. Kontrol yang tidak berlaku tidak dirender; satu outlet = tanpa kontrol outlet.
 */
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState, useTransition } from 'react';
import { announce } from '@/lib/client-nav';
import { buildSmQuery } from '@/lib/filter-url';
import { fmtRange } from '@/lib/format';
import type { SmFilters } from '@/lib/sm-filters';
import { DateRangeControl } from '../DateRangeControl';
import { MultiSelect } from '../MultiSelect';

export interface SmFilterShow {
  period: boolean;
  outlets: boolean;
  category: boolean;
}

const DEBOUNCE_MS = 500;

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
}): React.ReactNode {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const [pending, start] = useTransition();
  const timer = useRef<number | null>(null);
  const [wh, setWh] = useState<string[]>(filters.wh.map(String));
  const [cat, setCat] = useState<string[]>(filters.cat.map(String));
  const [catTouched, setCatTouched] = useState(false);
  const [seen, setSeen] = useState(`${filters.from}|${filters.to}|${filters.wh.join(',')}|${filters.cat.join(',')}`);
  const [dirty, setDirty] = useState(false);
  const urlKey = `${filters.from}|${filters.to}|${filters.wh.join(',')}|${filters.cat.join(',')}`;
  if (seen !== urlKey && !dirty) {
    setSeen(urlKey);
    setWh(filters.wh.map(String));
    setCat(filters.cat.map(String));
  }

  useEffect(() => {
    document.body.toggleAttribute('data-busy', pending);
  }, [pending]);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const showOutlets = show.outlets && outlets.length > 1;
  const showCategory = show.category && categories.length > 0;

  const push = (q: { from?: string; to?: string; wh: string[]; cat?: string[] }): void => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    setDirty(false);
    const qs = buildSmQuery(sp, { from: q.from, to: q.to, wh: q.wh.map(Number), cat: q.cat?.map(Number) });
    start(() => router.push(qs ? `${pathname}?${qs}` : pathname));
    announce('Filter diterapkan. Data diperbarui.');
  };
  const later = (q: { wh: string[]; cat?: string[] }): void => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    setDirty(true);
    timer.current = window.setTimeout(() => push(q), DEBOUNCE_MS);
  };

  if (!show.period && !showOutlets && !showCategory) return null;
  const nameOf = (list: { id: number; name: string }[], v: string): string => list.find((x) => String(x.id) === v)?.name ?? v;

  return (
    <>
      {adjusted ? (
        <p className="notice" role="status">
          Sebagian filter tidak valid dan dikembalikan ke bawaan.
        </p>
      ) : null}
      <div className="filterbar filterbar-inline" role="group" aria-label="Filter halaman">
        <div className="fb-row">
          {show.period ? (
            <DateRangeControl
              from={filters.from}
              to={filters.to}
              yesterday={yesterday}
              onChange={(r) => push({ from: r.from, to: r.to, wh, cat: catTouched ? cat : undefined })}
            />
          ) : null}
          {showOutlets ? (
            <MultiSelect
              label="Outlet"
              summaryNoun="outlet"
              options={outlets.map((o) => ({ value: String(o.id), label: o.name }))}
              selected={wh}
              onChange={(v) => {
                setWh(v);
                later({ wh: v, cat: catTouched ? cat : undefined });
              }}
              max={50}
            />
          ) : null}
          {showCategory ? (
            <MultiSelect
              label="Kategori"
              summaryNoun="kategori"
              options={categories.map((c) => ({ value: String(c.id), label: c.name }))}
              selected={cat}
              onChange={(v) => {
                setCat(v);
                setCatTouched(true);
                later({ wh, cat: v });
              }}
              max={20}
            />
          ) : null}
          <span className="fb-spacer" />
          {sp.toString() !== '' ? (
            <Link href={path} className="btn btn-sm">
              Reset
            </Link>
          ) : null}
        </div>
        <p className="muted small fb-summary">
          {show.period ? <>Periode: {fmtRange(filters.from, filters.to)}. </> : null}
          {showOutlets ? <>{filters.wh.length === 0 ? 'Semua outlet' : `${filters.wh.length} outlet: ${filters.wh.map((id) => nameOf(outlets, String(id))).join(', ')}`}. </> : null}
          {showCategory ? <>{filters.cat.length === 0 ? 'Semua kategori' : `${filters.cat.length} kategori`}{filters.catDefaulted ? ' (bawaan sesi)' : ''}.</> : null}
        </p>
      </div>
    </>
  );
}
