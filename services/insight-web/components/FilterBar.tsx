'use client';
/**
 * Filter global (spec §3.2). State = URL search params; server memvalidasi ulang (klien hanya untuk UX).
 * Perubahan diterapkan lewat tombol "Terapkan" (bukan tiap ketikan). Chip aktif dengan tombol hapus + Reset.
 */
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState, useTransition } from 'react';
import { codeColor, codeName } from '@/lib/brands';
import { addDays } from '@/lib/dates';
import { fmtRange } from '@/lib/format';
import { appliesTo } from '@/lib/page-filters';
import { announce } from '@/lib/client-nav';
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
  /** SMD adalah toggle sungguhan (sesi exec); false -> perilaku lama. */
  smdAllowed?: boolean;
  defaults: { from: string; to: string };
  yesterday: string;
}

const sameSet = (a: readonly string[], b: readonly string[]): boolean => a.length === b.length && a.every((x) => b.includes(x));

type Preset = '7' | '30' | '90' | 'mtd' | 'prev-month' | 'ytd' | 'custom';

function presetRange(p: Preset, yesterday: string): { from: string; to: string } | null {
  const [y, m] = yesterday.split('-').map(Number);
  const pad = (n: number) => String(n).padStart(2, '0');
  switch (p) {
    case '7':
      return { from: addDays(yesterday, -6), to: yesterday };
    case '30':
      return { from: addDays(yesterday, -29), to: yesterday };
    case '90':
      return { from: addDays(yesterday, -89), to: yesterday };
    case 'mtd':
      return { from: `${y}-${pad(m)}-01`, to: yesterday };
    case 'prev-month': {
      const first = `${y}-${pad(m)}-01`;
      const to = addDays(first, -1);
      return { from: `${to.slice(0, 7)}-01`, to };
    }
    case 'ytd':
      return { from: `${y}-01-01`, to: yesterday };
    default:
      return null;
  }
}

