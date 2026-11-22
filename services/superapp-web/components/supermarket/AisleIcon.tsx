import type { AisleIconId } from '@/lib/supermarket';

/**
 * Ikon garis lorong supermarket (SVG inline, dekoratif: `aria-hidden`). Digambar sendiri (bukan pustaka ikon pihak
 * ketiga) agar tanpa dependency baru dan lolos CSP `img-src 'self'`. Warna = `currentColor`.
 */
const PATHS: Record<AisleIconId, string[]> = {
  leaf: ['M5 19c0-8 5-13 14-14-1 9-6 14-14 14Z', 'M5 19 13 11'],
  fruit: ['M12 7c-4-2-8 1-7 6s4 7 7 6c3 1 6-1 7-6s-3-8-7-6Z', 'M12 7c0-2 1-3 3-4'],
  meat: ['M15 4c3 0 5 2 5 5 0 4-4 7-8 9-3 1-6 0-7-2s0-4 2-5c2-1 2-3 3-5 1-1 3-2 5-2Z', 'M7 18l-3 3'],
  fish: ['M3 12c3-4 7-6 11-5 3 1 5 3 6 5-1 2-3 4-6 5-4 1-8-1-11-5Z', 'M3 12l-1-3M3 12l-1 3', 'M15 11h.01'],
  bread: ['M4 11a8 5 0 0 1 16 0v7a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1Z', 'M9 9l1 3M14 9l1 3'],
  milk: ['M9 3h6v3l2 3v11a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V9l2-3Z', 'M7 13h10'],
  snow: ['M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9', 'M10 5l2 2 2-2M10 19l2-2 2 2'],
  jar: ['M8 3h8v3H8z', 'M7 6h10l1 3v10a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V9Z', 'M9 13h6'],
  bottle: ['M10 2h4v4l2 3v12a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V9l2-3Z', 'M8 14h8'],
  snack: ['M6 4h12l-2 4 2 4-2 4 2 4H6l2-4-2-4 2-4Z'],
  care: ['M9 3h6v4H9z', 'M8 7h8v13a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1Z', 'M12 11v5M9.5 13.5h5'],
  home: ['M3 11 12 4l9 7', 'M5 10v10h14V10', 'M10 20v-6h4v6'],
  star: ['M12 3l2.7 5.6 6.1.8-4.5 4.2 1.1 6.1L12 16.8 6.6 19.7l1.1-6.1L3.2 9.4l6.1-.8Z'],
};

export function AisleIcon({ id, size = 28, className }: { id: AisleIconId; size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[id].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
