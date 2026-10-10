import { imageUrl, type ImageSize } from '@/lib/catalog';
import styles from './online.module.css';

/**
 * Gambar produk lewat proxy origin sendiri `/api/catalog/images/:sha` (CSP `img-src 'self'`).
 * `<img>` biasa (bukan next/image) karena sumbernya sudah route handler ber-cache immutable dan tidak perlu optimizer.
 */
export function ProductImage({ sha, alt, size = 512 }: { sha: string | null; alt: string; size?: ImageSize }) {
  const src = imageUrl(sha, size);
  return (
    <div className={styles.imageBox}>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element -- sumber same-origin ber-cache immutable, tanpa optimizer
        <img src={src} alt={alt} loading="lazy" decoding="async" width={size} height={size} />
      ) : (
        <span aria-hidden="true">Tanpa gambar</span>
      )}
    </div>
  );
}
