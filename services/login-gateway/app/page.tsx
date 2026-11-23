import Image from 'next/image';
import { loadConfig, type Destination } from '@/lib/config';

// Env dibaca saat request (bukan saat build), dan CSP bernonce butuh render dinamis.
export const dynamic = 'force-dynamic';

const DESTINATION_COPY: Record<Destination, { title: string; desc: string }> = {
  erp: { title: 'ERP (Odoo)', desc: 'Penjualan, stok, produksi, dan keuangan.' },
  insight: { title: 'Insight', desc: 'Dashboard analitik penjualan dan operasional.' },
};

const BRANDS = [
  { src: '/brand/parisbaguette.png', alt: 'Paris Baguette', w: 120, h: 40 },
  { src: '/brand/grandlucky.png', alt: 'Grand Lucky', w: 120, h: 40 },
  { src: '/brand/bacha.svg', alt: 'Bacha Coffee', w: 120, h: 40 },
  { src: '/brand/wetzels.svg', alt: "Wetzel's Pretzels", w: 120, h: 40 },
];

export default function HomePage() {
  const config = loadConfig();
  return (
    <main className="shell">
      <section className="panel" aria-labelledby="judul">
        <Image className="logo" src="/brand/ebn.png" alt="EFN" width={160} height={64} priority />
        <h1 id="judul">Masuk untuk staf</h1>
        <p className="lead">Pilih tujuan. Anda akan diarahkan ke halaman masuk sistem terkait.</p>
        <ul className="dest-list">
          {(Object.keys(DESTINATION_COPY) as Destination[]).map((dest) => {
            const copy = DESTINATION_COPY[dest];
            const enabled = config[dest].enabled;
            return (
              <li key={dest}>
                {enabled ? (
                  <a className="dest" href={`/go/${dest}`}>
                    <span className="dest-title">{copy.title}</span>
                    <span className="dest-desc">{copy.desc}</span>
                  </a>
                ) : (
                  <div className="dest dest-off" aria-disabled="true">
                    <span className="dest-title">{copy.title}</span>
                    <span className="dest-desc">Belum tersedia. Hubungi administrator.</span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        <p className="note">Halaman ini tidak meminta kata sandi. Kata sandi hanya dimasukkan di halaman sistem tujuan.</p>
        <ul className="brands" aria-label="Brand EFN">
          {BRANDS.map((b) => (
            <li key={b.src}>
              <Image src={b.src} alt={b.alt} width={b.w} height={b.h} />
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
