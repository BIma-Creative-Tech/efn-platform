# ADR-001: Satu Database Odoo CE 19 Multi-Company untuk Semua PT

- **Status:** **Accepted** — pemilik proyek, 2026-10-10 (sesi tanya-jawab via efn-lead, butir 5 "ADR Gelombang 1: Accepted semua"; nama & jabatan penandatangan dilengkapi klien di register keputusan). Sebelumnya: Proposed.
  - **Catatan revisi 18 Okt 2026 (butir 18-27, 18-3):** **setiap brand punya PT sendiri.** Struktur company menjadi 6 entitas (Holding EFN, PT Grand Lucky, PT Paris Baguette, Bacha Coffee Retail, Bacha Coffee F&B, FARM). Keputusan inti ADR ini (satu DB multi-company) **tidak berubah** dan status tetap Accepted. Pemetaan 10 Okt di bawah **diralat**; rincian dan dampaknya di bagian "Catatan revisi 2026-10-18" di akhir dokumen.
  - ~~**Catatan penerimaan 10 Okt 2026:** pemetaan brand → PT:~~ *(diralat 18 Okt)* Grand Lucky = PT Retail; Paris Baguette = PT F&B; **Bacha Coffee = dua PT** (butik di PT Retail/PPN, kafe di PT F&B/PB1, POS terpisah). Central Kitchen **bukan** PT terpisah (bagian PT F&B). Transfer pricing antar-PT cost-plus (Q-3) dibangun di `efn_intercompany`. Rincian: `docs/decisions/README.md` bagian "Keputusan pemilik proyek 10 Okt 2026 (sesi tanya-jawab)", `docs/10_RENCANA_GELOMBANG_18_PLUS.md`.
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
- Dampak dokumen: FSD §1 langkah 2 harus ditulis ulang; Blueprint "Modul Custom Multi-Company" diganti menjadi "konfigurasi + OCA + glue module `efn_intercompany`". *(Catatan ADR-031: OCA tidak dipakai; `efn_intercompany` dibangun sendiri.)*

## Catatan revisi 2026-10-18 — struktur company per brand (status tetap Accepted)
Sumber: keputusan pemilik proyek 18 Okt 2026, butir 18-27 (entitas per brand), 18-3 (TP), 18-1 (PB1 dine-in saja), 18-4 (ADR-019 aman), 18-5/18-6 (PPN), 18-9..18-14 (poin), 18-25 (konsolidasi), di `docs/decisions/README.md`. Paket kerja: **W-32** (`docs/10_RENCANA_GELOMBANG_18_PLUS.md` §A).

> **Ringkasan manajemen.** Odoo tetap satu database. Isinya kini enam perusahaan terpisah: Holding EFN, PT Grand Lucky, PT Paris Baguette, Bacha Coffee Retail, Bacha Coffee F&B, dan FARM. Setiap perusahaan punya pajak, buku besar, produk, dan kanal online sendiri. Harga transfer (cost-plus) hanya dipakai antara dua perusahaan Bacha dan antara Holding dengan perusahaan lain. Poin pelanggan tetap satu program untuk semua brand: perusahaan tempat pelanggan berbelanja menanggung poinnya, dan bila poin ditukar di brand lain, perusahaan penerbit membayar ke perusahaan penukar setiap bulan. Karena belum ada data produksi, perubahan ini murah sekarang (**±2–3,5 minggu-engineer**). Setelah database produksi dibuat, perubahannya mahal dan sebagian tidak bisa dibalik (hierarki perusahaan Odoo terkunci).

### 1. Struktur company baru
| Entitas | `efn_brand_code` (usulan, ganti `RTL`/`FNB`) | Usaha | Pajak keluaran | Catatan |
|---|---|---|---|---|
| Holding EFN | `EFN` (tetap) | Induk; pemegang akun gateway & **agen penagih** (ADR-019 A1, 18-4); jasa bersama | PPN atas jasa ke PT (⚠ konsultan pajak) | Bukan penjual barang online |
| PT Grand Lucky | `GL` | Ritel/supermarket | **PPN** | Menggantikan "PT Retail" |
| PT Paris Baguette | `PB` | F&B (bakery/kafe) | **PB1** dine-in; non-dine-in **0 %** (18-1) | **Central Kitchen PB di dalam PT ini** (transfer internal, tanpa TP) |
| Bacha Coffee Retail | `BCR` | Butik ritel kopi | **PPN** | Menerima pasokan dari Bacha F&B = **TP** |
| Bacha Coffee F&B | `BCF` | Kafe | **PB1** dine-in; non-dine-in 0 % | CK Bacha (bila ada) di dalam PT ini (18-27) |
| FARM | `FARM` (tetap) | Collection center / pemasok produk segar | ⚠ **belum ditetapkan** (status PKP, perlakuan PPN produk segar → konsultan pajak, Q-AD1-2) | Pemasok internal ke beberapa PT |

