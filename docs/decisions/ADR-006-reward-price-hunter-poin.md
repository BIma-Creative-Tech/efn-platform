# ADR-006: Reward Price Hunter Berupa Poin ke Loyalty Wallet Tunggal (bukan e-Voucher Langsung)

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** PO + Finance (nilai poin & perlakuan akuntansi)
- **Referensi review:** D-05, R-B05, §3.3 (poin lintas-brand = liabilitas)
- **Terkait:** ADR-020 (lokasi ledger), ADR-007

> **Ringkasan manajemen.** Pelanggan yang melaporkan harga kompetitor mendapat **poin**, bukan voucher Rp2.000 langsung. Semua poin (Price Hunter, belanja, MGM) masuk ke satu dompet. Voucher tetap ada sebagai salah satu cara menukar poin. Satu dompet berarti satu angka kewajiban (liability) yang bisa diaudit Finance.

## Konteks
- FSD §2 langkah 4: *"Node.js menembak API Odoo untuk men-generate Promo Code/Voucher senilai Rp2.000, lalu mengikatnya ke user_id pelanggan."*
- Charter: reward berupa **Poin EFN**.
- Cetak Biru: **EFN Coin/Points**.
- Dua mekanisme (voucher + poin) = dua ledger, dua kewajiban, dua titik fraud (R-B05).

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| A. **Poin ke wallet tunggal**; voucher = bentuk redeem | Satu ledger, satu kebijakan kedaluwarsa, mudah cap/clawback; cocok dengan MGM | Perlu definisi nilai poin (Q-13) | Masuk ke pekerjaan ledger (ADR-020); tanpa tambahan |
| B. e-Voucher Rp2.000 per laporan (FSD) | Sederhana dipahami pelanggan | Voucher terbit per laporan → ribuan kode; sulit di-clawback bila fraud terdeteksi belakangan; ledger terpisah dari MGM | +1–2 minggu integrasi `loyalty` Odoo untuk generate kupon; biaya rekonsiliasi berulang |
| C. Keduanya | Fleksibel | Dua kewajiban, dua laporan, kebingungan pelanggan | Biaya tertinggi |

## Keputusan
Opsi A. Laporan valid menghasilkan event `loyalty.earn.requested` (`docs/04 §4`) dengan `idempotency_key = price_hunter:<report_id>`; BFF menulis ke `platform.loyalty_ledger`. Redeem poin menjadi voucher/potongan dilakukan saat checkout (online) atau via OTP gate di POS (ADR-017).

## Konsekuensi
- **Positif:** fraud bisa di-clawback dengan entri negatif; cap harian/bulanan diterapkan di satu tempat; Finance melihat satu saldo kewajiban.
- **Negatif:** UX perlu menjelaskan konversi poin → rupiah.
- **Dampak dokumen:** FSD §2 langkah 4 dikoreksi (lihat `docs/05_FSD_CORRECTIONS.md` K-02).

## Risiko terkait
R-B05 (fraud Price Hunter), R-B03 (biaya promo), R-L03 (perlakuan pajak poin — konsultasi pajak).

## Pertanyaan terbuka untuk klien
- **Q-13**: 1 poin = Rp berapa, kedaluwarsa, minimum redeem, transferable?
- **Q-43**: reward per laporan valid & cap harian/bulanan.
- **Q-46**: kebijakan banding bila AI menolak.

## Kapan ditinjau ulang
Setelah 3 bulan Price Hunter berjalan: evaluasi biaya poin per laporan vs nilai data kompetitor.
