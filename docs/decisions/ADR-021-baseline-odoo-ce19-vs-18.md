# ADR-021: Baseline Versi Odoo CE 19 (Fallback 18 Hanya bila PoC Menemukan Blocker)

- **Status:** **Accepted** — pemilik proyek, 2026-10-10 (sesi tanya-jawab via efn-lead, butir 5 "ADR Gelombang 1: Accepted semua"; nama & jabatan penandatangan dilengkapi klien di register keputusan). Sebelumnya: Proposed.
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** IT Lead (dengan masukan Odoo engineer senior)
- **Referensi review:** R-T04 (*"Keputusan di minggu 1"*), R-T03
- **Bukti:** `docs/06_ODOO_CE19_MODULE_AUDIT.md`
- **Catatan:** ADR tambahan di luar D-01..D-16; dibuat karena R-T04 meminta keputusan eksplisit dan audit menemukan kesenjangan OCA/pos 19.0.
- **Catatan ADR-031 (6 Okt 2026):** modul OCA tidak lagi dipasang (keputusan klien). Alasan "modul OCA kunci sudah 19.0" di bawah tidak lagi relevan; keputusan **tetap CE 19** karena alasan lain (umur dukungan, Coretax di CE) dan karena tanpa OCA keunggulan CE 18 (OCA/pos lebih lengkap) juga hilang. Gate PoC tidak lagi memasang OCA multi-company/mis_builder/auditlog; yang diuji adalah CE + `efn_*`.

> **Ringkasan manajemen.** Kami tetap memakai Odoo Community 19 (versi terbaru) karena modul-modul kunci (inter-company, laporan konsolidasi, audit log, pajak Indonesia/Coretax) sudah tersedia untuk versi 19. Kelemahannya, ekstensi POS dari komunitas (OCA) di versi 19 masih sangat sedikit. Pindah ke versi 18 hanya jika uji coba 2 minggu pertama menemukan hambatan serius.

## Konteks
Hasil verifikasi GitHub (2026-10-05):

| Kebutuhan kunci | 19.0 | Catatan |
|---|---|---|
| OCA/multi-company `purchase_sale_inter_company`, `account_invoice_inter_company` | Ada (19.0.1.0.1 / 19.0.1.0.3) | — |
| OCA/mis-builder `mis_builder` | Ada (19.0.1.2.1) | butuh `report_xlsx` (19.0 ada), `date_range` (19.0 ada) |
| OCA/server-tools `auditlog` | Ada (19.0.2.0.0) | — |
| CE `l10n_id`, `l10n_id_efaktur_coretax`, `l10n_id_pos` | Ada | — |
| OCA/pos | **4 modul** di 19.0 vs 28 di 18.0 | ekstensi POS (refund penuh, cash control, margin, payment terminal) belum ada |
| OCA/l10n-indonesia | branch 19.0 **kosong** (hanya `.github`) | CE sudah menutupi kebutuhan inti |

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| A. **CE 19** | Umur dukungan lebih panjang; Coretax di CE; modul OCA kunci sudah 19.0 | Ekstensi OCA/pos sedikit → beberapa fitur POS ditulis/di-port sendiri | Porting per modul OCA kecil ±2–5 hari |
| B. CE 18 | OCA/pos jauh lebih lengkap | Upgrade 18→19 dalam ±1–2 tahun (proyek tersendiri); dukungan Odoo S.A. untuk 18 berakhir lebih dulu | Upgrade nanti ±4–8 minggu (asumsi) |

## Keputusan
Opsi A, dengan **gate PoC 2 minggu** (fase 0): install `point_of_sale`, `pos_restaurant`, `pos_self_order`, `pos_loyalty`, `mrp`, `pos_mrp`, `l10n_id*`, OCA multi-company + mis_builder + auditlog di sandbox 19; jalankan skenario kitting (ADR-018), inter-company, dan POS offline. Fallback ke 18 hanya jika ada blocker pada inter-company/akuntansi, bukan karena kurangnya ekstensi POS kecil.

## Konsekuensi
- **Positif:** tidak ada upgrade mayor di tahun pertama.
- **Negatif:** backlog porting/penulisan ekstensi POS (lihat `docs/06`).

## Risiko terkait
R-T04, R-T03, R-B09.

## Pertanyaan terbuka untuk klien
- **Q-53** ketersediaan Odoo engineer senior untuk porting.

## Kapan ditinjau ulang
Di akhir PoC fase 0, lalu setiap kuartal (cek progres OCA 19.0).
