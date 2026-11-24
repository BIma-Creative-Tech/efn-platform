'use client';
/**
 * Sisa waktu sesi ESB di header modul Produksi (§30.17.2). Dihitung di klien dari `expires_at` (detik epoch) supaya tidak
 * basi pada halaman yang di-cache browser; diperbarui tiap 30 detik. Teks biasa (tanpa live region yang berisik); saat habis
 * tampil tautan "Masuk lagi" (tab baru agar konteks halaman dan isian yang sedang dikerjakan tidak hilang).
 */
import { useEffect, useState } from 'react';
import { remainingLabel } from '@/lib/prodplan-session';

export function SessionClock({ expiresAtSec }: { expiresAtSec: number | null }): React.ReactNode {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = (): void => setNow(Math.floor(Date.now() / 1000));
    const first = window.setTimeout(tick, 0);
    const id = window.setInterval(tick, 30_000);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, []);
  if (expiresAtSec === null) return null;
  const ended = now !== null && expiresAtSec - now <= 0;
  return (
    <span className="pp-clock muted small">
      Sisa sesi ESB: <strong>{now === null ? '...' : remainingLabel(expiresAtSec, now)}</strong>
      {ended ? (
        <>
          {' '}
          <a href="/produksi/masuk?e=expired" target="_blank" rel="noopener">
            Masuk lagi (tab baru)
          </a>
        </>
      ) : null}
    </span>
  );
}
