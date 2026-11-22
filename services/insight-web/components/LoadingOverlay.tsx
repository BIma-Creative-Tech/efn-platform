'use client';
/**
 * Layar memuat yang MEMBLOKIR halaman selama data diambil (query ClickHouse bisa puluhan detik untuk rentang panjang).
 * - LoadingOverlay: dipasang sekali di Shell; aktif saat klik tautan internal / drill-down (beginNav) atau transisi filter/refresh
 *   (useBusy). Saat aktif, `.shell` diberi `inert` (klik, fokus, dan keyboard ke menu/halaman diblokir) dan lapisan penuh layar
 *   menangkap semua klik. Tampilan muncul setelah SHOW_DELAY_MS agar navigasi cepat tidak berkedip; blokir berlaku seketika.
 * - LoadingScreen: tampilan (grafik batang animasi yang mengikuti kursor/sentuhan, tahapan, detik berjalan, progres, tip).
 *   Juga dipakai `app/loading.tsx` untuk muat awal (sebelum Shell ada).
 */
import { usePathname, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useRef, useState } from 'react';
import { beginNav, endNav, isInternalNavClick, useIsBusy } from '@/lib/loading-store';

const SHOW_DELAY_MS = 200;
const TIP_MS = 7000;
const BARS = 9;
const RELOAD_AFTER_S = 45;

const STAGES: readonly { at: number; text: string }[] = [
  { at: 0, text: 'Menghubungkan ke gudang data' },
  { at: 2, text: 'Mengambil data transaksi' },
  { at: 6, text: 'Menghitung agregasi per cabang dan produk' },
  { at: 15, text: 'Menyusun grafik dan tabel' },
  { at: 30, text: 'Rentang data besar sedang diproses, mohon tunggu' },
];

const TIPS: readonly string[] = [
  'Rentang tanggal yang lebih pendek dimuat jauh lebih cepat.',
  'Klik batang grafik atau baris tabel untuk menambah filter (drill-down).',
  'Klik nama seri pada legenda untuk menyembunyikan atau menampilkannya.',
  'Arahkan kursor ke istilah bergaris bawah untuk melihat artinya, atau buka Glosarium.',
  'Tabel dapat dicari dan diekspor ke CSV dari tombol di pojok kartu.',
  'Lencana kesegaran di atas menunjukkan kapan data terakhir diperbarui.',
];

function stageAt(sec: number): string {
  let text = STAGES[0].text;
  for (const s of STAGES) if (sec >= s.at) text = s.text;
  return text;
}

