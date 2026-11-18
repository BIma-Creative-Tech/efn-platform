import type { Metadata } from 'next';
import { InfoPage } from '@/components/InfoPage';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Sesi berakhir' };

export default function SessionEndedPage() {
  return <InfoPage title="Sesi berakhir">Sesi Anda sudah berakhir atau belum dimulai. Buka lagi dari Odoo untuk melanjutkan.</InfoPage>;
}
