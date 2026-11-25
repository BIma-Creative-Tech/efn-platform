import type { Metadata } from 'next';
import { InfoPage } from '@/components/InfoPage';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Segera hadir' };

/** INSIGHT_G68_ENABLED != "true" (docs/04 §32.8): Laba Rugi Outlet, Waste & Pemakaian Internal, Fill Rate belum diaktifkan. Tanpa data. */
export default function G68NotEnabledPage() {
  return (
    <InfoPage title="Halaman ini segera hadir" showOdooLink>
      Halaman Laba Rugi Outlet, Waste &amp; Pemakaian Internal, dan Fill Rate Outlet ke CK belum diaktifkan. Hubungi administrator bila Anda memerlukannya.
    </InfoPage>
  );
}
