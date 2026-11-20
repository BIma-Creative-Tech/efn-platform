import type { Metadata } from 'next';
import { InfoPage } from '@/components/InfoPage';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Supermarket belum aktif' };

/** INSIGHT_SM_ENABLED != "true": halaman supermarket belum diaktifkan (docs/04 §28.13.12). Tanpa data. */
export default function SmNotEnabledPage() {
  return (
    <InfoPage title="Dashboard supermarket belum aktif" showOdooLink>
      Dashboard supermarket belum diaktifkan. Hubungi administrator bila Anda memerlukannya.
    </InfoPage>
  );
}