export function LoadingScreen({ startedAt }: { startedAt?: number }): React.ReactNode {
  const [t0] = useState(() => startedAt ?? Date.now());
  const [now, setNow] = useState(t0);
  const [tip, setTip] = useState(0);
  const [pointer, setPointer] = useState<number | null>(null);
  const [pops, setPops] = useState(0);
  const tipBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => {
    const id = window.setInterval(() => setTip((i) => (i + 1) % TIPS.length), TIP_MS);
    return () => window.clearInterval(id);
  }, [tip]);
  useEffect(() => {
    tipBtn.current?.focus({ preventScroll: true });
  }, []);

  const sec = Math.max(0, (now - t0) / 1000);
  // Progres asimtotik (tidak pernah 100% sebelum data benar-benar tiba): 50% di ±8 dtk, 90% di ±27 dtk.
  const pct = Math.min(96, Math.round((1 - Math.exp(-sec / 12)) * 100));

  const onMove = (e: React.PointerEvent<HTMLDivElement>): void => {
    const r = e.currentTarget.getBoundingClientRect();
    setPointer(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)));
  };

  return (
    <div className="ld-card">
      <div
        className="ld-stage"
        onPointerMove={onMove}
        onPointerLeave={() => setPointer(null)}
        onPointerDown={(e) => {
          onMove(e);
          setPops((p) => p + 1);
        }}
        aria-hidden="true"
      >
        {Array.from({ length: BARS }, (_, i) => {
          const center = (i + 0.5) / BARS;
          const boost = pointer === null ? 0 : Math.max(0, 1 - Math.abs(pointer - center) * 4);
          return (
            <span
              key={`${i}-${boost > 0.6 ? pops : 0}`}
              className={`ld-bar${boost > 0.6 && pops > 0 ? ' ld-pop' : ''}`}
              style={{ animationDelay: `${i * 0.12}s`, ['--boost' as string]: boost.toFixed(2) }}
            />
          );
        })}
        <svg className="ld-line" viewBox="0 0 100 40" preserveAspectRatio="none">
          <polyline points="0,32 12,26 25,29 37,18 50,22 62,12 75,16 87,7 100,10" pathLength={100} />
        </svg>
      </div>

      <p className="ld-title" id="ld-title">
        Memuat data
      </p>
      <p className="ld-status" aria-live="polite">
        {stageAt(sec)}
        <span className="ld-dots" aria-hidden="true" />
      </p>

      <div className="ld-progress" role="progressbar" aria-label="Perkiraan progres" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <span style={{ width: `${pct}%` }} />
      </div>
      <p className="ld-meta">
        <span>{Math.floor(sec)} dtk</span>
        {sec >= 20 ? <span>Bisa sampai ±1 menit untuk rentang panjang</span> : <span>Halaman dikunci sampai selesai</span>}
      </p>
      {sec >= RELOAD_AFTER_S ? (
        // SEC-LS-01/02: jalan keluar bila permintaan menggantung (transisi tanpa batas waktu) atau URL tidak berubah.
        <p className="ld-reload">
          Terlalu lama?{' '}
          <button type="button" className="btn btn-sm" onClick={() => window.location.reload()}>
            Muat ulang halaman
          </button>
        </p>
      ) : null}

      <div className="ld-tip">
        <p key={tip} className="ld-tip-text">
          <strong>Tip:</strong> {TIPS[tip]}
        </p>
        <button ref={tipBtn} type="button" className="btn btn-sm" onClick={() => setTip((i) => (i + 1) % TIPS.length)}>
          Tip lain
        </button>
      </div>
    </div>
  );
}

function Overlay(): React.ReactNode {
  const busy = useIsBusy();
  const pathname = usePathname();
  const search = useSearchParams().toString();
  const [visibleAt, setVisibleAt] = useState<number | null>(null);
  const startedAt = useRef(0);

  // Navigasi klien selesai = URL berubah (halaman tanpa Suspense: commit setelah semua query selesai).
  useEffect(() => {
    endNav();
  }, [pathname, search]);

  // Tangkap klik tautan internal di fase capture (sebelum Link milik Next menangani).
  useEffect(() => {
    const onClick = (ev: MouseEvent): void => {
      const a = (ev.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!a) return;
      const from = window.location.href;
      const link = { href: a.href, target: a.target, hasDownload: a.hasAttribute('download') };
      // SEC-LS-02: dinilai setelah dispatch selesai agar preventDefault dari onClick React ikut terlihat.
      window.setTimeout(() => {
        if (isInternalNavClick(link, ev, from)) beginNav();
      }, 0);
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);

  // Blokir seketika; tampilkan visual setelah jeda singkat.
  useEffect(() => {
    const shell = document.querySelector<HTMLElement>('.shell');
    const skip = document.querySelector<HTMLElement>('.skip-link');
    if (!busy) {
      shell?.removeAttribute('inert');
      skip?.removeAttribute('inert');
      return;
    }
    startedAt.current = Date.now();
    shell?.setAttribute('inert', '');
    skip?.setAttribute('inert', '');
    (document.activeElement as HTMLElement | null)?.blur?.();
    const id = window.setTimeout(() => setVisibleAt(startedAt.current), SHOW_DELAY_MS);
    return () => {
      window.clearTimeout(id);
      setVisibleAt(null);
      shell?.removeAttribute('inert');
      skip?.removeAttribute('inert');
    };
  }, [busy]);

  if (!busy) return null;
  return (
    <div
      className={`ld-overlay${visibleAt !== null ? ' ld-visible' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby="ld-title"
      aria-busy="true"
    >
      {visibleAt !== null ? <LoadingScreen startedAt={visibleAt} /> : null}
    </div>
  );
}

export function LoadingOverlay(): React.ReactNode {
  return (
    <Suspense fallback={null}>
      <Overlay />
    </Suspense>
  );
}
