import type { Metadata } from 'next';
import { FreshnessBadge } from '@/components/FreshnessBadge';
import { FilterBar } from '@/components/FilterBar';
import { Shell } from '@/components/Shell';
import { requireSession } from '@/lib/auth';
import { codeName } from '@/lib/brands';
import { esbCodesOf } from '@/lib/claims';
import { odooOpenUrl } from '@/lib/config';
import { fmtAge } from '@/lib/format';
import { getConfig } from '@/lib/runtime';
import { loadShellData } from '@/lib/shell-data';
import { navGroups, smFilterLinks } from '@/lib/sm-personas';
import { freshnessStatus } from '@/lib/thresholds';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = { title: { default: 'EFN Insight', template: '%s - EFN Insight' } };

/**
 * Cangkang halaman F&B (ESB). Sesi tanpa kode ESB (mis. kepala toko SMD) tidak memicu query ESB sama sekali: halaman
 * ESB mengalihkan ke beranda persona (lib/page-ctx.ts), layout hanya menampilkan cangkang kosong.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();
  const cfg = getConfig();
  const esbCodes = esbCodesOf(session.codes);
  const odoo = odooOpenUrl(cfg);
  const groups = navGroups(session, cfg.smEnabled);

  if (esbCodes.length === 0) {
    return (
      <Shell session={session} groups={groups} odooUrl={odoo} roleLabel="Supermarket" footNote="Data: EFN Insight supermarket">
        {children}
      </Shell>
    );
  }

  const shell = await loadShellData(session);
  const status = freshnessStatus(shell.ageHours);
  const roleLabel = session.roles.includes('all_brands') ? 'Semua brand' : esbCodes.map(codeName).join(', ');

  return (
    <Shell
      session={session}
      groups={groups}
      odooUrl={odoo}
      roleLabel={roleLabel}
      footNote="Data: ESB Core (arsip G41)"
      headerSlot={<FreshnessBadge ageHours={shell.ageHours} rows={shell.freshness} />}
      belowHeader={
        <>
          <FilterBar
            options={shell.options}
            sessionCodes={esbCodes}
            smLinks={smFilterLinks(session, cfg.smEnabled)}
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
