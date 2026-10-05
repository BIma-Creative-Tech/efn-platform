import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="card stack" style={{ maxWidth: '28rem', margin: '0 auto' }}>
      <h1 style={{ marginTop: 0 }}>Halaman tidak ditemukan</h1>
      <p>Halaman yang Anda cari tidak ada atau Anda tidak memiliki akses.</p>
      <p>
        <Link href="/">Kembali ke beranda</Link>
      </p>
    </div>
  );
}
