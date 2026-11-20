/**
 * Komponen presentasi bersama (tanpa state; aman dipakai di Server maupun Client Component).
 * Semua warna dari token CSS (globals.css); status selalu ikon + kata + warna (tidak pernah warna saja).
 */
import type { ReactNode } from 'react';
import { fmtDelta, fmtValue, type ValueFmt } from '@/lib/format';
import { STATUS_LABEL, type Status } from '@/lib/thresholds';

const ICON_PATHS: Record<string, string> = {
  home: 'M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  flow: 'M4 6h10M4 12h16M4 18h10M17 4l3 2-3 2M17 16l3 2-3 2',
  cart: 'M3 4h2l2.5 11h10L20 7H6.5M9 20a1 1 0 1 0 0.01 0M17 20a1 1 0 1 0 0.01 0',
  card: 'M3 6h18v12H3zM3 10h18M6 15h4',
  truck: 'M2 6h11v9H2zM13 9h4l3 3v3h-7M6 18a1.5 1.5 0 1 0 0.01 0M17 18a1.5 1.5 0 1 0 0.01 0',
  check: 'M4 12l5 5L20 6',
  trend: 'M3 17l6-6 4 4 8-9M15 6h6v6',
  menu: 'M4 6h16M4 12h16M4 18h16',
  close: 'M6 6l12 12M18 6L6 18',
  up: 'M12 5l6 8H6z',
  down: 'M12 19l-6-8h12z',
  info: 'M12 8h.01M11 12h1v5h1',
  warn: 'M12 3l10 18H2zM12 10v5M12 18h.01',
  bad: 'M12 3a9 9 0 1 0 0.01 0M9 9l6 6M15 9l-6 6',
  empty: 'M4 7h16v12H4zM4 7l3-3h10l3 3M9 12h6',
  // G47 (persona supermarket): ikon sederhana, stroke saja.
  briefcase: 'M3 8h18v11H3zM9 8V5h6v3M3 13h18',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  store: 'M3 9l2-5h14l2 5M3 9h18v2a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0zM5 13v7h14v-7',
  megaphone: 'M3 10v4h4l8 4V6L7 10zM18 9a4 4 0 0 1 0 6',
  external: 'M14 4h6v6M20 4l-9 9M18 14v5H5V6h5',
  clock: 'M12 3a9 9 0 1 0 0.01 0M12 7v5l3 2',
};

export function Icon({ name, size = 18 }: { name: string; size?: number }): ReactNode {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={ICON_PATHS[name] ?? ICON_PATHS.info} />
    </svg>
  );
}

export function StatusPill({ status, label }: { status: Status; label?: string }): ReactNode {
  const icon = status === 'good' ? 'check' : status === 'warn' ? 'warn' : status === 'bad' ? 'bad' : 'info';
  return (
    <span className={`pill pill-${status}`}>
      <Icon name={icon} size={14} />
      <span>{label ?? STATUS_LABEL[status]}</span>
    </span>
  );
}

export function DeltaBadge({
  value,
  kind,
  goodWhen,
  compareLabel = 'periode lalu',
}: {
  value: number | null;
  kind: 'pct' | 'pp';
  goodWhen: 'up' | 'down' | 'neutral';
  compareLabel?: string;
}): ReactNode {
  if (value === null || !Number.isFinite(value)) {
    return (
      <span className="delta delta-neutral">
        <span aria-hidden="true">-</span>
        <span className="sr-only">tidak ada pembanding</span>
      </span>
    );
  }
  const flat = Math.abs(value) < 0.05;
  const dir = value > 0 ? 'up' : 'down';
  const tone = flat || goodWhen === 'neutral' ? 'neutral' : dir === goodWhen ? 'good' : 'bad';
  const unit = kind === 'pct' ? 'persen' : 'poin persentase';
  const words = flat ? 'tidak berubah' : `${dir === 'up' ? 'naik' : 'turun'} ${fmtValue(kind === 'pct' ? 'pct' : 'pp', Math.abs(value)).replace(/%| pp/, '')} ${unit}`;
  return (
    <span className={`delta delta-${tone}`}>
      {flat ? null : <Icon name={dir} size={12} />}
      <span aria-hidden="true">{fmtDelta(value, kind)}</span>
      <span className="sr-only">
        {words} dari {compareLabel}
      </span>
    </span>
  );
}

export function Sparkline({ values }: { values: number[] }): ReactNode {
  if (values.length < 2) return null;
  const w = 120;
  const h = 32;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - 3 - ((v - min) / span) * (h - 6)).toFixed(1)}`)
    .join(' ');
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} width={w} height={h} aria-hidden="true" focusable="false" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export function InfoTip({ label, children }: { label: string; children: ReactNode }): ReactNode {
  return (
    <details className="info">
      <summary aria-label={`Definisi: ${label}`}>
        <Icon name="info" size={14} />
      </summary>
      <div className="info-body" role="note">
        {children}
      </div>
    </details>
  );
}

export interface KpiProps {
  label: string;
  value: number | null;
  fmt: ValueFmt;
  delta?: { value: number | null; kind: 'pct' | 'pp'; goodWhen: 'up' | 'down' | 'neutral' };
  spark?: number[];
  status?: Status;
  info?: ReactNode;
  note?: string;
}

export function KpiTile({ label, value, fmt, delta, spark, status, info, note }: KpiProps): ReactNode {
  return (
    <div className="kpi">
      <dt>
        <span>{label}</span>
        {info ? <InfoTip label={label}>{info}</InfoTip> : null}
      </dt>
      <dd>
        <span className="kpi-value">{value === null ? 'n/a' : fmtValue(fmt, value)}</span>
        {status && status !== 'neutral' ? <StatusPill status={status} /> : null}
        {delta ? <DeltaBadge value={delta.value} kind={delta.kind} goodWhen={delta.goodWhen} /> : null}
        {note ? <span className="kpi-note">{note}</span> : null}
        {spark ? <Sparkline values={spark} /> : null}
      </dd>
    </div>
  );
}

export function KpiRow({ children }: { children: ReactNode }): ReactNode {
  return <dl className="kpi-row">{children}</dl>;
}

export function EmptyState({
  title,
  reason,
  tone = 'neutral',
  icon,
  children,
}: {
  title: string;
  reason?: string;
  tone?: 'neutral' | 'positive';
  /** Ikon eksplisit (G47: empat keadaan "tidak ada angka" memakai check/clock/info/warn). */
  icon?: string;
  children?: ReactNode;
}): ReactNode {
  return (
    <div className={`empty empty-${tone}`}>
      <Icon name={icon ?? (tone === 'positive' ? 'check' : 'empty')} size={28} />
      <p className="empty-title">{title}</p>
      {reason ? <p className="empty-reason">{reason}</p> : null}
      {children}
    </div>
  );
}

export function PageHeader({ title, question, actions }: { title: string; question: string; actions?: ReactNode }): ReactNode {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        <p className="page-q">{question}</p>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}

export function Section({ title, children, id }: { title?: string; children: ReactNode; id?: string }): ReactNode {
  return (
    <section className="section" aria-labelledby={id}>
      {title ? (
        <h2 id={id} className="section-title">
          {title}
        </h2>
      ) : null}
      {children}
    </section>
  );
}
