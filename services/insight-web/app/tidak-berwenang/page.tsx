import type { Metadata } from 'next';
import Link from 'next/link';
import { InfoPage } from '@/components/InfoPage';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Halaman untuk peran lain' };

/** Persona tidak ada di `roles` sesi (docs/04 §28.13.11; Q-INS-S9 default: tidak). Halaman ramah, bukan 404. */
export default function ForbiddenPersonaPage() {
  return (
    <InfoPage title="Halaman ini untuk peran lain" showOdooLink={false}>
      Halaman ini untuk peran lain. Hubungi admin bila Anda memerlukannya. <Link href="/">Kembali ke beranda</Link>.
    </InfoPage>
  );
}
