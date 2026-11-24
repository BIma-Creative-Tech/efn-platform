import Link from 'next/link';
import type { ReactNode } from 'react';

/**
 * Formulir filter khusus halaman G67 (GET; server memvalidasi ulang, klien hanya UX). Filter global (pt, branch, from, to)
 * dibawa lewat input tersembunyi agar tidak hilang saat "Terapkan". Tanpa JavaScript klien.
 */
export function G67Form({
  path,
  label,
  carry,
  summary,
  children,
}: {
  path: string;
  label: string;
  /** Param filter global yang dibawa (hanya nilai yang ada di URL). */
  carry: Readonly<Record<string, string | undefined>>;
  summary?: ReactNode;
  children: ReactNode;
}): ReactNode {
  return (
    <form method="get" action={path} className="filterbar filterbar-inline" aria-label={label}>
      {Object.entries(carry).map(([k, v]) => (v === undefined || v === '' ? null : <input key={k} type="hidden" name={k} value={v} />))}
      <div className="fb-row">
        {children}
        <button type="submit" className="btn btn-primary">
          Terapkan
        </button>
        <Link href={path} className="btn">
          Reset
        </Link>
      </div>
      {summary ? <p className="muted small fb-summary">{summary}</p> : null}
    </form>
  );
}

/** Kelompok kotak centang berlabel (fieldset + legend) di dalam <details> agar ringkas; dapat dioperasikan dengan keyboard. */
export function CheckGroup({
  legend,
  name,
  options,
  checked,
  hint,
}: {
  legend: string;
  name: string;
  options: readonly { value: string; label: string }[];
  checked: readonly string[];
  hint?: string;
}): ReactNode {
  return (
    <details className="g67-checks">
      <summary>
        {legend} ({checked.length} dipilih)
      </summary>
      <fieldset>
        <legend className="sr-only">{legend}</legend>
        {hint ? <p className="muted small">{hint}</p> : null}
        <ul>
          {options.map((o) => (
            <li key={o.value}>
              <label>
                <input type="checkbox" name={name} value={o.value} defaultChecked={checked.includes(o.value)} /> {o.label}
              </label>
            </li>
          ))}
        </ul>
      </fieldset>
    </details>
  );
}
