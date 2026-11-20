import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { getSessionToken } from '@/lib/session';
import { InboxView } from '@/components/engage/InboxView';

export const metadata: Metadata = { title: 'Kotak masuk & voucher' };
export const dynamic = 'force-dynamic';

/** Kotak masuk kampanye + voucher (G50; BFF GET /v1/me/inbox). Butuh login; isi mengikuti flag `campaign_inbox`. */
export default async function InboxPage() {
  const token = await getSessionToken();
  if (!token) redirect('/masuk?next=/inbox');
  return (
    <div className="stack" style={{ maxWidth: '40rem', margin: '0 auto' }}>
      <p>
        <Link href="/akun">&larr; Akun Saya</Link>
      </p>
      <h1>Kotak masuk &amp; voucher</h1>
      <InboxView />
    </div>
  );
}
