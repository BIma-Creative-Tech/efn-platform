import type { Metadata } from 'next';
import { notFound, redirect } from 'next/navigation';
import { bffFetch } from '@/lib/bff';
import { isUuid } from '@/lib/catalog';
import { parseOrderDetail } from '@/lib/orders';
import { paymentSimulatorEnabled } from '@/lib/orders-server';
import { getSessionToken } from '@/lib/session';
import { OnlineUnavailable } from '@/components/online/OnlineUnavailable';
import { OrderView } from '@/components/online/OrderView';

export const metadata: Metadata = { title: 'Status Pesanan', referrer: 'no-referrer' };
export const dynamic = 'force-dynamic';

/**
 * Status pesanan + pembayaran + kode ambil (docs/04 §10.3.6). `order_id` UUID opak; order milik orang lain -> 404.
 * Kode ambil TIDAK dirender server-side (hanya diambil saat tombol ditekan, lihat PickupCodePanel).
 */
export default async function OrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const token = await getSessionToken();
  if (!token) redirect(`/masuk?next=${encodeURIComponent(`/pesanan/${id}`)}`);

  const r = await bffFetch<unknown>(`/v1/orders/${id.toLowerCase()}`, { token });
  if (!r.ok) {
    if (r.status === 401) redirect(`/masuk?next=${encodeURIComponent(`/pesanan/${id}`)}&expired=1`);
    if (r.status === 404 || r.status === 403) notFound();
    return <OnlineUnavailable reason={r.status === 503 && r.code === 'online_unavailable' ? 'off' : 'error'} />;
  }
  const order = parseOrderDetail(r.data);
  if (!order) return <OnlineUnavailable reason="error" />;

  return (
    <div style={{ maxWidth: '40rem', margin: '0 auto' }}>
      <OrderView initial={order} simulator={paymentSimulatorEnabled()} />
    </div>
  );
}
