/**
 * /flow: "Sell-through per jam" dari ESB OMS (G69). Server component. Disembunyikan rapi bila view OMS belum ada/kosong untuk hari itu.
 * Pasokan dari mart Core (arus barang hari yang sama); terjual per jam dari OMS (resep menu -> produk FIN).
 */
import type { ReactNode } from 'react';
import { ErrorCard } from '@/components/blocks';
import { SmTable, type SmColumn } from '@/components/sm/SmTable';
import { EmptyState, Section } from '@/components/ui';
import { fmtDate, fmtPct, fmtQty } from '@/lib/format';
import type { Result } from '@/lib/loaders';
import {
  buildSellThrough,
  hourLabel,
  freshnessLabel,
  hourRangeLabel,
  PATOK_HOURS,
  unmappedSharePct,
  type FinHourlyRaw,
  type FreshnessRaw,
  type QualityRaw,
  type SellThroughRow,
  type SupplyRaw,
} from '@/lib/oms-vm';

const pct = (v: number | null): string => (v === null ? 'n/a' : fmtPct(v));
const uomNote = (r: SellThroughRow): string => `satuan beda (${r.uomMismatch === '' ? 'uom' : r.uomMismatch})`;

export function SellThroughSection({
  day,
  oneBranch,
  freshR,
  finR,
  supplyR,
  qualityR,
}: {
  day: string;
  /** Tepat satu cabang terpilih; selain itu hanya petunjuk (bila OMS aktif pada cakupan). */
  oneBranch: boolean;
  freshR: Result<FreshnessRaw[]> | null;
  finR: Result<FinHourlyRaw[]> | null;
  supplyR: Result<SupplyRaw[]> | null;
  qualityR: Result<QualityRaw[]> | null;
}): ReactNode {
  if (!oneBranch) {
    // Petunjuk hanya bila OMS terbukti aktif (ada tarikan hari ini) pada cakupan; selain itu tersembunyi rapi.
    if (!freshR || !freshR.ok || freshnessLabel(freshR.data) === null) return null;
    return (
      <Section title="Sell-through per jam" id="oms-st">
        <EmptyState title="Pilih satu outlet untuk melihat sell-through per jam." reason="Pasokan dan penjualan hanya sebanding per outlet. Pilih tepat satu cabang pada filter Cabang." icon="info" />
      </Section>
    );
  }
  if (!finR || !supplyR || !qualityR) return null;
  if (!finR.ok) {
    return finR.reason === 'rate_limited' ? (
      <Section title="Sell-through per jam" id="oms-st">
        <ErrorCard title="Sell-through per jam" refId={finR.ref} reason={finR.reason} />
      </Section>
    ) : null;
  }
  const rows = buildSellThrough(finR.data, supplyR.ok ? supplyR.data : []);
  if (rows.length === 0) return null;

  const unmapped = qualityR.ok ? unmappedSharePct(qualityR.data) : null;
  const cols: SmColumn<SellThroughRow>[] = [
    { header: 'Produk FIN', cell: (r) => r.label, text: (r) => r.label },
    { header: 'Pasokan', align: 'right', cell: (r) => (r.uomMismatch !== null ? uomNote(r) : r.supply === null ? 'n/a' : fmtQty(r.supply)) },
    { header: 'Terjual', align: 'right', cell: (r) => (r.uomMismatch !== null ? `${fmtQty(r.sold)} ${r.uomMismatch}` : fmtQty(r.sold)) },
    ...PATOK_HOURS.map((h): SmColumn<SellThroughRow> => ({ header: `% terjual ${hourLabel(h)}`, align: 'right', cell: (r) => (r.uomMismatch !== null ? 'n/a' : pct(r.pctAt[h])) })),
    { header: '% terjual tutup', align: 'right', cell: (r) => (r.uomMismatch !== null ? 'n/a' : pct(r.pctClose)) },
    {
      header: 'Perkiraan jam habis',
      align: 'right',
      cell: (r) =>
        r.uomMismatch !== null
          ? 'n/a'
          : r.supply === null || !(r.supply > 0)
            ? 'n/a'
            : r.soldOutHour === null
              ? 'Tidak habis'
              : hourRangeLabel(r.soldOutHour),
    },
  ];

  return (
    <Section title="Sell-through per jam" id="oms-st">
      <p className="muted small">
        Hari {fmtDate(day)} (hari terakhir pada filter periode), satu outlet. Hanya sampai kemarin: pasokan ESB Core baru tersedia keesokan harinya, jadi hari
        berjalan tidak ditampilkan di sini (lihat Toko F&B untuk penjualan per jam hari ini). Produk yang satuan resepnya bukan EA ditandai &ldquo;satuan beda&rdquo;
        dan tidak dihitung persen/jam habisnya. Pasokan = diterima + produksi toko + transfer masuk - transfer keluar (arus barang
        ESB Core); terjual per jam dari struk ESB OMS (waktu struk ditutup, WIB). &ldquo;% terjual 12:00&rdquo; = bagian pasokan yang sudah terjual sebelum pukul
        12:00. Jam habis = jam pertama saat kumulatif terjual mencapai pasokan; &ldquo;Tidak habis&rdquo; bila belum tercapai sampai tutup. Persen bisa melebihi
        100% bila stok awal toko tidak tercatat di pasokan.
      </p>
      {unmapped !== null && unmapped >= 0.05 ? (
        <p className="notice" role="note">
          Porsi tak terpetakan: sekitar {fmtPct(unmapped)} qty menu pada hari itu belum bisa dipetakan ke produk FIN lewat resep, sehingga angka terjual per produk di
          bawah bisa lebih rendah dari kenyataan.
        </p>
      ) : null}
      <SmTable caption={`Sell-through per jam per produk FIN pada ${fmtDate(day)}, urut terjual terbanyak`} columns={cols} rows={rows} rowKey={(r) => r.key} />
    </Section>
  );
}
