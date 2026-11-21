/**
 * Daftar kartu-baris near-expiry / stok kritis untuk ponsel (spesifikasi UX §4.1, §6.1 reflow): <ul> dengan <dl> berlabel
 * tetap terlihat, bukan tabel yang digeser. Status = ikon + kata (StatusPill). Nilai HPP = "Rp (HPP)" (L-203: kepala toko boleh).
 */
import type { ReactNode } from 'react';
import { fmtIdr, fmtQty } from '@/lib/format';
import { odooRecordUrl } from '@/lib/odoo-links';
import { fmtCover, fmtDaysLeft } from '@/lib/sm-format';
import type { CriticalRow, ExpiryDisplay, ExpiryLot } from '@/lib/sm-parse';
import { expiryReason } from '@/lib/status-reasons';
import type { Status } from '@/lib/thresholds';
import { StatusPill } from '../ui';
import { ActionLink } from './SmCard';

export const EXPIRY_PILL: Readonly<Record<ExpiryDisplay, { status: Status; label: string }>> = {
  kedaluwarsa: { status: 'bad', label: 'Kedaluwarsa' },
  markdown_sekarang: { status: 'warn', label: 'Markdown sekarang' },
  markdown_aktif: { status: 'good', label: 'Markdown aktif' },
  pantau: { status: 'neutral', label: 'Pantau' },
};

/** "markdown aktif -30%" / "markdown aktif Rp 9.000 s.d. 24 Nov" / "belum ada markdown". */
export function markdownText(l: ExpiryLot): string {
  if (l.display === 'markdown_aktif' || l.markdownId !== null) {
    const parts: string[] = [];
    if (l.markdownPct !== null && l.markdownPct > 0) parts.push(`-${Math.round(l.markdownPct)}%`);
    else if (l.markdownPrice !== null) parts.push(fmtIdr(l.markdownPrice));
    return parts.length > 0 ? `markdown aktif ${parts.join(' ')}` : 'markdown aktif';
  }
  return 'belum ada markdown';
}

export function ExpiryRows({
  lots,
  origin,
  whNames,
  label,
}: {
  lots: readonly ExpiryLot[];
  origin: string | null;
  /** Bila diisi, nama outlet ditampilkan (mis. >1 outlet). */
  whNames?: ReadonlyMap<number, string>;
  label: string;
}): ReactNode {
  return (
    <ul className="rows" aria-label={label}>
      {lots.map((l) => {
        const pill = EXPIRY_PILL[l.display];
        const lotUrl = odooRecordUrl(origin, 'stock.lot', l.lotId);
        return (
          <li key={l.alertId}>
            <div>
              <StatusPill status={pill.status} label={pill.label} /> <span className="row-title">{l.productName}</span>
            </div>
            <dl>
              <dt>Sisa umur</dt>
              <dd>{fmtDaysLeft(l.daysLeft)}</dd>
              <dt>Qty</dt>
              <dd>{fmtQty(l.qty)}</dd>
              <dt>Nilai (HPP)</dt>
              <dd>{fmtIdr(l.valueCost)}</dd>
              <dt>Markdown</dt>
              <dd>{markdownText(l)}</dd>
              <dt>Keterangan</dt>
              <dd>{expiryReason(l.display, l.daysLeft, l.valueCost)}</dd>
              {l.lotName ? (
                <>
                  <dt>Lot</dt>
                  <dd>{l.lotName}</dd>
                </>
              ) : null}
              {whNames ? (
                <>
                  <dt>Outlet</dt>
                  <dd>{whNames.get(l.warehouseId) ?? `Outlet #${l.warehouseId}`}</dd>
                </>
              ) : null}
            </dl>
            {lotUrl ? <ActionLink label="Buka lot di Odoo" href={lotUrl} /> : null}
          </li>
        );
      })}
    </ul>
  );
}

export function CriticalRows({
  rows,
  origin,
  whNames,
  label,
}: {
  rows: readonly CriticalRow[];
  origin: string | null;
  whNames?: ReadonlyMap<number, string>;
  label: string;
}): ReactNode {
  return (
    <ul className="rows" aria-label={label}>
      {rows.map((r) => {
        const out = r.isOut;
        const url = odooRecordUrl(origin, 'product.template', r.productTmplId);
        return (
          <li key={`${r.warehouseId}-${r.productId}`}>
            <div>
              <StatusPill status={out ? 'bad' : 'warn'} label={out ? 'Habis' : 'Kritis'} /> <span className="row-title">{r.name}</span>
            </div>
            <dl>
              <dt>Stok</dt>
              <dd>
                {fmtQty(r.onHand)} (minimum {fmtQty(r.minQty)})
              </dd>
              {r.cover !== null ? (
                <>
                  <dt>Cakupan</dt>
                  <dd>{fmtCover(r.cover)}</dd>
                </>
              ) : null}
              <dt>Keterangan</dt>
              <dd>
                {out
                  ? `Stok habis (0 dari minimum ${fmtQty(r.minQty)}): penjualan hilang sampai ada pengisian.`
                  : `Stok ${fmtQty(r.onHand)} di bawah minimum ${fmtQty(r.minQty)}${r.cover !== null ? `; cakupan ${fmtCover(r.cover)}` : ''}.`}
              </dd>
              <dt>Usulan order</dt>
              <dd>{r.openProposalId !== null ? 'Sudah di usulan order terdekat' : 'Belum ada di usulan'}</dd>
              {whNames ? (
                <>
                  <dt>Outlet</dt>
                  <dd>{whNames.get(r.warehouseId) ?? `Outlet #${r.warehouseId}`}</dd>
                </>
              ) : null}
            </dl>
            {url ? <ActionLink label="Buka produk di Odoo" href={url} /> : null}
          </li>
        );
      })}
    </ul>
  );
}
