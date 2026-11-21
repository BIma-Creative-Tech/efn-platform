/**
 * Catatan SMD di halaman F&B (G51 B1). Server component. Keadaan selalu berupa teks + ikon (tidak hanya warna).
 */
import type { ReactNode } from 'react';
import { MIXED_BASIS_NOTE, MIXED_NOTE, NA_SMD, SMD_BASIS } from '@/lib/smd-merge';
import { EmptyState } from './ui';

/** Pengganti kartu/halaman khusus F&B bila hanya SMD yang dipilih. */
export function NotApplicableSmd({ title, reason }: { title?: string; reason?: string }): ReactNode {
  return (
    <section className="card" aria-label={title ?? NA_SMD}>
      {title ? <p className="card-title">{title}</p> : null}
      <EmptyState
        title={NA_SMD}
        reason={reason ?? 'Kartu ini khusus brand F&B (ESB). Pilih brand F&B pada filter Brand / PT untuk melihatnya.'}
        icon="info"
      />
    </section>
  );
}

/** Catatan di kartu F&B-saja bila ESB dan SMD dipilih bersama. */
export function MixedScopeNote(): ReactNode {
  return (
    <p className="muted small" role="note">
      {MIXED_NOTE}
    </p>
  );
}

/** Label basis uang SMD pada halaman gabungan. */
export function SmdBasisNote({ mixed, extra }: { mixed: boolean; extra?: string }): ReactNode {
  return (
    <p className="notice" role="note">
      {mixed ? `Basis campuran: ${MIXED_BASIS_NOTE}` : `Omzet SMD ${SMD_BASIS}.`}
      {extra ? ` ${extra}` : ''}
    </p>
  );
}

/** Catatan bila satu sisi (F&B atau SMD) gagal dimuat tetapi sisi lain berhasil. */
export function SideNote({ note }: { note: string | null }): ReactNode {
  return note ? (
    <p className="notice" role="status">
      {note}
    </p>
  ) : null;
}
