import Link from "next/link";

export default function NotFound() {
  return (
    <main className="shell">
      <section className="panel">
        <h1>Halaman tidak ditemukan</h1>
        <p className="lead">
          <Link href="/">Kembali ke halaman masuk</Link>
        </p>
      </section>
    </main>
  );
}
