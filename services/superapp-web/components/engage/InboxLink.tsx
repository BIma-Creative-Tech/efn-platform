'use client';

/** Tautan "Kotak masuk & voucher" di /akun dengan lencana jumlah pesan baru (penanda terakhir dilihat di localStorage). */
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { apiFetch } from '@/lib/cart-client';
import { countNew, INBOX_SEEN_KEY, isExpired, parseInbox } from '@/lib/inbox';
import { useEngage } from './EngageProvider';

export function InboxLink() {
  const { features, ready } = useEngage();
  const [fresh, setFresh] = useState(0);

  useEffect(() => {
    if (!ready || !features.campaignInbox) return;
    let active = true;
    void apiFetch<{ items: unknown }>('/api/me/inbox').then((r) => {
      if (!active || !r.ok) return;
      let seen: string | null = null;
      try {
        seen = window.localStorage.getItem(INBOX_SEEN_KEY);
      } catch {
        // diabaikan
      }
      const now = Date.now();
      setFresh(countNew(parseInbox(r.data).filter((i) => !isExpired(i, now)), seen));
    });
    return () => {
      active = false;
    };
  }, [ready, features.campaignInbox]);

  if (!ready || !features.campaignInbox) return null;
  return (
    <section className="card stack" aria-labelledby="inbox-judul">
      <h2 id="inbox-judul" style={{ margin: 0 }}>
        Kotak masuk &amp; voucher
      </h2>
      <p style={{ margin: 0 }}>
        <Link href="/inbox">Buka kotak masuk</Link>
        {fresh > 0 && (
          <span className="badge" style={{ marginLeft: '0.5rem' }}>
            {fresh} baru
          </span>
        )}
      </p>
    </section>
  );
}
