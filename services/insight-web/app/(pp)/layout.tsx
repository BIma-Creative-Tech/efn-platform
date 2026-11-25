import type { Metadata } from 'next';
import { EnvBanner } from '@/components/pp/EnvBanner';
import { SessionClock } from '@/components/pp/SessionClock';
import { Shell } from '@/components/Shell';
import { PP_NAV_ITEMS, ppRoleLabel } from '@/lib/prodplan-access';
import { redirectIfExpired, requirePpSession } from '@/lib/pp-page';
import { loadMeta } from '@/lib/prodplan-data';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = { title: { default: 'Production Order - EFN Insight', template: '%s - EFN Insight' } };

/**
 * Cangkang modul Production Order (docs/04 §30.12 + §30.17.2, G66). Gerbang = sesi ESB (`__Host-epp`), bukan sesi Odoo.
 * Header: nama lengkap ESB, brand, peran, sisa waktu sesi, tombol Keluar; TANPA navigasi ke halaman Insight lain.
 * Tidak memuat data ClickHouse: semua data lewat API prodplan. Banner lingkungan ESB (STAGING/PRODUCTION) + status tulis
 * tampil di setiap halaman modul.
 */
export default async function ProdLayout({ children }: { children: React.ReactNode }) {
  const { sid, view } = await requirePpSession();
  const meta = await loadMeta(sid);
  redirectIfExpired(meta);
  const who = [ppRoleLabel(view.roles), view.company_code || view.esb_brand.toUpperCase()].filter(Boolean).join(' - ');
  return (
    <Shell
      session={{ name: view.name }}
      groups={[{ id: 'prod', title: 'Produksi', items: PP_NAV_ITEMS }]}
      odooUrl={null}
      roleLabel={who}
      headerSlot={<SessionClock expiresAtSec={view.expires_at_sec} />}
      logoutAction="/api/prodplan/logout"
      standalone
      footNote="Data: ESB Core melalui layanan perencanaan produksi"
      belowHeader={<EnvBanner meta={meta.ok ? meta.data : null} errorText={meta.ok ? undefined : meta.error.message} />}
    >
      {children}
    </Shell>
  );
}
