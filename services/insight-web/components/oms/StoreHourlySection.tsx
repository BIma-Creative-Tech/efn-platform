/**
 * /fnb/store: "Penjualan per jam & kanal" dari ESB OMS (G69). Server component. Disembunyikan rapi (tanpa kartu galat) bila view OMS
 * belum ada, belum aktif, atau tidak ada struk pada hari itu; hanya pembatasan laju yang ditampilkan sebagai kartu galat.
 */
import Link from 'next/link';
import type { ReactNode } from 'react';
import { ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { SmTable, type SmColumn } from '@/components/sm/SmTable';
import { EmptyState, KpiRow, KpiTile, Section } from '@/components/ui';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { fmtDate, fmtIdr, fmtInt, fmtPct } from '@/lib/format';
import type { Result } from '@/lib/loaders';
import { buildStoreHourly, hourLabel, type ChannelShare, type HourlyRaw, type OmsDayMode } from '@/lib/oms-vm';

export function StoreHourlySection({
  day,
  mode,
  fresh,
  links,
  hourlyR,
}: {
  /** Hari yang ditampilkan (hari ini WIB atau tanggal filter halaman). */
  day: string;
  mode: OmsDayMode;
  /** "HH:MM" tarikan terakhir; non-null berarti pilihan "Hari ini (berjalan)" tersedia. */
  fresh: string | null;
  /** Tautan pilihan hari (mempertahankan filter halaman); dipakai bila `fresh` ada. */
  links: { today: string; date: string };
  hourlyR: Result<HourlyRaw[]>;
}): ReactNode {
  if (!hourlyR.ok) {
    return hourlyR.reason === 'rate_limited' ? (
      <Section title="Penjualan per jam & kanal" id="oms">
        <ErrorCard title="Penjualan per jam & kanal" refId={hourlyR.ref} reason={hourlyR.reason} />
      </Section>
    ) : null;
  }
  const nav = fresh ? (
    <nav className="fb-row" aria-label="Pilih hari untuk penjualan per jam">
      <Link href={links.today} className={mode === 'today' ? 'btn btn-primary' : 'btn'} aria-current={mode === 'today' ? 'true' : undefined}>
        Hari ini (berjalan)
      </Link>
      <Link href={links.date} className={mode === 'date' ? 'btn btn-primary' : 'btn'} aria-current={mode === 'date' ? 'true' : undefined}>
        Tanggal filter halaman
      </Link>
    </nav>
  ) : null;
  const vm = buildStoreHourly(hourlyR.data);
  if (vm === null) {
    // Tanpa tarikan hari ini (OMS belum aktif / tanpa data) = tersembunyi. Dengan tarikan: tetap tampil agar pilihan hari tidak buntu.
    if (!fresh) return null;
    return (
      <Section title="Penjualan per jam & kanal" id="oms">
        {nav}
        <EmptyState
          title={`Belum ada struk selesai pada ${mode === 'today' ? 'hari ini' : fmtDate(day)}.`}
          reason={mode === 'today' ? 'Hari berjalan mungkin belum ada transaksi; coba lagi nanti atau pilih tanggal filter halaman.' : 'Pilih hari ini atau ubah tanggal pada filter halaman.'}
        />
      </Section>
    );
  }

  const cats = vm.hours.map(hourLabel);
  const specs: ChartSpec[] = [
    {
      id: 'oms-net',
      tabLabel: 'Penjualan bersih',
      kind: 'bar',
      fmt: 'idr',
      axisFmt: 'idr-short',
      categories: cats,
      series: [{ name: 'Penjualan bersih', data: vm.net, color: 'c1' }],
      summary: summarize('Penjualan bersih per jam', cats, vm.net, 'idr-short', true),
    },
    {
      id: 'oms-bills',
      tabLabel: 'Struk',
      kind: 'bar',
      fmt: 'int',
      categories: cats,
      series: [{ name: 'Struk', data: vm.bills, color: 'c2' }],
      summary: summarize('Struk per jam', cats, vm.bills, 'int', true),
    },
  ];
  const channelCols: SmColumn<ChannelShare>[] = [
    { header: 'Kanal (jenis kunjungan)', cell: (r) => r.name, text: (r) => r.name },
    { header: 'Struk', align: 'right', cell: (r) => fmtInt(r.bills) },
    { header: 'Penjualan bersih', align: 'right', cell: (r) => fmtIdr(r.net) },
    { header: 'Porsi', align: 'right', cell: (r) => (r.sharePct === null ? 'n/a' : fmtPct(r.sharePct)) },
  ];
  const t = vm.totals;

  return (
    <Section title="Penjualan per jam & kanal" id="oms">
      {nav}
      <p className="muted small">
        <strong>{mode === 'today' ? `Hari ini, data per ${fresh ?? '-'} WIB` : 'Data final'}</strong> ({fmtDate(day)}). Dari struk ESB OMS (jam = waktu struk
        ditutup, WIB; hanya struk selesai, bukan batal). Penjualan bersih = subtotal dikurangi diskon, sebelum PB1/service.
        {mode === 'today' ? ' Hari berjalan belum lengkap dan terus bertambah.' : ''} Angka ini bisa sedikit berbeda dari indikator di atas yang bersumber dari ESB Core.
      </p>
      <KpiRow>
        <KpiTile label="Struk" value={t.bills} fmt="int" />
        <KpiTile label="Pax (tamu)" value={t.pax} fmt="int" />
        <KpiTile label="Rata-rata nilai struk" value={t.avgBillValue} fmt="idr" />
        <KpiTile label="Item per struk" value={t.itemsPerBill} fmt="num" />
      </KpiRow>
      <ChartCard title="Penjualan per jam" subtitle="Jam tanpa struk di antara jam buka ditampilkan 0" specs={specs} wide />
      <SmTable caption={`Struk dan penjualan bersih per kanal pada ${fmtDate(day)}`} columns={channelCols} rows={vm.channels} rowKey={(r) => r.name} />
    </Section>
  );
}
