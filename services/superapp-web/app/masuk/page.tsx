import type { Metadata } from 'next';
import Link from 'next/link';
import { safeNextPath } from '@/lib/session';
import { LoginForm } from './LoginForm';

export const metadata: Metadata = { title: 'Masuk' };

type SearchParams = Promise<{ [key: string]: string | string[] | undefined }>;

export default async function MasukPage({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const nextRaw = typeof sp.next === 'string' ? sp.next : null;
  const expired = sp.expired === '1';

  return (
    <div className="card stack" style={{ maxWidth: '28rem', margin: '0 auto' }}>
      <h1 style={{ marginTop: 0 }}>Masuk</h1>
      {expired && (
        <p className="alert alert-error" role="status">
          Sesi Anda telah berakhir. Silakan masuk kembali.
        </p>
      )}
      <p className="muted">Masuk dengan nomor HP. Kami akan mengirim kode 6 digit lewat WhatsApp.</p>
      <LoginForm nextPath={safeNextPath(nextRaw)} />
      <p className="small muted">
        Dengan masuk, Anda menyetujui pemrosesan nomor HP sesuai <Link href="/privasi">Kebijakan Privasi</Link>.
      </p>
    </div>
  );
}
