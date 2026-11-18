import { InfoPage } from '@/components/InfoPage';

export default function NotFound() {
  return (
    <InfoPage title="Halaman tidak ditemukan" showOdooLink={false}>
      Halaman yang Anda cari tidak ada. Gunakan menu di sisi kiri setelah masuk.
    </InfoPage>
  );
}
