/**
 * Badge kesegaran halaman supermarket: tingkat TERBURUK dari mart yang memberi makan kartu halaman itu; popover per mart
 * (spesifikasi UX §5.2). Kesegaran tidak diketahui = "Tidak diketahui", bukan "Baik".
 */
import type { ReactNode } from 'react';
import { asOfText, freshnessOf, MART_LABEL, worstLevel, type FreshLevel } from '@/lib/sm-freshness';
import type { MartFreshness } from '@/lib/sm-parse';
import type { SmThresholds } from '@/lib/sm-thresholds';
import { StatusPill } from '../ui';
import type { Status } from '@/lib/thresholds';

const LEVEL_STATUS: Record<FreshLevel, Status> = { good: 'good', warn: 'warn', bad: 'bad', unknown: 'unknown' };
const LEVEL_TEXT: Record<FreshLevel, string> = { good: 'Segar', warn: 'Agak tertinggal', bad: 'Usang', unknown: 'Tidak diketahui' };

export function SmFreshnessBadge({
  marts,
  freshness,
  t,
  now,
}: {
  marts: readonly string[];
  freshness: ReadonlyMap<string, MartFreshness>;
  t: SmThresholds;
  now: Date;
}): ReactNode {
  // Mart yang belum pernah berjalan (fitur belum aktif) tidak menurunkan badge halaman; dia tampil "Belum aktif" di popover.
  const items = marts.map((m) => {
    const f = freshness.get(m);
    const off = !f || (f.dataAsOf === null && f.lastRunAt === null);
    return { mart: m, off, r: freshnessOf(f, now, t) };
  });
  const active = items.filter((i) => !i.off);
  const level = active.length === 0 ? 'unknown' : worstLevel(active.map((i) => i.r.level));
  return (
    <details className="fresh">
      <summary>
        <span className={`dot dot-${level === 'unknown' ? 'unknown' : level}`} aria-hidden="true" />
        <span>Data: {LEVEL_TEXT[level]}</span>
      </summary>
      <div className="fresh-panel">
        <ul>
          {items.map((i) => (
            <li key={i.mart}>
              <span>
                {MART_LABEL[i.mart] ?? i.mart}: {i.off ? 'belum aktif' : asOfText(i.r, now)}
              </span>
              {i.off ? null : <StatusPill status={LEVEL_STATUS[i.r.level]} label={LEVEL_TEXT[i.r.level]} />}
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}
