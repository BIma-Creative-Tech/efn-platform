import type { Metadata } from 'next';
import { InfoPage } from '@/components/InfoPage';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Anda telah keluar' };

export default function LoggedOutPage() {
  return <InfoPage title="Anda telah keluar">Sesi Insight sudah ditutup. Buka lagi dari Odoo untuk masuk kembali.</InfoPage>;
}
