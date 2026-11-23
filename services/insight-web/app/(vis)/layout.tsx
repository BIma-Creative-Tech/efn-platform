import type { Metadata } from 'next';
import { Shell } from '@/components/Shell';
import { requireSession } from '@/lib/auth';
import { codeName } from '@/lib/brands';
import { odooHomeUrl } from '@/lib/config';
import { getConfig } from '@/lib/runtime';
import { navGroups } from '@/lib/sm-personas';
import { visitorCodes, withVisitorNav } from '@/lib/visitor';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = { title: { default: 'EFN Insight - Pengunjung', template: '%s - EFN Insight' } };

/**
 * Cangkang halaman Pengunjung lintas PT (V-01). Tanpa FilterBar global F&B: halaman memegang filter sendiri (periode, PT, kanal)
 * karena PT yang boleh dipilih = kode pengunjung sesi (ESB + SMD), bukan daftar filter F&B.
 */
export default async function VisitorLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();
  const cfg = getConfig();
  const codes = visitorCodes(session, cfg.smEnabled);
  return (
    <Shell
      session={session}
      groups={withVisitorNav(navGroups(session, cfg.smEnabled, cfg.fnbEnabled), session, cfg.smEnabled)}
      odooUrl={odooHomeUrl(cfg)}
      roleLabel={codes.length > 0 ? codes.map(codeName).join(', ') : 'EFN Insight'}
      footNote="Data: log kunjungan Super-App (ETL tiap 15 menit, agregat per jam)"
    >
      {children}
    </Shell>
  );
}
