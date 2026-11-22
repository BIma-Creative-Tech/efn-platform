import { LoadingScreen } from '@/components/LoadingOverlay';

/**
 * Fallback Suspense tingkat akar: tampil saat muat awal / pindah grup rute, ketika layout grup masih menunggu data cangkang
 * (lib/shell-data.ts). Shell belum ada, jadi layar ini penuh dan tidak ada menu yang bisa diklik.
 */
export default function Loading(): React.ReactNode {
  return (
    <div className="ld-overlay ld-visible ld-page" role="dialog" aria-modal="true" aria-labelledby="ld-title" aria-busy="true">
      <LoadingScreen />
    </div>
  );
}
