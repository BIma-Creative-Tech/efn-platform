'use client';
/**
 * Toolbar filter global halaman F&B (spec §3.2; review 21 Nov 2026). State = URL search params; server memvalidasi ulang
 * (klien hanya untuk UX). Filter DIPERLAKUKAN LANGSUNG: chip brand dan preset tanggal memakai `router.push` seketika;
 * tanggal kustom dan pilihan ganda (cabang/kategori/produk) di-debounce ~400-500 ms. Tidak ada tombol "Terapkan".
 * Hanya kontrol yang berlaku di halaman ini yang ditampilkan; halaman tanpa rentang tanggal memberi keterangan eksplisit.
 */
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useEffectEvent, useState, useTransition } from 'react';
import { codeColor, codeName } from '@/lib/brands';
import { announce } from '@/lib/client-nav';
import { useBusy } from '@/lib/loading-store';
import { buildEsbQuery, nextPt, sameSet, type FilterDraft } from '@/lib/filter-url';
import { fmtRange } from '@/lib/format';
import { appliesTo, DATE_NA_REASON, selectableCodes } from '@/lib/page-filters';
import { DateRangeControl } from './DateRangeControl';
import { MultiSelect, type Option } from './MultiSelect';

export interface FilterOptions {
  branches: (Option & { code: string })[];
  categories: string[];
  products: Option[];
}

interface Props {
  options: FilterOptions;
  /** Semua kode yang boleh dipilih: brand ESB sesi, ditambah `SMD` bila `smdAllowed` (G51 B1). */
  sessionCodes: string[];
  /** Kode brand ESB sesi (SMD tidak termasuk). */
  esbCodes: string[];
  /** SMD adalah pilihan sungguhan (sesi exec); false -> perilaku lama. */
  smdAllowed?: boolean;
  defaults: { from: string; to: string };
  yesterday: string;
}

const DEBOUNCE_MS = 500;

