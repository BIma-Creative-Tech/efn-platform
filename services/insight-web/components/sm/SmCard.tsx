/**
 * Kerangka kartu supermarket (spesifikasi UX §5.1, `InsightCard`): judul h2 (fokus lewat jangkar), baris kesegaran kartu
 * ("Data per 14:45 WIB (12 menit lalu)"), spanduk usang, isi, baris aksi. Server component (tanpa state); `SmCardAuto`
 * memetakan kesegaran mart + hasil query ke keadaan kartu: siap / galat / empat keadaan "tidak ada angka" (§6.5).
 */
import type { ReactNode } from 'react';
import type { Result } from '@/lib/loaders';
import { asOfText, emptyText, freshnessOf, resolveCardState, staleBanner, type EmptyKind, type FreshLevel, type FreshResult } from '@/lib/sm-freshness';
import type { MartFreshness } from '@/lib/sm-parse';
import type { SmThresholds } from '@/lib/sm-thresholds';
import { RetryButton } from '../RetryButton';
import { DeltaBadge, EmptyState, Icon, InfoTip, StatusPill } from '../ui';
import type { Status } from '@/lib/thresholds';

export function SmCard({
  id,
  title,
  asOf,
  banner,
  bannerBad = false,
  actions,
  wide = false,
  children,
}: {
  id: string;
  title: string;
  asOf?: string | null;
  banner?: string | null;
  bannerBad?: boolean;
  actions?: ReactNode;
  wide?: boolean;
  children: ReactNode;
}): ReactNode {
  return (
    <section className={`card sm-card${wide ? ' wide' : ''}`} aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} tabIndex={-1}>
        {title}
      </h2>
      {asOf ? <p className="sm-asof">{asOf}</p> : null}
      {banner ? (
        <div className={`sm-banner${bannerBad ? ' sm-banner-bad' : ''}`} role="note">
          <Icon name="warn" size={16} />
          <span>{banner}</span>
        </div>
      ) : null}
      {children}
      {actions ? <div className="card-tools">{actions}</div> : null}
    </section>
  );
}

export function SmErrorInline({ refId, reason }: { refId: string; reason?: 'rate_limited' }): ReactNode {
  return (
    <div role="alert">
      <p>{reason === 'rate_limited' ? 'Terlalu banyak permintaan. Coba lagi sebentar.' : 'Data tidak dapat dimuat. Coba lagi.'}</p>
      <p className="muted small">Kode referensi: {refId}</p>
      <RetryButton />
    </div>
  );
}

export interface FreshBase {
  freshness: ReadonlyMap<string, MartFreshness>;
  t: SmThresholds;
}

export function freshOf(base: FreshBase, mart: string, now: Date): FreshResult {
  return freshnessOf(base.freshness.get(mart), now, base.t);
}

/**
 * Kartu lengkap: kesegaran dari `mart`, hasil `result`, `hasRows` menentukan kosong/terisi. `zeroMeans`: 'zero' = tidak ada
 * baris berarti nol sungguhan (mis. tidak ada lot); 'no-data' = tidak ada baris berarti belum ada data periode itu.
 */
export function SmCardAuto<T>({
  id,
  title,
  mart,
  base,
  now,
  result,
  hasRows,
  zeroMeans,
  texts,
  actions,
  wide,
  staleDetail,
  children,
}: {
  id: string;
  title: string;
  mart: string;
  base: FreshBase;
  now: Date;
  result: Result<T>;
  hasRows: (d: T) => boolean;
  zeroMeans: 'zero' | 'no-data';
  texts: { zero: string; off: string; noData?: string };
  actions?: ReactNode;
  wide?: boolean;
  staleDetail?: string;
  children: (data: T, level: FreshLevel) => ReactNode;
}): ReactNode {
  const fresh = base.freshness.get(mart);
  const fr = freshnessOf(fresh, now, base.t);
  const asOf = fresh?.dataAsOf ? asOfText(fr, now) : null;
  if (!result.ok) {
    return (
      <SmCard id={id} title={title} asOf={asOf} wide={wide}>
        <SmErrorInline refId={result.ref} reason={result.reason} />
      </SmCard>
    );
  }
  const state = resolveCardState({ fresh, level: fr.level, hasRows: hasRows(result.data), zeroMeans });
  if (state.kind === 'unknown') {
    return (
      <SmCard id={id} title={title} asOf="Kesegaran data tidak diketahui" wide={wide}>
        <EmptyState title="Pembaruan data belum pernah berhasil, jadi angka tidak ditampilkan." icon="warn" />
      </SmCard>
    );
  }
  if (state.kind === 'empty') {
    const e = emptyText(state.empty as EmptyKind, texts, staleDetail ?? (asOf ? `${asOf}; mungkin belum mencakup transaksi terbaru.` : undefined));
    return (
      <SmCard id={id} title={title} asOf={state.empty === 'feature-off' ? null : asOf} wide={wide}>
        <EmptyState title={e.title} icon={e.icon} tone={state.empty === 'zero' ? 'positive' : 'neutral'} />
      </SmCard>
    );
  }
  return (
    <SmCard id={id} title={title} asOf={asOf} banner={staleBanner(state.level)} bannerBad={state.level === 'bad'} actions={actions} wide={wide}>
      {children(result.data, state.level)}
    </SmCard>
  );
}

/** Ubin KPI supermarket: nilai teks siap tampil (label basis uang wajib di `label`, L-202), status = ikon + kata. */
export function SmKpiTile({
  label,
  value,
  status,
  statusLabel,
  delta,
  note,
  info,
  muted = false,
}: {
  label: string;
  value: string;
  status?: Status;
  statusLabel?: string;
  delta?: { value: number | null; kind: 'pct' | 'pp'; goodWhen: 'up' | 'down' | 'neutral'; compareLabel?: string };
  note?: string;
  info?: ReactNode;
  /** Nilai berupa teks keadaan ("Belum aktif", "Menunggu data"), bukan angka: tampil lebih kecil. */
  muted?: boolean;
}): ReactNode {
  return (
    <div className="kpi">
      <dt>
        <span>{label}</span>
        {info ? <InfoTip label={label}>{info}</InfoTip> : null}
      </dt>
      <dd>
        <span className="kpi-value" style={muted ? { fontSize: 16, fontWeight: 600 } : undefined}>
          {value}
        </span>
        {status && status !== 'neutral' ? <StatusPill status={status} label={statusLabel} /> : null}
        {delta ? <DeltaBadge value={delta.value} kind={delta.kind} goodWhen={delta.goodWhen} compareLabel={delta.compareLabel} /> : null}
        {note ? <span className="kpi-note">{note}</span> : null}
      </dd>
    </div>
  );
}

/** Tautan "Buka di Odoo": tab baru, `rel="noopener noreferrer"`, label spesifik + ikon (aria-hidden) + "(tab baru)" sr-only. */
export function ActionLink({ label, href, big = false }: { label: string; href: string | null; big?: boolean }): ReactNode {
  if (!href) return null;
  return (
    <a className={`btn action-link${big ? ' action-link-big' : ''}`} href={href} target="_blank" rel="noopener noreferrer">
      <span>{label}</span>
      <Icon name="external" size={16} />
      <span className="sr-only">(tab baru)</span>
    </a>
  );
}
