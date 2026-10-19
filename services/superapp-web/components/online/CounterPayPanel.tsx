'use client';

import { formatRupiah } from '@/lib/format';
import { formatCountdown } from '@/lib/orders';
import { countdownAnnouncement, spellCounterCode, spokenDuration } from '@/lib/payment';
import styles from './online.module.css';

/**
 * Panel "Bayar di kasir" (docs/04 §12.2). Menampilkan KODE BAYAR (bukan kode ambil), hitung mundur dari `expires_at`
 * server, dan instruksi ke kasir.
 *
 * Kode hanya diterima lewat props (memori komponen): tidak ditulis ke URL, localStorage/sessionStorage, console,
 * atau analytics. Tidak ada tombol salin/bagikan (mengurangi kebocoran lewat clipboard/riwayat).
 */
export function CounterPayPanel({ code, secondsLeft, amount }: { code: string | null; secondsLeft: number; amount: number }) {
  const expired = secondsLeft <= 0;
  return (
    <section className={`card ${styles.payBox}`} aria-labelledby="bayar-kasir-judul">
      <h2 id="bayar-kasir-judul" style={{ margin: 0, fontSize: '1.0625rem' }}>
        Bayar di kasir
      </h2>
      {expired ? (
        <p style={{ margin: 0 }}>Waktu pembayaran habis. Menunggu konfirmasi status…</p>
      ) : (
        <>
          <p style={{ margin: 0 }}>
            Tunjukkan kode ini ke kasir, lalu bayar <strong>{formatRupiah(amount)}</strong> (tunai, kartu, atau QRIS di kasir).
          </p>
          {code ? (
            <p className={styles.pickupCode} aria-label={`Kode bayar ${spellCounterCode(code)}`}>
              {code}
            </p>
          ) : (
            <p className="muted" role="status">
              Menyiapkan kode bayar…
            </p>
          )}
          <p className="small muted" style={{ margin: 0 }}>
            Ini <strong>kode bayar</strong>, bukan kode ambil. Kode ambil muncul setelah kasir menerima pembayaran dan toko
            mengonfirmasi pesanan. Jangan bagikan kode bayar ke orang lain.
          </p>
          <p style={{ margin: 0 }}>Selesaikan pembayaran dalam</p>
          <p
            className={`${styles.countdown} ${secondsLeft <= 60 ? styles.countdownUrgent : ''}`}
            style={{ margin: 0 }}
            role="timer"
            aria-live="off"
            aria-label={`Sisa waktu ${spokenDuration(secondsLeft)}`}
          >
            {formatCountdown(secondsLeft)}
          </p>
        </>
      )}
      {/* Pengumuman hanya tiap menit / saat kritis (bukan tiap detik). */}
      <div className="visually-hidden" role="status" aria-live="polite">
        {countdownAnnouncement(secondsLeft)}
      </div>
    </section>
  );
}