**Pemodelan Odoo (terverifikasi di sumber 19.0):**
- **Enam company root.** Tidak ada yang memakai `parent_id`.
- Di Odoo 19, `res.company.parent_id` berarti **cabang** (`child_ids` berlabel "Branches"). Cabang mewarisi template COA induk (`company.parent_ids[0].chart_template`), field yang didelegasikan ke root (`currency_id`; di akuntansi juga `tax_exigibility`), dan pengecekan lock date lintas `child_of`. **"The company hierarchy cannot be changed"** setelah dibuat (`odoo/addons/base/models/res_company.py`, `addons/account/models/company.py`, branch 19.0).
- Entitas hukum ber-NPWP sendiri **tidak** boleh dimodelkan sebagai cabang Holding. Hubungan grup dicatat di luar hierarki Odoo: konfigurasi `efn_intercompany` + mart konsolidasi.
- Outlet = gudang/`pos.config`/kanal, bukan cabang. **Pengecualian yang perlu fit-gap:** bila Coretax mewajibkan NITKU per outlet, cabang Odoo bisa jadi pilihan untuk outlet di dalam satu PT (Q-AD1-3).
- Produk per company (`product.template.company_id`) + record rule standar. Akses user per company (`company_ids`). Produk yang diperdagangkan antar-entitas (biji kopi Bacha) perlu keputusan fit-gap: produk bersama (`company_id` kosong, terlihat semua entitas) **atau** produk kembar per entitas dengan pemetaan di `efn_intercompany`. Rekomendasi: **produk kembar + pemetaan**, karena 18-27 meminta akses produk dibatasi per PT (Q-AD1-4).

### 2. Transfer pricing & antar-perusahaan (`efn_intercompany`, W-15)
| Pasangan | Perlakuan (18-3) |
|---|---|
| Bacha Coffee F&B ↔ Bacha Coffee Retail | **TP cost-plus**, markup configurable (Finance pusat) |
| Holding ↔ setiap PT | **TP cost-plus** (jasa bersama, fee agen penagih bila ada) |
| CK PB → outlet PB; CK Bacha → kafe Bacha | **transfer internal** (satu company, tanpa TP) |
| **FARM → PT lain; PT ↔ PT antar-brand** (mis. PB membeli dari GL) | **Belum diputuskan.** Menurut 18-3, TP "hanya" untuk Bacha + Holding, sehingga transaksi ini ditafsirkan sebagai jual-beli antar-entitas pada **harga daftar/pasar** (pricelist antar-PT), bukan cost-plus → **Q-AD1-1** (Finance + konsultan pajak) |

Dampak: matriks pasangan entitas di `efn_intercompany` naik dari 2–3 menjadi ≤ 15 pasangan (6 entitas). Hanya pasangan yang dipakai yang dikonfigurasi. Eliminasi konsolidasi dilakukan per pasangan.

### 3. Pajak per entitas
- PPN (GL, BCR, Holding; FARM ⚠): pembulatan per dokumen `round_globally` (18-6). Rumus Coretax 12 % × DPP 11/12 per baris, dengan selisih ±Rp1 ke akun beban *Selisih Pembulatan Pajak* configurable per PT (18-5) → paket **W-34** (fit-gap dulu).
- PB1 (PB, BCF): fiscal position dine-in (PB1 + SC 5 % masuk DPP) dan non-dine-in (**PB1 0 %**, 18-1). Risiko R-L09 **diterima pemilik proyek**, tetapi konfirmasi tertulis konsultan pajak per kota tetap disarankan sebelum go-live. Tarif per kota tetap configurable (ADR-027).
- Coretax/e-Faktur per entitas PKP: NPWP, `l10n_id_tku`, sertifikat/akses Coretax per PT (data klien).

### 4. Kanal online (ADR-034, ADR-019)
- **Kode kanal per PT** (usulan, mengganti `RTL-GL`, `RTL-BACHA`, `FNB-PB`, `FNB-BACHA` di `docs/04 §11.3`): `GL`, `BCR`, `PB`, `BCF`. `brand_code` tetap (`GL`, `BACHA`, `PB`), sehingga katalog domain Bacha menampilkan dua kanal (BCR + BCF).
  - Secret API key per kanal mengikuti pola §11.3 (`odoo_api_key_online_gl`, …).
  - Larangan akhiran `-FILE` (L-46) tetap berlaku.
  - Kanal dev `RTL`/`FNB` diganti data demo per entitas baru.
  - **Keputusan kontrak = lead** (perubahan `docs/04 §1` dan §11.3).
- Unified cart (ADR-019): satu checkout bisa berisi ≤ 4 bagian dari PT penjual berbeda.
  - Holding tetap agen penagih (A1).
  - Settlement harian Holding → 4 PT; MDR proporsional per porsi (Q-ON9).
  - Laporan settlement W-26 kini per 4 PT.
