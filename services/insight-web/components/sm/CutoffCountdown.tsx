'use client';
/**
 * Batas persetujuan usulan order (ST-1): waktu absolut WIB + hitung mundur yang diperbarui tiap menit di klien.
 * Hitung mundur TIDAK `aria-live` (spesifikasi §4.1/§6.1 4.1.3); hanya satu pengumuman `role="status"` saat melewati batas.
 * `nowMs` dari server dipakai untuk render awal agar tidak ada selisih hidrasi.
 */
import { useEffect, useRef, useState } from 'react';
import { fmtClockWib, fmtCountdown } from '@/lib/sm-format';

export function CutoffCountdown({ cutoffAtIso, nowMs, showCountdown }: { cutoffAtIso: string; nowMs: number; showCountdown: boolean }): React.ReactNode {
  const cutoff = new Date(cutoffAtIso);
  const cutoffMs = cutoff.getTime();
  const [now, setNow] = useState(nowMs);
  const [announce, setAnnounce] = useState(false);
  const prev = useRef(nowMs);

  useEffect(() => {
    if (!Number.isFinite(cutoffMs) || !showCountdown) return;
    const id = window.setInterval(() => {
      const t = Date.now();
      if (prev.current < cutoffMs && t >= cutoffMs) setAnnounce(true);
      prev.current = t;
      setNow(t);
    }, 60_000);
    return () => window.clearInterval(id);
  }, [cutoffMs, showCountdown]);

  if (!Number.isFinite(cutoffMs)) return <span>Batas persetujuan tidak diketahui</span>;
  return (
    <>
      <span className="big-time">Batas persetujuan {fmtClockWib(cutoff)}</span>
      {showCountdown ? <span>({fmtCountdown(cutoffMs - now)})</span> : null}
      {announce ? (
        <span role="status" className="sr-only">
          Batas persetujuan sudah lewat.
        </span>
      ) : null}
    </>
  );
}
