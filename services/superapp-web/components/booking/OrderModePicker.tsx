import Link from 'next/link';
import type { OrderMode } from '@/lib/order-modes';
import styles from './booking.module.css';

/**
 * Pemilih mode pesan (docs/04 §15.7): Ambil · Pre-order · Makan di tempat · Reservasi · Pesan-antar (nonaktif).
 * Server Component tanpa state: setiap mode = tautan ke alurnya; mode nonaktif bukan tautan (`aria-disabled`).
 */
export function OrderModePicker({ modes, labelledBy, onDark = false }: { modes: OrderMode[]; labelledBy: string; onDark?: boolean }) {
  return (
    <ul className={`${styles.modes} ${onDark ? styles.modesOnDark : ''}`} aria-labelledby={labelledBy} data-order-modes="">
      {modes.map((m) => (
        <li key={m.id}>
          {m.href ? (
            <Link href={m.href} className={styles.mode} data-mode={m.id}>
              <span className={styles.modeLabel}>{m.label}</span>
              <span className={styles.modeHint}>{m.hint}</span>
            </Link>
          ) : (
            <span className={`${styles.mode} ${styles.modeOff}`} aria-disabled="true" data-mode={m.id}>
              <span className={styles.modeLabel}>{m.label}</span>
              <span className={styles.modeHint}>{m.hint}</span>
              {m.badge && <span className={styles.modeBadge}>{m.badge}</span>}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
