import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { isConsentActive } from '@/lib/consent';
import { loadConsents } from '@/lib/consent-server';
import { getSessionToken } from '@/lib/session';
import { PriceReportForm } from './PriceReportForm';

export const metadata: Metadata = { title: 'Pantau Harga' };
export const dynamic = 'force-dynamic';

/**
 * Price Hunter / Market Scout (docs/04 §3.3). Wajib login: laporan terkait akun untuk poin.
 * Lokasi hanya DITAWARKAN bila consent `location` aktif (docs/04 §8.4). Gagal memuat consent -> tidak
 * ditawarkan (fail-closed). BFF tetap membuang koordinat bila consent tidak aktif.
 */
export default async function PantauHargaPage() {
  const token = await getSessionToken();
  if (!token) redirect('/masuk?next=/pantau-harga');

  const consents = await loadConsents(token);
  if (consents.status === 'unauthenticated') redirect('/masuk?next=/pantau-harga&expired=1');
  const locationConsent = consents.status === 'ok' && isConsentActive(consents.records, 'location');

  return (
    <div className="stack" style={{ maxWidth: '36rem', margin: '0 auto' }}>
      <h1>Pantau Harga</h1>
      <p className="muted">
        Temukan harga lebih murah di toko lain? Foto label harganya dan kirim ke kami. Laporan diperiksa otomatis
        (bisa beberapa menit); bila valid, poin masuk ke akun Anda dan Anda menerima notifikasi.
      </p>
      <PriceReportForm locationConsent={locationConsent} />
      <p className="small muted">
        Foto dikecilkan dan metadata kamera (termasuk lokasi GPS di foto) dihapus di perangkat Anda sebelum dikirim.
        Lihat <Link href="/privasi">Kebijakan Privasi</Link>.
      </p>
    </div>
  );
}