- **Bayar di kasir (W-30, 18-18)** hanya untuk pesanan **satu PT**, karena uang diterima di kasir PT itu. Cart multi-PT wajib bayar online (setelah vendor payment dipilih) atau dipecah menjadi beberapa checkout.

### 5. Member & poin lintas PT (ADR-020)
- **Member tetap satu identitas platform** (BFF, ADR-029) untuk semua brand. **Pengendali data member** (Holding sebagai operator Super-App, atau pengendali bersama 6 entitas) → **Q-AD1-5** (Legal/DPO; masuk DPIA).
- **Penerbit poin = PT tempat transaksi earn** (kolom `company_code` di ledger, usulan ADR-020 butir 1). Liabilitas dicatat di PT penerbit lewat **jurnal akrual harian** dari ledger BFF (18-11, W-29).
- **Redeem di PT lain = alat bayar** "Poin EFN" di PT penukar (sudah G18) → piutang kliring ke PT penerbit. **Settlement bulanan** antar-PT (Q-4) dengan netting oleh Finance pusat.
  - Agar penerbit bisa ditentukan saat redeem, ledger BFF perlu **alokasi FIFO per lot earn** (tabel alokasi redeem → entri earn, append-only).
  - Alokasi yang sama dipakai untuk **breakage 12 bulan** per PT penerbit (18-14, W-17).
  - Perubahan skema BFF (kontrak lead) → W-29.
- Parameter poin **per PT** configurable di Odoo (18-9, W-29).
  - Rekomendasi: **nilai tukar 1 poin (Rp) seragam** untuk semua PT (satu program). Laju earn dan batas boleh berbeda per PT. Bila nilai tukar berbeda per PT, settlement lintas PT menjadi konversi kurs poin (Q-AD1-6, Finance).

### 6. ETL / mart konsolidasi (W-16, 18-25)
- `dim_company` 6 baris + atribut `brand`, `segment` (ritel/F&B/induk/pemasok), `tax_regime` (PPN/PB1).
- Mart konsolidasi GL per entitas + **eliminasi antar-entitas** berdasarkan partner company lawan (6×6) dan penanda `efn_intercompany`.
- Laporan per brand Bacha = gabungan BCR + BCF. Dev saja (tanpa data produksi), jadi data dev dibangun ulang.
- Estimasi data-ai ±0,5–1 minggu (masuk W-32 + W-16).

### 7. Biaya, waktu, dan urutan
- **W-32** ±2–3,5 minggu-engineer:
  - functional 1–1,5 (6 company, COA/pajak/jurnal/metode Poin EFN per PT, data demo);
  - odoo 0,5–1 (kode yang mengasumsikan `RTL`/`FNB`, test);
  - BFF 0,25–0,5 (kode company/kanal, smoke);
  - data-ai 0,25–0,5;
  - QA 0,5.
- **Wajib sebelum DB produksi dibuat** (hierarki & COA tidak bisa diubah setelah ada jurnal) dan sebelum W-29, W-34, W-15.
- **Biaya bila ditunda setelah go-live:** migrasi company + jurnal + stok lintas entitas. Estimasi kasar ≥ 4–8 minggu + risiko audit. Tidak direkomendasikan.

### 8. Pertanyaan terbuka (Q-AD1)
| ID | Pertanyaan | Pemutus | Default sementara |
|---|---|---|---|
| Q-AD1-1 | Harga FARM → PT dan transaksi antar-brand (bukan Bacha/Holding): pricelist antar-PT (harga pasar) atau cost-plus? | Finance + konsultan pajak | pricelist antar-PT, configurable |
| Q-AD1-2 | FARM: PKP? perlakuan PPN produk segar? | Finance + konsultan pajak | tanpa PPN keluaran (placeholder) |
| Q-AD1-3 | Coretax: NITKU per outlet → cabang Odoo per outlet atau cukup TKU di partner/company? | Finance + Tax | tanpa cabang |
| Q-AD1-4 | Produk lintas entitas: kembar + pemetaan (rekomendasi) atau produk bersama? | PO + merchandising + Finance | kembar + pemetaan |
| Q-AD1-5 | Pengendali data member Super-App: Holding atau pengendali bersama? Perjanjian antar-entitas | Legal/DPO | Holding (usulan) |
| Q-AD1-6 | Nilai tukar poin seragam lintas PT? | Finance | seragam |
| Q-AD1-7 | Kode entitas & kanal final (`GL`, `PB`, `BCR`, `BCF`) | PO + lead | usulan di atas |

### 9. Risiko terkait
R-L04 (TP: cakupan lebih sempit, pasangan lebih banyak), R-L03/R-L09 (PB1 non-dine-in 0 % diterima pemilik proyek), R-T11 (beban record rule 6 company, kecil), R-B03 (settlement poin 4–5 PT), R-L01 (pengendali data member lintas entitas).

### 10. Kapan ditinjau ulang
Brand/entitas baru, perubahan status PKP, opini konsultan pajak atas TP/PB1/FARM, atau Coretax mewajibkan NITKU per outlet.
