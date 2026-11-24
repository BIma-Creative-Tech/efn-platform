import type { Metadata } from 'next';
import { InfoPage } from '@/components/InfoPage';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Production Order belum aktif' };

/** INSIGHT_PRODPLAN_ENABLED != "true" (docs/04 §30.12): modul Production Order belum diaktifkan. Tanpa data. */
export default function ProdplanNotEnabledPage() {
  return (
    <InfoPage title="Production Order belum aktif" showOdooLink>
      Modul perencanaan produksi (Production Order) belum diaktifkan. Hubungi administrator bila Anda memerlukannya.
    </InfoPage>
  );
}
