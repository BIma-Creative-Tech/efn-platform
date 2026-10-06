# ADR-015: Penamaan Modul Custom `efn_<domain>` dan Prinsip "OCA/CE Dulu, Custom Terakhir"

- **Status:** Proposed — **sebagian digantikan oleh [ADR-031](ADR-031-tanpa-dependency-modul-odoo-pihak-ketiga.md) (6 Okt 2026, keputusan klien)**: prinsip "OCA/CE dulu", peta modul OCA di bagian Keputusan, dan aturan lisensi AGPL-3 untuk modul yang bergantung OCA **tidak berlaku lagi**. Yang tetap berlaku: penamaan `efn_<domain>` dan prinsip "Standar CE dulu, custom terakhir".
- **Tanggal:** 2026-10-05 (catatan supersede 2026-10-06)
- **Pemutus (klien):** IT Lead
- **Referensi review:** D-15, R-T03, R-T04, R-L08, R-B09

> **Catatan ADR-031.** Urutan fit-gap kini: Standar CE → di luar Odoo (BFF/ETL/Metabase) → modul `efn_*` sendiri dengan OCA & Cybrosys sebagai **referensi desain** (bukan dependency). Semua `efn_*` berlisensi LGPL-3. Tabel "Kebutuhan → Solusi" di bawah dibiarkan sebagai catatan sejarah; peta yang berlaku ada di ADR-031 dan `docs/06`.

> **Ringkasan manajemen.** Dokumen menyebut 4 atau 5 "modul custom". Setelah dicek, sebagian sudah tersedia gratis (inter-company dari OCA, loyalty dari Odoo Community). Kami hanya menulis kode sendiri untuk yang benar-benar tidak ada, dengan nama seragam `efn_*`. Ini memangkas pekerjaan dan biaya perawatan saat upgrade.

## Konteks
- Blueprint: 4 modul — *Multi-Company, MGM, Kitting, OTP Interceptor*.
- Cetak Biru: 5 modul `custom_efn_*`.
- Konvensi Odoo: nama teknis pendek, snake_case, prefix organisasi. Repo sudah memakai `efn_core`.

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Dampak |
|---|---|---|---|
| A. **`efn_<domain>` + peta ke CE/OCA dulu** | Konsisten dengan repo; jumlah kode custom minimal | Perlu disiplin audit sebelum menulis | Menghemat estimasi minggu (lihat tabel) |
| B. `custom_efn_*` (Cetak Biru) | Eksplisit "custom" | Nama panjang; tidak konvensional | Sama dengan A |
| C. Tulis semua modul dari nol | Kontrol penuh | Mengulang OCA; utang upgrade besar | +8–12 minggu |

## Keputusan
Opsi A. Peta modul (sementara, diperbarui oleh `docs/06`):

| Kebutuhan (Blueprint/Cetak Biru) | Solusi | Modul custom |
|---|---|---|
| Multi-Company / inter-company | OCA `purchase_sale_inter_company`, `account_invoice_inter_company` (19.0 tersedia) | `efn_intercompany` (glue/konfigurasi saja, bila perlu) |
| Kitting | CE `mrp` BoM *Manufacture* (ADR-018) | `efn_kitting` hanya bila perlu automasi MO dari permintaan outlet |
| OTP Interceptor POS | — | `efn_pos_otp` (OWL + panggilan server ke BFF, ADR-017) |
| MGM | Engine di BFF (ADR-020), bukan Odoo | Tidak ada di Odoo; Odoo hanya menerima jurnal liabilitas |
| KDS | Tidak ada di CE/OCA 19 (ADR-013) | `efn_pos_kds` |
| Farming / QC Intake | Sebagian `quality_control_oca` (OCA/manufacture 19.0, perlu dievaluasi) | `efn_agri` |
| Fondasi (kode brand, param) | — | `efn_core` (sudah ada) |

Lisensi: `LGPL-3`, atau `AGPL-3` bila bergantung pada modul OCA AGPL (R-L08).

## Konsekuensi
- **Positif:** kode custom lebih sedikit; upgrade lebih murah.
- **Negatif:** ketergantungan pada laju migrasi OCA (ADR-021).

## Risiko terkait
R-T03, R-T04, R-L08, R-B09.

## Pertanyaan terbuka untuk klien
Tidak ada yang bersifat bisnis; IT Lead mengonfirmasi konvensi.

## Kapan ditinjau ulang
Setiap kali modul OCA baru tersedia di 19.0 yang bisa menggantikan modul `efn_*`.