function FilterForm({ options, sessionCodes: allSessionCodes, smdAllowed: smdAllowedProp = false, defaults, yesterday }: Props): React.ReactNode {
  const router = useRouter();
  const pathname = usePathname();
  // Halaman persona F&B (/fnb/*) hanya ESB: tanpa chip SMD, tanpa cabang `sm:`.
  const sessionCodes = selectableCodes(pathname, allSessionCodes);
  const smdAllowed = smdAllowedProp && sessionCodes.includes('SMD');
  const sp = useSearchParams();
  const [pending, start] = useTransition();
  useBusy(pending);
  const [queued, setQueued] = useState<FilterDraft | null>(null);

  const urlFrom = sp.get('from') ?? defaults.from;
  const urlTo = sp.get('to') ?? defaults.to;
  const urlPt = (sp.get('pt') ?? '').split(',').filter((c) => sessionCodes.includes(c.toUpperCase())).map((c) => c.toUpperCase());
  const urlBranch = (sp.get('branch') ?? '').split(',').filter(Boolean);
  const urlCat = sp.getAll('cat');
  const urlProduct = (sp.get('product') ?? '').split(',').filter(Boolean);
  const urlKey = sp.toString();

  const [branch, setBranch] = useState<string[]>(urlBranch);
  const [cat, setCat] = useState<string[]>(urlCat);
  const [product, setProduct] = useState<string[]>(urlProduct);
  const [seen, setSeen] = useState(urlKey);
  const [dirty, setDirty] = useState(false);
  // Sinkron dari URL (Kembali/Maju, hapus chip, drill-down) selama tidak ada perubahan pilihan ganda yang menunggu debounce.
  if (seen !== urlKey && !dirty) {
    setSeen(urlKey);
    setBranch(urlBranch);
    setCat(urlCat);
    setProduct(urlProduct);
  }

  useEffect(() => {
    document.body.toggleAttribute('data-busy', pending);
  }, [pending]);
  const push = (next: FilterDraft, forceDate: boolean): void => {
    // Membatalkan debounce yang menunggu: `queued` = null -> efek di bawah membersihkan timernya.
    setQueued(null);
    setDirty(false);
    const qs = buildEsbQuery(sp, next, { defaults, allCodes: sessionCodes, forceDate });
    start(() => router.push(qs ? `${pathname}?${qs}` : pathname));
    announce('Filter diterapkan. Data diperbarui.');
  };
  const later = (next: FilterDraft): void => {
    setDirty(true);
    setQueued(next);
  };
  // Debounce pilihan ganda tanpa ref (aturan react-hooks/refs): timer hidup di efek, dibatalkan saat `queued` berubah/unmount.
  const flush = useEffectEvent((next: FilterDraft) => push(next, false));
  useEffect(() => {
    if (queued === null) return;
    const id = window.setTimeout(() => flush(queued), DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [queued]);

  const current: FilterDraft = { from: urlFrom, to: urlTo, pt: urlPt, branch, cat, product };
  const hasDate = appliesTo(pathname, 'date');
  const hasPt = appliesTo(pathname, 'pt', smdAllowed);
  const hasBranch = appliesTo(pathname, 'branch', smdAllowed);
  const hasCat = appliesTo(pathname, 'cat');
  const hasProduct = appliesTo(pathname, 'product');
  const dateNa = DATE_NA_REASON[pathname];

  // Halaman tanpa filter sama sekali (glosarium).
  if (!hasDate && !hasPt && !hasBranch && !hasCat && !hasProduct && !dateNa) return null;

  const allSelected = urlPt.length === 0 || sameSet(urlPt, sessionCodes);
  // Mengganti brand/PT membuang cabang terpilih yang bukan milik brand/PT baru (kalau tidak, hasilnya kosong tanpa penjelasan).
  const withPt = (pt: string[]): FilterDraft => {
    const codes = pt.length === 0 ? sessionCodes : pt;
    const keep = urlBranch.filter((b) => {
      const o = options.branches.find((x) => x.value === b);
      return !o || codes.includes(o.code);
    });
    return { ...current, pt, branch: keep };
  };
  // Tanpa PT dipilih = semua PT sesi (termasuk outlet SMD untuk sesi exec).
  const branchOptions = options.branches.filter((b) => (urlPt.length === 0 ? sessionCodes.includes(b.code) : urlPt.includes(b.code)));

  // Chip aktif (dari URL, bukan draft).
  const labelOf = (list: Option[], v: string): string => list.find((o) => o.value === v)?.label ?? v;
  const chips: { key: string; label: string; remove: () => void }[] = [];
  if (hasDate && (urlFrom !== defaults.from || urlTo !== defaults.to)) {
    chips.push({ key: 'date', label: fmtRange(urlFrom, urlTo), remove: () => push({ ...current, from: defaults.from, to: defaults.to }, false) });
  }
  if (hasPt) {
    for (const c of urlPt) chips.push({ key: `pt-${c}`, label: c, remove: () => push(withPt(urlPt.filter((x) => x !== c)), false) });
  }
  if (hasBranch) {
    for (const b of urlBranch) chips.push({ key: `b-${b}`, label: labelOf(options.branches, b), remove: () => push({ ...current, branch: urlBranch.filter((x) => x !== b) }, false) });
  }
  if (hasCat) {
    for (const c of urlCat) chips.push({ key: `c-${c}`, label: c, remove: () => push({ ...current, cat: urlCat.filter((x) => x !== c) }, false) });
  }
  if (hasProduct) {
    for (const pr of urlProduct) chips.push({ key: `p-${pr}`, label: labelOf(options.products, pr), remove: () => push({ ...current, product: urlProduct.filter((x) => x !== pr) }, false) });
  }

  return (
    <div className="filterbar" role="group" aria-label="Filter data">
      <div className="fb-row">
        {hasDate ? (
          <DateRangeControl from={urlFrom} to={urlTo} yesterday={yesterday} onChange={(r) => push({ ...current, from: r.from, to: r.to }, true)} />
        ) : dateNa ? (
          <p className="fb-na" role="note">
            <strong>Rentang tanggal tidak berlaku:</strong> {dateNa}
          </p>
        ) : null}

        {hasPt ? (
          sessionCodes.length <= 1 ? (
            <span className="chip chip-static" title="Brand sesi Anda">
              {sessionCodes[0] ? codeName(sessionCodes[0]) : '-'}
            </span>
          ) : (
            <div role="group" aria-label="Brand atau PT" className="fb-seg">
              <button type="button" className="fb-seg-btn" aria-pressed={allSelected} onClick={() => push(withPt([]), false)}>
                Semua
              </button>
              {sessionCodes.map((c) => {
                const on = !allSelected && urlPt.includes(c);
                return (
                  <button
                    key={c}
                    type="button"
                    className="fb-seg-btn"
                    aria-pressed={on}
                    title={codeName(c)}
                    onClick={() => push(withPt(nextPt(urlPt, c, sessionCodes)), false)}
                  >
                    <span className={`dot dot-${codeColor(c)}`} aria-hidden="true" />
                    {c}
                  </button>
                );
              })}
            </div>
          )
        ) : null}

        {hasBranch ? (
          <MultiSelect
            label="Cabang"
            summaryNoun="cabang"
            options={branchOptions}
            selected={branch}
            onChange={(v) => {
              setBranch(v);
              later({ ...current, branch: v, cat, product });
            }}
            max={50}
          />
        ) : null}
        {hasCat ? (
          <MultiSelect
            label="Kategori menu"
            summaryNoun="kategori"
            options={options.categories.map((c) => ({ value: c, label: c }))}
            selected={cat}
            onChange={(v) => {
              setCat(v);
              later({ ...current, branch, cat: v, product });
            }}
            max={20}
          />
        ) : null}
        {hasProduct ? (
          <MultiSelect
            label="Produk FIN"
            summaryNoun="produk"
            options={options.products}
            selected={product}
            onChange={(v) => {
              setProduct(v);
              later({ ...current, branch, cat, product: v });
            }}
            max={20}
          />
        ) : null}

        <span className="fb-spacer" />
        {chips.length > 0 ? (
          <button type="button" className="btn btn-sm" onClick={() => push({ from: defaults.from, to: defaults.to, pt: [], branch: [], cat: [], product: [] }, false)}>
            Reset
          </button>
        ) : null}
      </div>

      {hasPt && smdAllowed && (urlPt.length === 0 || urlPt.includes('SMD')) ? (
        <p className="muted small" role="note">
          SMD: omzet termasuk PPN; kartu khusus F&amp;B tidak memuat SMD.
        </p>
      ) : null}

      {chips.length > 0 ? (
        <ul className="chip-list" aria-label="Filter aktif">
          {chips.map((c) => (
            <li key={c.key} className="chip">
              <span>{c.label}</span>
              <button type="button" className="chip-x" aria-label={`Hapus filter ${c.label}`} onClick={c.remove}>
                <span aria-hidden="true">&times;</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function FilterBar(props: Props): React.ReactNode {
  return <FilterForm {...props} />;
}