function FilterForm({ options, sessionCodes, smdAllowed = false, defaults, yesterday }: Props): React.ReactNode {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const [pending, start] = useTransition();
  // Bawaan (pt kosong) = semua PT sesi, termasuk SMD untuk sesi exec (keputusan 21 Nov 2026, sama dengan brand lain).
  const defaultCodes = sessionCodes;

  const urlFrom = sp.get('from') ?? defaults.from;
  const urlTo = sp.get('to') ?? defaults.to;
  const urlPt = (sp.get('pt') ?? '').split(',').filter((c) => sessionCodes.includes(c.toUpperCase())).map((c) => c.toUpperCase());
  const urlBranch = (sp.get('branch') ?? '').split(',').filter(Boolean);
  const urlCat = sp.getAll('cat');
  const urlProduct = (sp.get('product') ?? '').split(',').filter(Boolean);

  const [from, setFrom] = useState(urlFrom);
  const [to, setTo] = useState(urlTo);
  const [preset, setPreset] = useState<Preset>('custom');
  const [pt, setPt] = useState<string[]>(urlPt);
  const [branch, setBranch] = useState<string[]>(urlBranch);
  const [cat, setCat] = useState<string[]>(urlCat);
  const [product, setProduct] = useState<string[]>(urlProduct);

  useEffect(() => {
    document.body.toggleAttribute('data-busy', pending);
  }, [pending]);

  const apply = (next: { from: string; to: string; pt: string[]; branch: string[]; cat: string[]; product: string[] }): void => {
    const p = new URLSearchParams(sp.toString());
    for (const k of ['from', 'to', 'pt', 'branch', 'cat', 'product']) p.delete(k);
    if (next.from !== defaults.from || next.to !== defaults.to) {
      p.set('from', next.from);
      p.set('to', next.to);
    }
    // Tanpa param = semua PT sesi (bawaan, termasuk SMD bila boleh). Subset apa pun ditulis eksplisit.
    if (next.pt.length > 0 && !sameSet(next.pt, defaultCodes)) p.set('pt', [...next.pt].sort().join(','));
    if (next.branch.length > 0) p.set('branch', next.branch.join(','));
    for (const c of next.cat) p.append('cat', c);
    if (next.product.length > 0) p.set('product', next.product.join(','));
    const qs = p.toString();
    start(() => router.push(qs ? `${pathname}?${qs}` : pathname));
    announce('Filter diterapkan. Data diperbarui.');
  };

  const current = { from, to, pt, branch, cat, product };
  const hasDate = appliesTo(pathname, 'date');
  const hasPt = appliesTo(pathname, 'pt', smdAllowed);
  const hasBranch = appliesTo(pathname, 'branch', smdAllowed);
  const hasCat = appliesTo(pathname, 'cat');
  const hasProduct = appliesTo(pathname, 'product');

  // Tanpa PT dipilih = semua PT sesi (termasuk outlet SMD untuk sesi exec).
  const branchOptions = options.branches.filter((b) => (pt.length === 0 ? defaultCodes.includes(b.code) : pt.includes(b.code)));
  const na = 'Tidak berlaku di halaman ini';

  // Chip aktif (dari URL, bukan draft).
  const chips: { key: string; label: string; remove: () => void }[] = [];
  if (urlFrom !== defaults.from || urlTo !== defaults.to) {
    chips.push({ key: 'date', label: fmtRange(urlFrom, urlTo), remove: () => apply({ ...current, from: defaults.from, to: defaults.to }) });
  }
  for (const c of urlPt) {
    chips.push({ key: `pt-${c}`, label: c, remove: () => apply({ ...current, pt: urlPt.filter((x) => x !== c) }) });
  }
  const labelOf = (list: Option[], v: string): string => list.find((o) => o.value === v)?.label ?? v;
  for (const b of urlBranch) {
    chips.push({ key: `b-${b}`, label: labelOf(options.branches, b), remove: () => apply({ ...current, branch: urlBranch.filter((x) => x !== b) }) });
  }
  for (const c of urlCat) {
    chips.push({ key: `c-${c}`, label: c, remove: () => apply({ ...current, cat: urlCat.filter((x) => x !== c) }) });
  }
  for (const pr of urlProduct) {
    chips.push({ key: `p-${pr}`, label: labelOf(options.products, pr), remove: () => apply({ ...current, product: urlProduct.filter((x) => x !== pr) }) });
  }

  return (
    <form
      className="filters"
      aria-label="Filter data"
      onSubmit={(e) => {
        e.preventDefault();
        apply(current);
      }}
    >
      <div className="filter-row">
        <fieldset className="f-group" disabled={!hasDate}>
          <legend>Rentang tanggal{hasDate ? '' : ` (${na})`}</legend>
          <label className="sr-only" htmlFor="f-preset">
            Preset rentang
          </label>
          <select
            id="f-preset"
            className="input"
            value={preset}
            onChange={(e) => {
              const p = e.target.value as Preset;
              setPreset(p);
              const r = presetRange(p, yesterday);
              if (r) {
                setFrom(r.from);
                setTo(r.to);
              }
            }}
          >
            <option value="custom">Kustom</option>
            <option value="7">7 hari</option>
            <option value="30">30 hari</option>
            <option value="mtd">Bulan ini</option>
            <option value="prev-month">Bulan lalu</option>
            <option value="90">90 hari</option>
            <option value="ytd">Tahun ini</option>
          </select>
          <label className="sr-only" htmlFor="f-from">
            Dari tanggal
          </label>
          <input id="f-from" type="date" className="input" value={from} max={yesterday} onChange={(e) => { setFrom(e.target.value); setPreset('custom'); }} />
          <span aria-hidden="true">-</span>
          <label className="sr-only" htmlFor="f-to">
            Sampai tanggal
          </label>
          <input id="f-to" type="date" className="input" value={to} max={yesterday} onChange={(e) => { setTo(e.target.value); setPreset('custom'); }} />
        </fieldset>

        <fieldset className="f-group" disabled={!hasPt}>
          <legend>Brand / PT{hasPt ? '' : ` (${na})`}</legend>
          {sessionCodes.length <= 1 ? (
            <span className="chip chip-static">{sessionCodes[0] ? codeName(sessionCodes[0]) : '-'}</span>
          ) : (
            <div role="group" aria-label="Brand atau PT" className="chips">
              {sessionCodes.map((c) => {
                const on = pt.includes(c);
                return (
                  <button key={c} type="button" className="chip chip-toggle" aria-pressed={on} title={codeName(c)} onClick={() => setPt(on ? pt.filter((x) => x !== c) : [...pt, c])}>
                    <span className={`dot dot-${codeColor(c)}`} aria-hidden="true" />
                    {c}
                  </button>
                );
              })}
            </div>
          )}
          {smdAllowed && (pt.length === 0 || pt.includes('SMD')) ? (
            <p className="muted small" role="note">
              SMD: omzet termasuk PPN; kartu khusus F&amp;B tidak memuat SMD.
            </p>
          ) : null}
        </fieldset>

        <MultiSelect label="Cabang" summaryNoun="cabang" options={branchOptions} selected={branch} onChange={setBranch} max={50} disabledReason={hasBranch ? undefined : na} />
        <MultiSelect label="Kategori menu" summaryNoun="kategori" options={options.categories.map((c) => ({ value: c, label: c }))} selected={cat} onChange={setCat} max={20} disabledReason={hasCat ? undefined : na} />
        <MultiSelect label="Produk FIN" summaryNoun="produk" options={options.products} selected={product} onChange={setProduct} max={20} disabledReason={hasProduct ? undefined : na} />

        <div className="f-actions">
          <button type="submit" className="btn btn-primary" disabled={pending}>
            Terapkan
          </button>
          {chips.length > 0 ? (
            <button type="button" className="btn" onClick={() => { setPt([]); setBranch([]); setCat([]); setProduct([]); apply({ from: defaults.from, to: defaults.to, pt: [], branch: [], cat: [], product: [] }); }}>
              Reset
            </button>
          ) : null}
        </div>
      </div>

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
    </form>
  );
}

export function FilterBar(props: Props): React.ReactNode {
  const sp = useSearchParams();
  // Reset draft setiap URL berubah (Kembali/Maju browser, drill-down, hapus chip).
  return <FilterForm key={sp.toString()} {...props} />;
}
