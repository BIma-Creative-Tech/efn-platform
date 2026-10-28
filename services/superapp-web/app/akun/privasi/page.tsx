import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { CONSENT_PURPOSES, CONSENT_TEXTS } from '@/lib/consent';
import { loadConsents } from '@/lib/consent-server';
import { getSessionToken } from '@/lib/session';
import { ConsentSettings } from './ConsentSettings';

export const metadata: Metadata = { title: 'Pengaturan Privasi' };
export const dynamic = 'force-dynamic';

/**
 * Pengaturan persetujuan (UU PDP; docs/04 §8.4, docs/compliance/PDP_CONSENT.md).
 * Granular per tujuan, default tidak dicentang, penarikan semudah pemberian. Teks DRAFT menunggu DPO.
 */
export default async function PrivasiAkunPage() {
  const token = await getSessionToken();
  if (!token) redirect('/masuk?next=/akun/privasi');

  const consents = await loadConsents(token);
  if (consents.status === 'unauthenticated') redirect('/masuk?next=/akun/privasi&expired=1');

  return (
    <div className="stack" style={{ maxWidth: '40rem', margin: '0 auto' }}>
      <p>
        <Link href="/akun">&larr; Akun Saya</Link>
      </p>
      <h1>Pengaturan Privasi</h1>
      <p className="notice small" role="note">
        <strong>DRAFT</strong> — teks persetujuan menunggu tinjauan DPO/Legal.
      </p>
      <p className="muted">
        Setiap persetujuan terpisah dan bisa Anda ubah kapan saja. Menarik persetujuan tidak memengaruhi pemrosesan
        yang sudah dilakukan sebelumnya. Pesan layanan (kode OTP, status laporan, poin) tidak memerlukan persetujuan
        ini.
      </p>

      {consents.status === 'error' ? (
        <p className="alert alert-error" role="alert">
          Pengaturan privasi belum dapat dimuat. Silakan muat ulang halaman beberapa saat lagi.
        </p>
      ) : (
        <ConsentSettings
          initial={CONSENT_PURPOSES.map((p) => ({ record: consents.records[p], text: CONSENT_TEXTS[p] }))}
        />
      )}

      <section className="card stack" aria-labelledby="persetujuan-privasi">
        <h2 id="persetujuan-privasi" style={{ margin: 0 }}>
          Kebijakan Privasi &amp; data akun
        </h2>
        <p style={{ margin: 0 }}>
          {consents.privacy
            ? `Disetujui (versi ${consents.privacy.version}${consents.privacy.updated_at ? `, ${new Date(consents.privacy.updated_at).toLocaleDateString('id-ID', { dateStyle: 'medium' })}` : ''}).`
            : 'Belum tercatat. Persetujuan diminta saat Anda masuk berikutnya.'}{' '}
          Persetujuan ini diperlukan agar akun, pesanan, dan poin bisa berjalan. Untuk menariknya, ajukan penghapusan akun
          di <Link href="/akun/data-saya">Data Saya</Link>.
        </p>
      </section>

      <section className="card stack" aria-labelledby="hak-lain">
        <h2 id="hak-lain" style={{ margin: 0 }}>
          Hak lainnya
        </h2>
        <p style={{ margin: 0 }}>
          Unduh salinan data atau hapus akun di <Link href="/akun/data-saya">Data Saya</Link>. Rincian pemrosesan dan
          masa simpan ada di <Link href="/privasi">Kebijakan Privasi</Link>.
        </p>
      </section>
    </div>
  );
}
