'use client';

import { useRouter } from 'next/navigation';
import { useTransition } from 'react';
import { useBusy } from '@/lib/loading-store';

export function RetryButton(): React.ReactNode {
  const router = useRouter();
  const [pending, start] = useTransition();
  useBusy(pending);
  return (
    <button type="button" className="btn" disabled={pending} onClick={() => start(() => router.refresh())}>
      {pending ? 'Memuat...' : 'Coba lagi'}
    </button>
  );
}
