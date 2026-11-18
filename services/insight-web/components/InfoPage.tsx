import type { ReactNode } from 'react';
import { odooOpenUrl } from '@/lib/config';
import { getConfig } from '@/lib/runtime';

/** Halaman informasi tanpa cangkang (belum aktif, sesi berakhir, keluar). Tidak memuat data apa pun. */
export function InfoPage({ title, children, showOdooLink = true }: { title: string; children: ReactNode; showOdooLink?: boolean }) {
  const odoo = odooOpenUrl(getConfig());
  return (
    <main className="info-page" id="konten">
      <h1>{title}</h1>
      <p>{children}</p>
      {showOdooLink && odoo ? (
        <p>
          <a className="btn btn-primary" href={odoo}>
            Buka dari Odoo
          </a>
        </p>
      ) : null}
    </main>
  );
}
