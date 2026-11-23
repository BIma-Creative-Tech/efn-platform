import type { Metadata } from 'next';
import Link from 'next/link';
import { TrackingSettings } from '@/components/engage/TrackingSettings';

export const metadata: Metadata = { title: 'Pengaturan Pelacakan' };

/**
 * Pengaturan pelacakan aktivitas (G50; ADR-047). Publik: pilihan perangkat berlaku tanpa login; consent akun hanya untuk
 * member login. Halaman tetap bisa dibuka bila fitur mati (menampilkan penjelasan, bukan galat).
 */
export default function PelacakanPage() {
  return (
    <div className="stack" style={{ maxWidth: '40rem', margin: '0 auto' }}>
      <p>
        <Link href="/privasi">&larr; Kebijakan Privasi</Link>
      </p>
      <h1>Pengaturan Pelacakan</h1>
      <p className="notice small" role="note">
        <strong>DRAFT</strong>: teks persetujuan menunggu tinjauan DPO/Legal.
      </p>
      <p className="muted">
        Rekomendasi dan promo personal (pelacakan yang dikaitkan dengan Anda) bersifat pilihan. Menolak atau mematikannya tidak
        mengurangi layanan belanja, poin, atau pesanan Anda. Anda bisa mengubah pilihan ini kapan saja. Bila fitur kunjungan anonim
        aktif, kunjungan tetap dicatat tanpa identitas; penjelasannya tampil di bawah.
      </p>
      <TrackingSettings />
    </div>
  );
}
