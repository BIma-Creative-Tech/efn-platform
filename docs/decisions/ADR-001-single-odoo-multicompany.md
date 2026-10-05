# ADR-001: Satu Database Odoo CE 19 Multi-Company untuk Semua PT

- **Status:** Proposed (menunggu persetujuan PO)
- **Tanggal:** 2026-10-05
- **Referensi review:** D-09, D-10, R-T01, §3.4

## Konteks
FSD Detailed Flows §1 menyebut "Odoo F&B" dan "Odoo Supermarket" seolah dua instance terpisah yang dijembatani "Agentic AI". Cetak Biru dan Arsitektur v2 mengasumsikan satu Odoo multi-company. Inter-company transfer, konsolidasi, dan master data bergantung pada keputusan ini.

## Opsi yang dipertimbangkan
1. **Satu DB, multi-company (res.company per PT).** Inter-company via OCA `purchase_sale_inter_company` & `account_invoice_inter_company`. Satu katalog produk. Konsolidasi via `mis_builder`/ClickHouse.
2. **Satu instance Odoo per PT.** Isolasi penuh, tapi inter-company menjadi integrasi API lintas sistem, master data terduplikasi, konsolidasi harus di DWH.
3. **Satu DB per PT dalam satu instance (multi-db).** Tidak ada inter-company bawaan; sama buruknya dengan opsi 2.

## Keputusan
Opsi 1. Automasi inter-company bersifat deterministik (modul Odoo/OCA), bukan AI runtime. Hak akses per company diatur dengan record rules multi-company bawaan Odoo.

## Konsekuensi
- Positif: inter-company, pricelist, katalog, dan laporan lintas PT native. Satu backup, satu upgrade path.
- Negatif: satu insiden DB berdampak ke semua PT; mitigasi dengan replica + backup WAL ke MinIO (R-T08). *Catatan implementasi (2026-10-05):* alat backup yang dipakai adalah **WAL-G**, bukan pgBackRest — repo S3 pgBackRest mensyaratkan HTTPS sedangkan MinIO internal berjalan HTTP di network `data`; WAL-G mendukung endpoint HTTP, enkripsi libsodium, zstd, dan PITR. Pilihan dapat dibalik (hanya image `infra/backup/postgres`). Detail: `docs/runbooks/backup-restore.md`. Performa harus dijaga dengan worker terpisah dan PgBouncer (R-T11).
- Dampak dokumen: FSD §1 langkah 2 harus ditulis ulang; Blueprint "Modul Custom Multi-Company" diganti menjadi "konfigurasi + OCA + glue module `efn_intercompany`".
