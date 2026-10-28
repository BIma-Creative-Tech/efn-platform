import type { Metadata } from 'next';
import Link from 'next/link';
import { getBrand } from '@/lib/brand-server';
import { parseGraceDays } from '@/lib/dsar';

export const metadata: Metadata = { title: 'Kebijakan Privasi' };

/**
 * PLACEHOLDER kebijakan privasi (UU PDP 27/2022). DRAFT — WAJIB ditinjau DPO/Legal sebelum go-live.
 * Masa simpan = retensi default usulan docs/04 §8.4 (configurable di server, MENUNGGU DPO), bukan keputusan.
 */
export default async function PrivasiPage() {
  const brand = await getBrand();
  const graceDays = parseGraceDays(process.env.DSAR_DELETE_GRACE_DAYS);
  return (
    <article className="card stack" style={{ maxWidth: '48rem', margin: '0 auto' }}>
      <p className="notice" role="note">
        <strong>DRAFT — menunggu tinjauan DPO/Legal.</strong> Teks ini belum berlaku sebagai kebijakan resmi. Nama
        badan hukum, kontak DPO, daftar pemroses data, dan masa simpan masih usulan.
      </p>

      <h1 style={{ marginTop: 0 }}>Kebijakan Privasi {brand.name}</h1>
      <p>
        {brand.name} adalah bagian dari grup EFN. Kebijakan ini menjelaskan data pribadi apa yang kami proses, untuk apa,
        berapa lama disimpan, dan hak Anda sesuai Undang-Undang No. 27 Tahun 2022 tentang Pelindungan Data Pribadi.
      </p>

      <h2>Data yang kami proses</h2>
      <ul>
        <li>
          <strong>Nomor HP</strong> — untuk masuk (kode OTP via WhatsApp) dan sebagai identitas akun poin. Disimpan
          terenkripsi; di catatan sistem nomor disamarkan (mis. 62812****890).
        </li>
        <li>
          <strong>Saldo dan riwayat poin</strong> — untuk program loyalitas di seluruh brand EFN.
        </li>
        <li>
          <strong>Laporan Pantau Harga</strong> — foto label harga, nama produk, harga, nama toko (opsional). Metadata
          foto (termasuk GPS kamera) dihapus sebelum disimpan.
        </li>
        <li>
          <strong>Lokasi perkiraan</strong> — hanya bila Anda mengaktifkan persetujuan lokasi di{' '}
          <Link href="/akun/privasi">Pengaturan Privasi</Link> <em>dan</em> memilih menyertakannya pada laporan;
          dibulatkan sekitar 11 meter dan tidak dilacak terus-menerus.
        </li>
        <li>
          <strong>Catatan persetujuan</strong> — tujuan, versi teks, dan waktu Anda memberi atau menarik persetujuan,
          sebagai bukti.
        </li>
        <li>
          <strong>Data teknis</strong> — alamat IP dan log akses untuk keamanan dan pencegahan penyalahgunaan.
          Verifikasi &quot;Saya bukan robot&quot; dihitung di perangkat Anda (tanpa layanan pihak ketiga dan tanpa
          pelacakan).
        </li>
      </ul>

      <h2>Tujuan dan dasar pemrosesan</h2>
      <ul>
        <li>Autentikasi dan keamanan akun; pesan layanan (OTP, status laporan, poin) — pelaksanaan perjanjian.</li>
        <li>Pemberian dan penukaran poin loyalitas — pelaksanaan perjanjian.</li>
        <li>Validasi laporan harga dan analisis harga pasar (agregat, tanpa identitas Anda).</li>
        <li>
          Lokasi perkiraan dan info promo via WhatsApp &amp; notifikasi aplikasi — hanya dengan persetujuan Anda, terpisah
          per tujuan.
        </li>
        <li>
          Saat masuk/daftar Anda menyetujui Kebijakan Privasi ini secara terpisah (wajib); versi teks dan waktunya kami
          catat sebagai bukti. Untuk menariknya, ajukan penghapusan akun di <Link href="/akun/data-saya">Data Saya</Link>.
        </li>
        <li>Pencegahan kecurangan dan pemenuhan kewajiban hukum (mis. perpajakan).</li>
      </ul>

      <h2>Masa simpan (usulan default — menunggu DPO)</h2>
      <ul>
        <li>Kode OTP: maksimal 3 menit, lalu dihapus otomatis.</li>
        <li>Catatan audit OTP (tanpa nomor HP mentah): 90 hari.</li>
        <li>Foto laporan harga: 90 hari sejak laporan dibuat.</li>
        <li>Keterkaitan laporan harga dengan akun Anda: dilepas setelah 90 hari; isi laporan (tanpa identitas) 2 tahun.</li>
        <li>Data analitik turunan (agregat, tanpa nomor HP): 5 tahun.</li>
        <li>Riwayat poin: 10 tahun (catatan keuangan); identitas dilepas bila akun dihapus.</li>
        <li>Catatan persetujuan: selama akun aktif ditambah 5 tahun, sebagai bukti.</li>
        <li>
          Cadangan (backup) terenkripsi: data terhapus ikut hilang dari cadangan saat siklus retensi cadangan berakhir.
        </li>
      </ul>

      <h2>Hak Anda</h2>
      <ul>
        <li>
          Mengakses dan memperoleh salinan data pribadi Anda — <Link href="/akun/data-saya">Data Saya</Link>.
        </li>
        <li>Memperbaiki data yang tidak akurat.</li>
        <li>
          Menarik persetujuan (lokasi, info promo) kapan saja tanpa memengaruhi pemrosesan sebelumnya —{' '}
          <Link href="/akun/privasi">Pengaturan Privasi</Link>.
        </li>
        <li>
          Meminta penghapusan akun — <Link href="/akun/data-saya">Data Saya</Link>. Penghapusan memerlukan verifikasi
          kode OTP, dijalankan setelah masa tenggang {graceDays} hari (dapat dibatalkan selama masa itu), dan{' '}
          <strong>sisa poin hangus</strong>. Data yang wajib disimpan menurut peraturan (mis. catatan transaksi untuk
          pajak) tetap disimpan tanpa identitas Anda.
        </li>
      </ul>
      <p>
        Permintaan lain dapat diajukan ke: <strong>[kontak DPO / email privasi — belum ditetapkan]</strong>. Kami menanggapi
        dalam batas waktu yang diatur peraturan.
      </p>

      <h2>Berbagi data</h2>
      <p>
        Data diproses di dalam grup EFN (PT holding, PT Retail, PT F&amp;B) sesuai kebutuhan layanan, serta oleh
        penyedia layanan pihak ketiga (mis. pengiriman pesan WhatsApp) berdasarkan perjanjian pemrosesan data.{' '}
        <strong>[Daftar pemroses data menunggu legal.]</strong>
      </p>

      <p className="small muted">Versi DRAFT (Gelombang 5, Okt 2026). Terakhir diubah: belum berlaku.</p>
    </article>
  );
}
