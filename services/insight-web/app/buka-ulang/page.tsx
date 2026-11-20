import type { Metadata } from 'next';
import { InfoPage } from '@/components/InfoPage';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Buka ulang dari Odoo' };

/** Sesi lama (eis1) tidak membawa persona/outlet; halaman supermarket butuh sesi eis2 (docs/04 §28.13.5). */
export default function ReopenPage() {
  return (
    <InfoPage title="Buka ulang Insight dari Odoo" showOdooLink>
      Sesi Anda dibuat sebelum dashboard supermarket tersedia. Buka ulang Insight dari Odoo agar peran dan outlet Anda terbaca.
    </InfoPage>
  );
}
