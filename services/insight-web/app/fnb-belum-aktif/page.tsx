import type { Metadata } from 'next';
import { InfoPage } from '@/components/InfoPage';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Dashboard F&B belum aktif' };

/** INSIGHT_FNB_PERSONA_ENABLED != "true": halaman Eksekutif/Toko/Marketing F&B belum diaktifkan (docs/04 §28.15.10). Tanpa data. */
export default function FnbNotEnabledPage() {
  return (
    <InfoPage title="Dashboard F&B untuk peran Anda belum aktif" showOdooLink>
      Dashboard Eksekutif, Toko, dan Marketing untuk F&B belum diaktifkan. Hubungi administrator bila Anda memerlukannya.
    </InfoPage>
  );
}
