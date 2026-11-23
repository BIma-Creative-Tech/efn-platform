import type { Metadata } from 'next';
import { FreshnessBadge } from '@/components/FreshnessBadge';
import { FilterBar } from '@/components/FilterBar';
import { Shell } from '@/components/Shell';
import { requireSession } from '@/lib/auth';
import { codeName } from '@/lib/brands';
import { esbCodesOf } from '@/lib/claims';
import { odooHomeUrl } from '@/lib/config';
import { fnbStoreChip } from '@/lib/fnb-personas';
import { fmtAge } from '@/lib/format';
import { getConfig } from '@/lib/runtime';
import { loadShellData } from '@/lib/shell-data';
import { navGroups, smdAllowed } from '@/lib/sm-personas';
import { freshnessStatus } from '@/lib/thresholds';
import { withVisitorNav } from '@/lib/visitor';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = { title: { default: 'EFN Insight', template: '%s - EFN Insight' } };

/**
 * Cangkang halaman F&B (ESB). Sesi tanpa kode ESB dan tanpa hak SMD (mis. kepala toko SMD) tidak memicu query ESB sama sekali:
 * halaman ESB mengalihkan ke beranda persona (lib/page-ctx.ts), layout hanya menampilkan cangkang kosong.
 * G51 B1 (docs/04 §28.14.1): sesi exec ber-SMD memakai filter Brand / PT dengan SMD sebagai toggle; tanpa PT F&B, SMD terpilih bawaan.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();
  const cfg = getConfig();
  const esbCodes = esbCodesOf(session.codes);
  const smd = smdAllowed(session, cfg.smEnabled);
  const odoo = odooHomeUrl(cfg);
  const groups = withVisitorNav(navGroups(session, cfg.smEnabled, cfg.fnbEnabled), session, cfg.smEnabled);

  // G52 (§28.15.9): sesi ber-cabang (kepala toko F&B) = cangkang minimal. Tanpa FilterBar global dan tanpa kesegaran tingkat PT:
  // query itu memakai view kelas PT yang memang kosong untuk sesi ber-cabang (halaman Toko memegang hari + cabang sendiri).
  if (session.brs.length > 0) {
    return (
      <Shell
        session={session}
        groups={groups}
        odooUrl={odoo}
        roleLabel={fnbStoreChip(session.brs, new Map())}
        footNote="Data: ESB Core (arsip G41)"
      >
        {children}
      </Shell>
    );
  }

  if (esbCodes.length === 0 && !smd) {
    return (
      <Shell session={session} groups={groups} odooUrl={odoo} roleLabel="Supermarket" footNote="Data: EFN Insight supermarket">
        {children}
      </Shell>
    );
  }

  const shell = await loadShellData(session, new Date(), smd);
  const esbPresent = esbCodes.length > 0;
  const status = esbPresent ? freshnessStatus(shell.ageHours) : 'good';
  const roleLabel = !esbPresent
    ? codeName('SMD')
    : session.roles.includes('all_brands')
      ? 'Semua brand'
      : esbCodes.map(codeName).join(', ');
  const sessionCodes: string[] = smd ? [...esbCodes, 'SMD'] : [...esbCodes];

  return (
    <Shell
      session={session}
      groups={groups}
      odooUrl={odoo}
      roleLabel={roleLabel}
      footNote={esbPresent ? 'Data: ESB Core (arsip G41)' + (smd ? '; supermarket: EFN Insight' : '') : 'Data: EFN Insight supermarket'}
      headerSlot={esbPresent ? <FreshnessBadge ageHours={shell.ageHours} rows={shell.freshness} /> : undefined}
      belowHeader={
        <>
          <FilterBar
            options={shell.options}
            sessionCodes={sessionCodes}
            esbCodes={esbCodes}
            smdAllowed={smd}
            defaults={shell.defaults}
            yesterday={shell.yesterday}
          />
          {status === 'warn' || status === 'bad' || status === 'unknown' ? (
            <div className={`banner banner-${status}`} role="status">
              {shell.ageHours === null
                ? 'Kesegaran data tidak diketahui. Angka mungkin belum mencakup kemarin.'
                : `Data terakhir diperbarui ${fmtAge(shell.ageHours)}. Angka mungkin belum mencakup kemarin.`}
            </div>
          ) : null}
        </>
      }
    >
      {children}
    </Shell>
  );
}
