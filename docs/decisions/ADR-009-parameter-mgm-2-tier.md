# ADR-009: Parameter & Struktur Reward MGM 2-Tier (Cashback Promosi Ber-cap, Settle setelah Masa Retur)

- **Status:** Proposed — **nilai angka wajib diputuskan Finance; struktur wajib disetujui Legal**
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** Finance (angka & simulasi margin), Legal (struktur, Permendag 70/2019), PO
- **Referensi review:** D-08, R-B03, R-B06, R-L02, R-O05
- **Terkait:** ADR-020 (ledger), ADR-006

> **Ringkasan manajemen.** Dokumen menyebut komisi 5% + 2% dari nilai belanja tanpa batas. Untuk groceries dengan margin kotor 15–25%, ini bisa menghabiskan sepertiga margin. Kami mengusulkan struktur: reward berupa **poin cashback promosi** (bukan "komisi"), ada batas bulanan, kategori tertentu dikecualikan, dan baru cair setelah masa retur lewat. Angka final ditentukan Finance lewat simulasi; Legal harus menyetujui sebelum peluncuran agar tidak dikategorikan penjualan berjenjang.

## Konteks
- BRD: MGM 2-tier **tanpa parameter**.
- Cetak Biru: *"5% Tier-1, 2% Tier-2 cashback"*, settle H+1 tanpa retur.
- Retur groceries bisa sampai 7 hari (Q-15) → H+1 terlalu cepat; reward yang sudah cair sulit ditarik.

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Dampak biaya |
|---|---|---|---|
| A. **Poin cashback, basis net order value setelah diskon, % dikonfigurasi, cap per referrer per bulan, kategori dikecualikan, settle H+N (N = masa retur + 1), clawback otomatis** | Biaya terkendali & dapat diprediksi; fraud & retur tertangani; lebih aman secara regulasi | Lebih kompleks dijelaskan ke pelanggan | Biaya maksimal = cap × jumlah referrer aktif (bisa dianggarkan) |
| B. 5% + 2% order value tanpa cap (Cetak Biru) | Menarik bagi pelanggan | Potensi gerus margin 7% dari omzet; insentif fraud sybil | Tidak terbatas |
| C. Basis gross margin (bukan order value) | Paling adil bagi margin | Margin per SKU adalah data sensitif; sulit dijelaskan; perhitungan berat | +2 minggu; butuh margin akurat per SKU |
| D. Reward flat per transaksi pertama referral saja (1 tier) | Paling sederhana, risiko regulasi terendah | Kurang "viral" | Paling murah |

## Keputusan
Opsi A sebagai **struktur**; angka default yang diusulkan untuk simulasi (bukan final): Tier-1 2%, Tier-2 0,5%, cap Rp100.000/referrer/bulan, settle H+8, masa relasi referral 12 bulan, pengecualian rokok/susu formula/pulsa/barang promo. Engine membaca parameter dari konfigurasi (bukan hard-code). Tidak ada biaya keanggotaan. Peluncuran (fase 4) **diblokir** sampai ada legal sign-off tertulis.

## Konsekuensi
- **Positif:** biaya bisa dianggarkan; clawback via entri negatif di ledger tunggal.
- **Negatif:** pelanggan menunggu ~8 hari untuk reward.
- **Dampak dokumen:** Cetak Biru (5%+2%, H+1) diganti setelah Finance menetapkan angka.

## Risiko terkait
R-B03 (margin, skor 16), R-B06 (fraud sybil/circular), R-L02 (penjualan berjenjang), R-O05 (retur → clawback).

## Pertanyaan terbuka untuk klien
- **Q-14** basis, persentase, cap, masa relasi, pengecualian, syarat minimal downline.
- **Q-15** kapan settle; **Q-16** berlaku untuk transaksi POS offline/tanpa app?
- **Q-4** settlement poin lintas PT.

## Kapan ditinjau ulang
Setelah simulasi spreadsheet Finance (sebelum fase 4) dan 3 bulan pasca-peluncuran (biaya aktual vs anggaran).
