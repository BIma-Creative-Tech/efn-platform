import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { bffFetch } from '@/lib/bff';
import { companyDisplayName, CURSOR_RE } from '@/lib/catalog';
import { formatRupiah } from '@/lib/format';
import { orderStatusLabel, parseOrderList } from '@/lib/orders';
import { getSessionToken } from '@/lib/session';
import { OnlineUnavailable } from '@/components/online/OnlineUnavailable';
import styles from '@/components/online/online.module.css';

export const metadata: Metadata = { title: 'Pesanan Saya' };
export const dynamic = 'force-dynamic';

function formatDate(iso: string | null): string {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? '' : new Intl.DateTimeFormat('id-ID', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Jakarta' }).format(t);
}

/** Daftar pesanan milik member (docs/04 §10.3.6), terbaru dulu, halaman lewat `?cursor=` opak. */
export default async function PesananPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const token = await getSessionToken();
  if (!token) redirect('/masuk?next=/pesanan');
  const sp = await searchParams;
  const raw = Array.isArray(sp.cursor) ? sp.cursor[0] : sp.cursor;
  const q = new URLSearchParams({ limit: '20' });
  if (raw && CURSOR_RE.test(raw)) q.set('cursor', raw);

  const r = await bffFetch<unknown>(`/v1/orders?${q}`, { token });
  if (!r.ok) {
    if (r.status === 401) redirect('/masuk?next=/pesanan&expired=1');
    return <OnlineUnavailable reason={r.status === 503 && r.code === 'online_unavailable' ? 'off' : 'error'} />;
  }
  const list = parseOrderList(r.data);

  return (
    <div className="stack" style={{ maxWidth: '44rem', margin: '0 auto' }}>
      <h1 style={{ margin: 0 }}>Pesanan Saya</h1>
      {!list ? (
        <p className="alert alert-error" role="alert">
          Daftar pesanan belum dapat dimuat. Silakan muat ulang.
        </p>
      ) : list.items.length === 0 ? (
        <section className="card stack">
          <p style={{ margin: 0 }}>Belum ada pesanan.</p>
          <p style={{ margin: 0 }}>
            <Link href="/belanja" className="btn">
              Mulai belanja
            </Link>
          </p>
        </section>
      ) : (
        <>
          <ul className={styles.orderList}>
            {list.items.map((o) => (
              <li key={o.order_id} className="card">
                <Link href={`/pesanan/${o.order_id}`} className={styles.orderLink}>
                  <span className={styles.groupHeader}>
                    <strong>{formatDate(o.created_at) || 'Pesanan'}</strong>
                    <span className="badge">{orderStatusLabel(o.status)}</span>
                  </span>
                  <span className="small muted" style={{ display: 'block' }}>
                    {o.parts.map((p) => companyDisplayName(p.channel_code)).join(', ')} · {formatRupiah(o.amount_payable)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          {list.next_cursor && (
            <p>
              <Link href={`/pesanan?cursor=${encodeURIComponent(list.next_cursor)}`} className="btn btn-secondary">
                Pesanan sebelumnya
              </Link>
            </p>
          )}
        </>
      )}
    </div>
  );
}
