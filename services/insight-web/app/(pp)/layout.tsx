import type { Metadata } from 'next';
import { EnvBanner } from '@/components/pp/EnvBanner';
import { Shell } from '@/components/Shell';
import { odooHomeUrl } from '@/lib/config';
import { requirePpSession } from '@/lib/pp-page';
import { ppRoleLabel } from '@/lib/prodplan-access';
import { loadMeta } from '@/lib/prodplan-data';
import { getConfig } from '@/lib/runtime';
import { navGroups } from '@/lib/sm-personas';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = { title: { default: 'Production Order - EFN Insight', template: '%s - EFN Insight' } };

/**
 * Cangkang modul Production Order (docs/04 §30.12, G66). Tidak memuat data ClickHouse: semua data lewat API prodplan.
 * Banner lingkungan ESB (STAGING/PRODUCTION) + status tulis tampil di setiap halaman modul.
 */
export default async function ProdLayout({ children }: { children: React.ReactNode }) {
  const { session } = await requirePpSession();
  const cfg = getConfig();
  const meta = await loadMeta(session);
  return (
    <Shell
      session={session}
      groups={navGroups(session, cfg.smEnabled, cfg.fnbEnabled, cfg.ppEnabled)}
      odooUrl={odooHomeUrl(cfg)}
      roleLabel={ppRoleLabel(session.roles)}
      footNote="Data: ESB Core melalui layanan perencanaan produksi"
      belowHeader={<EnvBanner meta={meta.ok ? meta.data : null} errorText={meta.ok ? undefined : meta.error.message} />}
    >
      {children}
    </Shell>
  );
}
