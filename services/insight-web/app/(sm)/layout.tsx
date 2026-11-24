import type { Metadata } from 'next';
import { Shell } from '@/components/Shell';
import { requireSession } from '@/lib/auth';
import { personasOf } from '@/lib/claims';
import { odooHomeUrl } from '@/lib/config';
import { getConfig } from '@/lib/runtime';
import { navGroups, PERSONA_LABEL } from '@/lib/sm-personas';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = { title: { default: 'EFN Insight - Supermarket', template: '%s - EFN Insight' } };

/** Cangkang halaman supermarket per persona (docs/04 §28.13.11). Tidak memuat data ESB; data kartu dimuat tiap halaman. */
export default async function SmLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();
  const cfg = getConfig();
  const roleLabel = personasOf(session.roles).map((p) => PERSONA_LABEL[p]).join(', ') || 'Supermarket';
  return (
    <Shell
      session={session}
      groups={navGroups(session, cfg.smEnabled, cfg.fnbEnabled, cfg.ppEnabled, cfg.g68Enabled)}
      odooUrl={odooHomeUrl(cfg)}
      roleLabel={roleLabel}
      footNote="Data: mart supermarket (ETL tiap 15 menit)"
    >
      {children}
    </Shell>
  );
}
