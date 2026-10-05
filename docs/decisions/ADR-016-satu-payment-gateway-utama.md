# ADR-016: Satu Payment Gateway Utama di Fase 1–3 (Rekomendasi: Xendit, dengan Syarat Validasi)

- **Status:** Proposed — **keputusan vendor ditunda** (klien 6 Okt 2026: "payment gateway belum ditentukan"). Platform dibangun gateway-agnostic (ADR-026); Xendit kini *kandidat*, bukan pilihan. Penilaian vendor memakai kriteria ADR-026.
- **Tanggal:** 2026-10-05 (catatan status 2026-10-06)
- **Pemutus (klien):** Finance (MDR, settlement) + Legal (kontrak) + IT Lead
- **Referensi review:** D-16, §3.3, R-S09, R-L05, R-T10
- **Terkait:** ADR-019 (merchant of record)

> **Ringkasan manajemen.** Cukup satu payment gateway di awal; Xendit dan Midtrans sama-sama mendukung QRIS, Virtual Account, dan e-wallet. Kami condong ke **Xendit** karena Odoo Community 19 sudah punya konektor resminya (terverifikasi) dan Xendit memasarkan fitur pembagian dana antar akun yang relevan untuk keranjang lintas PT. Keputusan final setelah membandingkan penawaran tarif dan kontrak kedua vendor.

## Konteks
- BRD & Charter: payment **tidak disebut**.
- Cetak Biru: *"Midtrans, Xendit, QRIS, VA, e-wallet"*.
- Dua gateway = dua integrasi webhook, dua rekonsiliasi, dua kontrak.
- Audit: `payment_xendit` ada di Odoo CE 19.0 (LGPL-3, *"payment provider solution for Indonesia and the Philippines"*); `payment_midtrans` **404** di CE 19.0.

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya (asumsi, validasi dengan vendor) |
|---|---|---|---|
| A. **Xendit** | Konektor CE Odoo 19 tersedia; produk sub-account/split (xenPlatform) diklaim vendor | Fitur split & syaratnya **belum terverifikasi** untuk kasus multi-PT | MDR QRIS ±0,7%; VA ±Rp4.000–5.000/trx; e-wallet ±1,5–2%; kartu ±2,9% + Rp2.000 |
| B. Midtrans | Pangsa pasar besar, Snap UI | Tidak ada modul CE 19 resmi (perlu konektor custom bila dipakai di Odoo); dukungan split perlu dicek | Struktur MDR mirip |
| C. Dua gateway sejak awal | Redundansi | 2× integrasi & rekonsiliasi | +3–4 minggu |

## Keputusan
Opsi A secara kondisional: Xendit dipilih **jika** (1) penawaran tarif setara/lebih baik, dan (2) model settlement multi-PT (ADR-019) dapat dipenuhi secara kontraktual. Integrasi utama di **BFF** (checkout Super-App), dengan verifikasi HMAC/token callback, idempotency key, dan replay window (prinsip CLAUDE.md). Tidak pernah menyimpan PAN (SAQ A). Odoo menerima hasil pembayaran via BFF, bukan webhook langsung.

## Konsekuensi
- **Positif:** satu rekonsiliasi; QRIS/VA/e-wallet tercakup.
- **Negatif:** vendor lock-in moderat; abstraksi `PaymentProvider` di BFF agar bisa berpindah.

## Risiko terkait
R-S09 (webhook palsu), R-L05 (PCI DSS), R-T10 (saga pembayaran), R-B02 (fee).

## Pertanyaan terbuka untuk klien
- **Q-29** metode pembayaran di POS (EDC terintegrasi?); **Q-1** entitas yang berkontrak dengan gateway (merchant of record).
- Apakah EFN sudah punya akun/kontrak gateway yang berjalan?

## Kapan ditinjau ulang
Setiap perpanjangan kontrak gateway, atau bila tingkat kegagalan pembayaran > 2% selama sebulan.
