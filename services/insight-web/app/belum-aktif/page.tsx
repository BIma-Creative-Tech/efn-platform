import type { Metadata } from 'next';
import { InfoPage } from '@/components/InfoPage';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Belum diaktifkan' };

export default function NotEnabledPage() {
  return (
    <InfoPage title="Insight belum diaktifkan" showOdooLink={false}>
      Dashboard ini belum diaktifkan atau belum dikonfigurasi. Hubungi administrator.
    </InfoPage>
  );
}
