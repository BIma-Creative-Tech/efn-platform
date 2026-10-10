import Link from 'next/link';
import styles from './online.module.css';

/**
 * Tampilan ramah saat belanja online dimatikan (kill switch BFF `ONLINE_ENABLED=false` / `ONLINE_CATALOG_ENABLED=false`
 * -> 503 `online_unavailable`) atau layanan sedang gangguan. Tidak menampilkan detail teknis.
 */
export function OnlineUnavailable({ title = 'Belanja online sedang istirahat', reason }: { title?: string; reason?: 'off' | 'error' }) {
  return (
    <section className={`card stack ${styles.unavailable}`} role="status" aria-live="polite">
      <h1 style={{ margin: 0 }}>{title}</h1>
      {reason === 'error' ? (
        <p style={{ margin: 0 }}>Data belum dapat dimuat. Silakan muat ulang halaman beberapa saat lagi.</p>
      ) : (
        <p style={{ margin: 0 }}>
          Pemesanan lewat aplikasi sedang tidak tersedia untuk sementara. Anda tetap bisa berbelanja langsung di toko kami.
          Silakan coba lagi nanti.
        </p>
      )}
      <p style={{ margin: 0 }}>
        <Link href="/" className="btn btn-secondary">
          Kembali ke beranda
        </Link>
      </p>
    </section>
  );
}
