# REVIEW DOKUMEN, RISK REGISTER & GAP ANALYSIS
## Platform Era Food and Nourishment (EFN)

**Tanggal review:** 5 Oktober 2026
**Dokumen yang direview:**
1. `efn_master_blueprint_and_concept.md` (Blueprint v2.0)
2. `efn_brd_business_requirements_document.md` (BRD v1.0)
3. `efn_fsd_detailed_flows.md` (FSD Detailed Flows)
4. `project_documentation_suite_efn.md` (Charter + BRD + FSD + TSD ringkas)
5. `efn_opensource_architecture_v2.md` (Arsitektur Open Source v2)
6. `cetak_biru_arsitektur_enterprise_...efn.md` (Cetak Biru Enterprise v1.0)

**Cara membaca dokumen ini:**
- Bagian 1: kesimpulan eksekutif.
- Bagian 2: inkonsistensi antar-dokumen yang harus diputuskan satu versinya (decision log).
- Bagian 3: kesalahan konsep teknis yang akan menjadi masalah saat implementasi.
- Bagian 4: risk register lengkap (bisnis, teknis, legal/regulasi, keamanan, operasional).
- Bagian 5: gap kebutuhan dan daftar pertanyaan untuk digali bersama stakeholder.
- Bagian 6: rekomendasi arsitektur target yang sudah dikonsolidasikan.
- Bagian 7: usulan re-phasing roadmap.

---

## 1. KESIMPULAN EKSEKUTIF

Visi bisnisnya jelas dan kuat: holding multi-PT F&B + Retail, tiga pilar (Price Hunter, Farming Hub, MGM 2-Tier), stack 100% open source. Namun enam dokumen ini ditulis pada waktu berbeda dan **belum pernah direkonsiliasi**. Ada minimal 14 titik di mana dokumen saling bertentangan, dan 6 kesalahan konsep teknis yang jika dibawa ke tim developer akan menghasilkan sistem yang salah.

Tiga temuan paling kritis:

| # | Temuan | Dampak jika tidak ditangani |
|---|---|---|
| 1 | **Konsep "Phantom BoM" di FSD salah.** Phantom/Kit BoM di Odoo tidak membuat Manufacturing Order dan justru memotong stok bahan mentah di POS, kebalikan dari yang diinginkan. | Modul Kitting dibangun di atas mekanisme yang salah; outlet tetap harus stock opname bahan mentah. |
| 2 | **OTP gating bertabrakan dengan POS offline-first.** Validasi OTP butuh Redis + WhatsApp online; POS offline tidak bisa memverifikasi. | Fitur anti-fraud hilang saat offline, atau kasir tidak bisa transaksi saat internet putus. Harus ada keputusan bisnis. |
| 3 | **Satu keranjang, dua entitas hukum (PT).** Unified Cart menghasilkan satu pembayaran untuk tagihan milik dua PT berbeda. Belum ada desain untuk pemecahan settlement, faktur pajak per PT, dan perbedaan pajak (PB1 restoran vs PPN retail). | Masalah akuntansi, perpajakan, dan rekonsiliasi payment gateway sejak hari pertama go-live. |

"Zero license fee" dapat dicapai. "Zero cost" tidak. Biaya WhatsApp Business API, payment gateway, 3PL, server GPU, dan app store adalah biaya operasional yang perlu masuk ke business case agar ekspektasi manajemen realistis.

---

## 2. INKONSISTENSI ANTAR-DOKUMEN (DECISION LOG)

Setiap baris butuh **satu keputusan** dari product owner. Kolom "Rekomendasi" adalah usulan saya.

| ID | Topik | Versi A | Versi B | Versi C | Rekomendasi |
|---|---|---|---|---|---|
| D-01 | API Gateway | Node.js Express/NestJS (Blueprint, Charter) | Golang (Arch v2) | Kong (Cetak Biru) | **Traefik** sebagai edge/reverse proxy (TLS, routing, rate limit) + **NestJS** sebagai BFF/orchestrator. Kong menambah komponen tanpa kebutuhan nyata di skala awal. |
| D-02 | Interval ETL | 10 menit (BRD, Arch v2) | 15 menit (TSD) | Debezium CDC (Cetak Biru) | Mulai **batch 15 menit** (sederhana, mudah di-debug). CDC Debezium di fase 2 jika kebutuhan near-real-time terbukti. |
| D-03 | Topologi infra | 3 server VPS (Arch v2) | 4 node (TSD) | Kubernetes EKS/GKE + Cloudflare (Cetak Biru) | **4 node VPS/bare-metal + Docker Compose/Swarm**. EKS/GKE bertentangan dengan arahan "hindari AWS/GCP". K8s managed baru masuk akal di >20 node. |
| D-04 | Timeline | 8 bulan, 4 fase (Charter) | 9 bulan, 4 fase dengan isi berbeda (Cetak Biru) | - | Lihat Bagian 7. Keduanya terlalu agresif untuk cakupan ini. |
| D-05 | Reward Price Hunter | e-Voucher Rp2.000 (FSD) | Poin EFN (Charter) | EFN Coin/Points (Cetak Biru) | **Poin** ke loyalty wallet tunggal. Voucher adalah salah satu cara menukar poin. Satu ledger, bukan dua. |
| D-06 | Penyimpanan data Price Hunter | Insert langsung ke ClickHouse (FSD) | PostgreSQL (Arch v2) | - | **PostgreSQL** sebagai source of truth (transaksional, bisa di-audit/retur), ETL ke ClickHouse. Jangan tulis langsung ke DWH dari API. |
| D-07 | Engine OCR | Llama 3.2 Vision (Blueprint, BRD) | LLaVA / Tesseract (Arch v2, TSD) | - | Pipeline 2 tahap: **PaddleOCR/Tesseract** (cepat, CPU) untuk ekstraksi teks, lalu **VLM kecil** (Qwen2.5-VL 3B/7B atau Llama 3.2 Vision 11B) hanya jika confidence rendah. Lihat R-T05. |
| D-08 | Parameter MGM | Tidak didefinisikan (BRD) | 5% Tier-1, 2% Tier-2 cashback (Cetak Biru) | - | Harus diputuskan finance. 7% total komisi dari order value sangat besar untuk margin groceries (umumnya 15-25% gross). Lihat R-B03. |
| D-09 | Arsitektur Odoo multi-PT | Satu Odoo multi-company (Cetak Biru, Arch v2) | "Odoo F&B" dan "Odoo Supermarket" sebagai instance terpisah (FSD §1 langkah 2) | - | **Satu database Odoo, multi-company**. Instance terpisah membuat inter-company jadi integrasi API antar-sistem, 10x lebih rumit. |
| D-10 | Peran AI saat runtime | AI tidak berjalan di sistem live (TSD §4.2) | "Agentic AI mendeteksi permintaan lintas-PT dan men-generate PO/SO" (FSD §1 langkah 2) | - | AI **tidak** di jalur transaksi. Inter-company PO/SO adalah automasi deterministik (OCA `purchase_sale_inter_company`; **diperbarui ADR-031: `efn_intercompany`**). AI hanya untuk OCR dan Text-to-SQL. |
| D-11 | Kanal WhatsApp | Provider murah / Baileys open source (Arch v2) | WhatsApp API (TSD) | - | **WhatsApp Cloud API resmi (Meta)** via BSP. Baileys adalah library unofficial, melanggar ToS WhatsApp, nomor bisa diblokir. Tidak boleh dipakai untuk mekanisme anti-fraud produksi. |
| D-12 | Message broker | Tidak ada (Blueprint, Arch v2, TSD) | RabbitMQ/Kafka (Cetak Biru) | - | **RabbitMQ** diperlukan (OCR async, notifikasi, kalkulasi MGM, webhook 3PL). Kafka berlebihan di skala awal. |
| D-13 | Scope POS F&B | KDS, ODS, Kiosk, Booking, Waitlist (BRD, Cetak Biru) | Tidak dirinci (Charter) | - | Verifikasi ketersediaan modul di **Odoo CE 19** (bukan Enterprise). Lihat R-T03. |
| D-14 | Hosting AI | "100% offline/air-gapped" (TSD) | Menerima request HTTP dari API Gateway (FSD) | - | Istilah yang benar: **jaringan privat tanpa egress internet**. Air-gapped berarti tidak bisa dipanggil API Gateway. |
| D-15 | Nama custom module | 4 modul (Blueprint: Multi-Company, MGM, Kitting, OTP Interceptor) | 5 modul `custom_efn_*` (Cetak Biru) | - | Gunakan penamaan Cetak Biru dengan prefix `efn_` (konvensi Odoo), dan petakan ke OCA dulu sebelum menulis dari nol. **Diperbarui ADR-031 (keputusan klien 6 Okt 2026):** tidak ada dependency modul Odoo pihak ketiga; urutan = Standar CE → di luar Odoo (BFF/ETL/Metabase) → `efn_*` sendiri (LGPL-3), OCA & Cybrosys hanya referensi desain. |
| D-16 | Payment | Tidak disebut (BRD, Charter) | Midtrans, Xendit, QRIS, VA, e-wallet (Cetak Biru) | - | Pilih **satu** gateway utama (Xendit atau Midtrans) di fase 1. Keduanya sudah mencakup QRIS, VA, dan e-wallet. |

---

## 3. KESALAHAN KONSEP TEKNIS YANG HARUS DIKOREKSI

### 3.1 Phantom BoM vs Manufacturing BoM (FSD §1)

**Yang ditulis:** Goods receipt bahan mentah di Central Kitchen memicu MO berstatus Done, mengubah 20 kg ayam + 5 kg tepung menjadi 100 "Paket Ayam Geprek" via "Phantom BoM effect".

**Faktanya di Odoo:**
- **Phantom (Kit) BoM** tidak pernah membuat Manufacturing Order. Produk kit tidak punya stok sendiri. Saat dijual (termasuk di POS), Odoo meng-explode kit dan memotong stok komponen. Ini **persis kebalikan** dari tujuan bisnis (outlet tidak boleh berurusan dengan gramasi).
- Yang dibutuhkan adalah **BoM tipe Manufacture** di Central Kitchen, dengan MO dibuat dan dikonfirmasi (bisa otomatis via automated action atau MRP scheduler). Produk "Paket Ayam Geprek" menjadi **storable product** dengan stok nyata di CK dan outlet.
- Pengiriman CK ke outlet adalah **internal transfer** (atau inter-company jika PT berbeda) dari produk paket, bukan komponen.

**Koreksi desain:**
```
Supermarket PT (raw) --inter-company SO/PO--> Central Kitchen PT (raw)
Central Kitchen: MO (Manufacture BoM) raw -> "Paket Siap Olah" (storable, UoM Porsi)
Central Kitchen --internal/inter-company transfer--> Outlet (stok "Paket")
POS Outlet: jual menu -> potong 1 Porsi "Paket" (produk storable biasa, tanpa BoM di outlet)
```
Jika satu menu memakai beberapa paket (misal Ayam Geprek + Nasi + Es Teh), **di sinilah** Phantom BoM tepat digunakan: menu POS = kit dari beberapa produk paket.

### 3.2 OTP Gating vs POS Offline-First (BRD §7.2 vs FSD-2.1)

Odoo POS memang bisa transaksi offline, tetapi semua yang bergantung pada server (OTP ke Redis, kirim WhatsApp, cek saldo poin) tidak tersedia. Pilihan bisnis yang harus diambil:
- **(a)** Saat offline, tombol promo/MGM/redeem poin **dinonaktifkan**. Transaksi reguler tetap jalan. *(Rekomendasi: paling aman.)*
- **(b)** Saat offline, promo diizinkan dengan "soft lock" (foto KTP/manual approval manager) dan direkonsiliasi saat online. Membuka celah fraud yang persis ingin ditutup.
- **(c)** Pelanggan generate TOTP dari aplikasi (tanpa server) yang bisa diverifikasi offline oleh POS dengan shared secret per member. Secara teknis bisa, tapi butuh desain kripto yang hati-hati dan hanya bekerja untuk member yang punya app.

### 3.3 Unified Cart lintas PT (FSD-1.1)

Satu checkout berisi barang dari PT Retail dan PT F&B. Konsekuensi yang belum dibahas di dokumen mana pun:
- **Pajak berbeda**: F&B restoran dikenai **PB1 / Pajak Restoran (pajak daerah, umumnya 10%)**, bukan PPN. Retail groceries dikenai **PPN** (sebagian bahan pokok dibebaskan). Struk/faktur harus dipisah per PT.
- **Settlement payment gateway**: satu pembayaran masuk ke rekening siapa? Perlu salah satu: (i) entitas holding sebagai *merchant of record* lalu inter-company billing ke tiap PT, (ii) fitur split settlement gateway (Xendit `split payment`/Midtrans tidak semua mendukung), atau (iii) dua tagihan terpisah di UI.
- **Refund parsial**: retur barang retail tidak boleh mengembalikan uang dari rekening PT F&B.
- **Poin lintas-brand** = kewajiban (liability) antar-PT. Perlu ledger poin terpusat, perlakuan akuntansi, masa kedaluwarsa, dan mekanisme tagih-menagih antar PT setiap periode.

### 3.4 Multi-company vs multi-instance Odoo (FSD §1)

Jika tiap PT adalah instance Odoo terpisah, tidak ada "inter-company rule" bawaan apa pun; semuanya menjadi integrasi API antar-sistem dengan masalah idempotency, retry, dan rekonsiliasi master data. Satu database multi-company adalah satu-satunya jalur yang realistis. Catatan: modul `sale_purchase_inter_company` dan `account_invoice_inter_company` tersedia di **OCA/multi-company** (open source, AGPL), sehingga modul "Multi-Company" tidak perlu ditulis dari nol. *(Diperbarui ADR-031, 6 Okt 2026: modul OCA tidak dipasang; inter-company dibangun sebagai `efn_intercompany` dengan OCA & Cybrosys `inter_company_synchronization` sebagai referensi, ±4–6 minggu.)*

### 3.5 Target latency OCR < 5 detik tanpa GPU (BRD §7.1)

Llama 3.2 Vision 11B pada CPU memakan 30-120 detik per gambar. Target <5 detik membutuhkan GPU minimal 12-16 GB VRAM (contoh kelas RTX 4070 Ti/A10) atau pergeseran ke pipeline OCR klasik (PaddleOCR ~0.5-2 detik di CPU). Arsitektur Arch v2 §4 sudah menyinggung ini, tapi BRD tetap mematok KPI tanpa syarat hardware. Selain itu, proses harus **asynchronous** (antre di RabbitMQ, pelanggan diberi notifikasi), bukan request-response sinkron seperti di FSD §2.

### 3.6 Konsolidasi keuangan lintas-PT real-time (BRD §5.1)

Odoo CE tidak memiliki modul konsolidasi (`account_consolidation` adalah Enterprise). Opsi open source: **OCA `mis_builder`** untuk laporan multi-company, atau konsolidasi di layer ClickHouse/Metabase. "Real-time" harus diturunkan menjadi "near real-time (15 menit)" selaras dengan D-02. *(Diperbarui ADR-031: `mis_builder` tidak dipasang; konsolidasi di ClickHouse/Metabase. Laporan keuangan per PT untuk akuntan menunggu Q-V3.)*

---

## 4. RISK REGISTER

Skala: **Probabilitas** (P) dan **Dampak** (D) 1-5. **Skor** = P × D. Prioritas: ≥15 Kritis, 10-14 Tinggi, 5-9 Sedang, <5 Rendah.

### 4.1 Risiko Bisnis & Model Operasi

| ID | Risiko | P | D | Skor | Mitigasi |
|---|---|---|---|---|---|
| R-B01 | **Cakupan terlalu luas untuk 8-9 bulan.** ERP multi-PT, 2 jenis POS, KDS/ODS/Kiosk, Super-App web+mobile, brand apps, farming, price hunter, MGM, DWH, AI. Setara 4-5 produk sekaligus. | 5 | 5 | 25 | Re-phasing (Bagian 7). Tentukan MVP per pilar. Go-live bertahap per brand/outlet. |
| R-B02 | **Ekspektasi "zero cost"** padahal ada OpEx: WhatsApp BSP (~Rp300-600/pesan utility), fee payment gateway (0,7%-2,9%), komisi 3PL (GoFood 20%+), GPU server, Apple Developer ($99/th), Google Play ($25), domain/SSL, SMS fallback. | 4 | 3 | 12 | Buat TCO 3 tahun yang memisahkan "license fee" (Rp0) dari "operating cost". Presentasikan ke manajemen sebelum sign-off. |
| R-B03 | **MGM 5%+2% menggerus margin.** Komisi 7% dari order value groceries dengan gross margin 15-25% menyisakan margin tipis setelah promo dan ongkir. | 4 | 4 | 16 | Finance mendefinisikan: basis komisi (net margin vs order value), cap per bulan, kategori yang dikecualikan (rokok, susu formula, barang promo), masa aktif referral (misal 12 bulan). Simulasikan di spreadsheet sebelum coding. |
| R-B04 | **Ketergantungan pada approval partner API GoFood/GrabFood/ShopeeFood.** API mereka tidak publik; butuh program partner/aggregator berbayar. | 4 | 4 | 16 | Mulai dengan GoSend/GrabExpress/Lalamove (API delivery publik). Untuk food marketplace, evaluasi aggregator POS (berbayar) vs input manual ke KDS. Masukkan sebagai risiko eksternal di charter. |
| R-B05 | **Fraud Price Hunter**: foto berulang, foto dari internet, foto hasil edit/AI, kolusi antar akun untuk "menambang" poin. | 4 | 3 | 12 | Perceptual hash untuk dedup, EXIF/geofence (jarak ke toko kompetitor), cap harian per user, rate limit, review sampling 5% oleh admin, reward kecil di awal, blacklist. |
| R-B06 | **Fraud MGM**: self-referral, akun sybil (banyak nomor HP), referral circular. | 4 | 3 | 12 | Verifikasi nomor via OTP saat daftar, device fingerprint, 1 nomor = 1 akun, reward baru cair setelah referral bertransaksi minimal X dan tidak retur H+N, graph analysis sederhana untuk circular pattern. |
| R-B07 | **Adopsi Farmer Portal rendah** (literasi digital petani, sinyal di area pertanian). | 4 | 3 | 12 | Fase 1: aplikasi QC Intake dipakai **staf EFN** di collection center, bukan petani. Petani cukup punya kartu/QR identitas. Portal petani adalah fase lanjut. |
| R-B08 | **Change management** di outlet: kasir, kitchen, warehouse harus belajar sistem baru serentak. | 3 | 4 | 12 | Pilot 1 outlet F&B + 1 toko retail. SOP, training, super-user per outlet, parallel run 2 minggu. |
| R-B09 | **Tim internal mengandalkan "Agentic AI" untuk coding modul Odoo** tanpa Odoo engineer senior yang mereview. | 4 | 4 | 16 | AI sebagai asisten, bukan pengganti. Wajib: code review manusia, test otomatis, staging. Rekrut/kontrak minimal 1 Odoo engineer berpengalaman multi-company & MRP. |
| R-B10 | **Tidak ada product owner tunggal** yang memutuskan konflik 16 item di Bagian 2. | 3 | 4 | 12 | Tunjuk PO + arsitek. Semua keputusan dicatat di `docs/decisions/` (ADR). |
| R-B11 | **Bus factor kunci = 1 Superadmin** (Q-E1a/b/c, diterima pemilik proyek 10 Okt 2026): satu orang memegang root CA, kunci PII, WAL-G, MinIO KMS, LUKS, pepper; salinan offline tersegel hanya di **brankas Direksi** (satu lokasi). Bila orang itu sakit/keluar/tidak terjangkau saat insiden: rotasi kunci, restore backup, dan buka disk LUKS tertunda; kehilangan salinan = kehilangan data permanen. *(Baru 10 Okt 2026.)* | 2 | 5 | 10 | **Risiko diterima pemilik proyek.** Mitigasi tersisa (ADR-022 §Kustodi): salinan tersegel bernomor + log pembukaan segel; prosedur serah terima saat Superadmin keluar (rotasi semua kunci yang dapat dirotasi); break-glass dengan alarm; uji pemulihan 2×/tahun **disaksikan** Direksi/Internal Audit; runbook `key-custody.md` cukup rinci agar orang baru dapat menjalankan pemulihan dengan salinan brankas. Tinjau ulang sebelum go-live publik atau saat Superadmin cuti panjang. Pemilik: Direksi + Superadmin. Terkait R-T08. |

### 4.2 Risiko Teknis & Arsitektur

| ID | Risiko | P | D | Skor | Mitigasi |
|---|---|---|---|---|---|
| R-T01 | **Phantom BoM salah konsep** (§3.1). | 5 | 4 | 20 | Koreksi FSD sebelum development. Proof of concept 2 hari di Odoo CE 19 sandbox. |
| R-T02 | **OTP vs offline POS** (§3.2). | 5 | 4 | 20 | Keputusan bisnis opsi (a)/(b)/(c). Dokumentasikan di ADR. |
| R-T03 | **Fitur POS yang diasumsikan ada di CE 19 ternyata Enterprise** (Kitchen Display / `pos_preparation_display`, table booking/appointment, loyalty/gift card tertentu, IoT box). | 4 | 4 | 16 | Audit modul per fitur pada Odoo CE 19 aktual di minggu 1. Cari padanan OCA (`pos` repo) atau rencanakan custom. Kiosk `pos_self_order` ada di CE. |
| R-T04 | **Odoo 19 CE masih baru** (rilis akhir 2025): OCA modules multi-company/pos/mrp belum semua dimigrasi ke 19. | 4 | 3 | 12 | Cek status migrasi OCA untuk tiap modul yang dibutuhkan. Fallback: Odoo 18 CE (OCA lebih matang) atau porting sendiri. Keputusan di minggu 1. **Diperbarui ADR-031 (6 Okt 2026):** OCA tidak lagi dipasang, sehingga risiko bergeser dari "menunggu migrasi OCA" ke **kapasitas engineer EFN** untuk modul `efn_*` pengganti (audit log, inter-company, laporan): +10–16 minggu-engineer. Lihat usulan R-T13 di ADR-031. |
| R-T05 | **OCR < 5 detik tidak tercapai tanpa GPU** (§3.5). | 4 | 3 | 12 | Pipeline async + OCR klasik dulu. GPU bila volume >1.000 foto/hari. Ubah KPI: "hasil validasi diterima pelanggan ≤ 60 detik". |
| R-T06 | **Text-to-SQL menghasilkan jawaban salah** yang dipakai manajemen untuk keputusan. | 4 | 3 | 12 | Read-only user ClickHouse, whitelist tabel/view (semantic layer), tampilkan SQL yang dijalankan, label "AI-generated, verifikasi di dashboard", batas waktu query, logging. |
| R-T07 | **Uptime 99.9% tidak tercapai** dengan Odoo+PostgreSQL single node (budget downtime 43 menit/bulan). | 4 | 3 | 12 | Fase 1 target 99.5% (3,6 jam/bulan). PostgreSQL streaming replica + failover (Patroni/repmgr) sebelum klaim 99.9%. |
| R-T08 | **Tidak ada RPO/RTO, strategi backup, dan DR** di seluruh dokumen. | 5 | 4 | 20 | Definisikan: RPO 15 menit (WAL archiving ke MinIO), RTO 4 jam. Backup harian terenkripsi, restore test bulanan. |
| R-T09 | **Tidak ada observability** (log, metric, trace, alert). | 5 | 3 | 15 | Prometheus + Grafana + Loki + Alertmanager masuk fase 0. Uptime Kuma untuk sintetik. |
| R-T10 | **Split order + 3PL + payment adalah distributed transaction** (bayar sukses, 3PL gagal, stok sudah dipotong). | 4 | 4 | 16 | Saga/outbox pattern via RabbitMQ, status order eksplisit (`paid`, `dispatch_pending`, `dispatch_failed`), idempotency key di semua webhook, kompensasi otomatis. |
| R-T11 | **Odoo sebagai backend POS + e-commerce + ERP dalam satu proses** menyebabkan POS lambat saat ETL/laporan berat. | 3 | 4 | 12 | Pisahkan worker Odoo (HTTP vs cron vs longpolling), PgBouncer, ETL membaca dari **replica**, bukan primary. |
| R-T12 | **Monorepo tanpa disiplin** (Odoo Python, NestJS, Next.js, RN, ETL Python) membuat CI lambat dan rilis saling menunggu. | 3 | 3 | 9 | Path-filtered workflows (sudah disiapkan di `.github/workflows/`), versioning per service, container image per service. |
| R-T13 | **Kapasitas & retensi Odoo engineer** — ADR-031 memindahkan audit log, inter-company, dan laporan ke modul `efn_*` sendiri (±10–16 minggu-engineer) plus porting tiap upgrade mayor Odoo. | 4 | 4 | 16 | Minimal 2 Odoo engineer yang paham modul `efn_*` (bus factor), dokumentasi & test wajib per modul, estimasi divalidasi Odoo engineer senior (Q-53), anggarkan porting tiap rilis mayor. |
| R-T16 | *(dahulu R-T13 ganda; dinomori ulang 10 Okt 2026 — tidak ada rujukan lain)* **React Native + Next.js PWA "hybrid"** tidak jelas: dua codebase mobile atau satu? | 3 | 3 | 9 | Putuskan: **Expo (React Native) untuk mobile**, Next.js untuk web. Shared package untuk API client & design tokens. |
| R-T14 | **Multi-tenant brand frontends** (satu codebase banyak brand) tanpa desain tema/konfigurasi. | 3 | 3 | 9 | Config-driven theming per brand (domain → brand_id → theme + catalog scope). Uji dengan 2 brand di fase awal. |
| R-T15 | **API key kanal online Odoo kedaluwarsa/dicabut** (user teknis `efn_online_<kanal>`, masa berlaku `api_key_duration` grup, PLACEHOLDER 90 hari) → BFF tidak bisa memanggil Odoo: katalog basi, quote/checkout 503, event status tetap masuk tetapi rekonsiliasi pull gagal. POS tidak terdampak. *(Baru 10 Okt 2026, dari L-6 / ADR-034.)* | 3 | 3 | 9 | Masa berlaku key dikendalikan grup teknis; rotasi dua kunci (ADR-024) lewat `docs/runbooks/odoo-online-api-key.md`; alert sisa umur key (`EfnOnlineApiKeyExpiringSoon` / `EfnOnlineApiKeyExpiryCritical` / `EfnOnlineApiKeyExpiryUnknown`, metrik textfile) dan `EfnOnlineOdooAuthFailure` (critical); `deploy.sh` menolak key placeholder saat online prod. Pemilik: Superadmin (pemegang rotasi — keputusan IT terbuka, `docs/decisions/README.md` Fase 3). **Diperbarui 10 Okt 2026 (pemilik proyek):** masa berlaku **30 hari** + **rotasi otomatis** oleh timer host Superadmin (key baru → secret → recreate BFF → cabut key lama setelah overlap ±1 jam, gagal = alert) — paket W-11. Probabilitas kedaluwarsa turun, tetapi frekuensi rotasi naik 3× → kegagalan otomasi harus terdeteksi (alert). |
| R-T17 | **Perubahan presisi IDR global ke Rp1** (Q-ON6, 10 Okt 2026) menimbulkan regresi akuntansi & POS: pembulatan pajak per baris vs global berubah, selisih Rp1 pada PPN/PB1/SC, nilai poin (`value_idr`), pembulatan kas, rekonsiliasi BFF ↔ Odoo (`amount_payable`, porsi per PT), ETL/ClickHouse desimal, dan test yang mengharapkan 2 desimal. Bila dilakukan **setelah** ada jurnal produksi: saldo lama berdesimal, migrasi berisiko. *(Baru 10 Okt 2026.)* | 3 | 4 | 12 | Kerjakan **sebelum DB produksi dibuat** (paket W-03, G18): `efn_setup_company` menetapkan `rounding=1` IDR; fit-gap efn-odoo-functional atas pembulatan pajak (`tax_calculation_rounding_method`) dan cash rounding; regresi penuh semua PT (unit, tour POS, smoke online, rekonsiliasi poin & ETL); guard CI bahwa DB baru memakai presisi 1. Pemilik: efn-odoo-functional + QA. |

### 4.3 Risiko Legal, Regulasi & Kepatuhan (Indonesia)

| ID | Risiko | P | D | Skor | Mitigasi |
|---|---|---|---|---|---|
| R-L01 | **UU PDP No. 27/2022** (berlaku penuh Okt 2024): data pelanggan, nomor WA, lokasi, foto. Butuh dasar pemrosesan, consent, retensi, hak hapus, notifikasi kebocoran 72 jam, DPO. | 4 | 4 | 16 | Privacy notice di app, consent granular (lokasi, marketing), data retention policy, DSAR flow (export/hapus akun), enkripsi at-rest, DPO ditunjuk. |
| R-L02 | **MGM 2-tier bisa dikategorikan penjualan langsung berjenjang** (Permendag 70/2019 & aturan SIUPL) jika reward berasal dari transaksi downline. | 3 | 4 | 12 | Konsultasi legal sebelum launch. Struktur reward sebagai *cashback promosi* dengan cap, bukan "komisi", dan tidak ada biaya keanggotaan. |
| R-L03 | **Pajak**: PB1 (pajak restoran, daerah) vs PPN (pusat), e-Faktur/Coretax per PT, kode transaksi, NSFP. | 4 | 4 | 16 | Konfigurasi pajak per company di Odoo (`l10n_id` ada di CE), faktur terpisah per PT, integrasi Coretax (export CSV/XML) di fase 2. Konsultasi konsultan pajak. |
| R-L04 | **Transfer pricing antar-PT** (FSD-3.2) harus wajar (arm's length) untuk pajak. | 3 | 3 | 9 | Finance menetapkan metode (cost plus X%). Dokumentasikan. **Diperbarui 10 Okt 2026:** metode = **cost-plus** (markup ditetapkan Finance pusat) untuk transaksi antar-PT; CK berada di PT F&B (bukan PT) sehingga CK → outlet F&B = transfer internal tanpa TP; TP berlaku mis. Holding ↔ PT dan CK → butik Bacha (PT Retail). Konfirmasi konsultan pajak tetap diperlukan (paket W-15). |
| R-L05 | **PCI DSS** jika menyimpan data kartu. | 2 | 5 | 10 | Jangan pernah menyimpan PAN. Gunakan tokenisasi gateway (SAQ A). |
| R-L06 | **Memotret di toko kompetitor** bisa melanggar tata tertib toko; menampilkan nama kompetitor + harga bisa memicu sengketa. | 2 | 3 | 6 | Tampilkan "harga pasar" teragregasi, bukan per-nama-toko ke publik. Terms of use untuk kontributor. |
| R-L07 | **Keamanan pangan & traceability** (BPOM, halal): lot/expiry tracking untuk fresh product. | 3 | 3 | 9 | Aktifkan `product_expiry` dan lot tracking (CE). QR traceability di fase lanjut. |
| R-L08 | **Lisensi AGPL Odoo/OCA**: modul custom yang di-deploy sebagai layanan jaringan tunduk AGPL (source harus tersedia bagi pengguna jaringan jika didistribusikan). | 2 | 2 | 4 | Untuk penggunaan internal EFN tidak masalah. Catat di `LICENSES.md`. Jangan campur kode proprietary ke dalam addon Odoo jika akan dikomersialkan. **Diperbarui ADR-031:** tidak ada modul AGPL pihak ketiga yang dipasang; semua `efn_*` LGPL-3. Risiko baru: kode OCA (AGPL/LGPL) atau Cybrosys (LGPL/AGPL/OPL-1) tersalin ke `efn_*` → panduan clean implementation + bagian "Referensi" di README modul. |
| R-L09 | **Pajak daerah (PBJT/PB1) atas penjualan F&B non-dine-in.** Jawaban pemilik proyek 10 Okt 2026 (P-12): "selain dine-in tidak dikenakan pajak" → takeaway/3PL/pickup tanpa PB1. Di bawah UU 1/2022 (HKPD), PBJT makanan/minuman umumnya juga terutang atas penjualan restoran yang dibawa pulang; pengecualian bergantung Perda kota. Bila Perda mengenakan PBJT: kurang setor, sanksi/denda, koreksi struk & laporan setoran, dan harga online F&B harus berubah. *(Baru 10 Okt 2026.)* | 3 | 4 | 12 | Konfigurasi sebagai fiscal position per layanan (ADR-027, tanpa tarif di kode) sehingga perubahan = setelan, bukan rilis; **opini konsultan pajak per kota outlet sebelum go-live F&B** (syarat W-14); default aman yang diusulkan bila opini belum ada: PB1 berlaku juga untuk takeaway (lebih konservatif) — keputusan Finance. Pemilik: Finance + Tax. Terkait R-L03. |
| R-L10 | **PII pembeli tersimpan di Odoo** (Q-ON7/Q-P21, 10 Okt 2026): nama, NPWP, alamat, HP, email pembeli online yang meminta faktur masuk `res.partner` Odoo — menyimpang dari prinsip minimisasi §10.1 butir 4 (partner generik). Risiko: akses staf Odoo luas (Sales/Accounting lintas PT), erasure tidak lengkap (faktur 10 th), kebocoran lewat ekspor/laporan/ETL, dan staf mengetik PII di chatter. *(Baru 10 Okt 2026.)* | 3 | 4 | 12 | Disimpan **hanya atas permintaan pelanggan** (opsional di checkout); kolom PII **terenkripsi** (Q-E3, paket W-08) **sebelum** W-06 diaktifkan; record rule per PT; tidak disalin ke ClickHouse; erasure menganonimkan partner pembeli kecuali data wajib faktur 10 th; inventaris PDP v7 §2.2d; tinjauan Legal (DPO sementara) + review keamanan sebelum aktif. Pemilik: Legal/DPO + odoo-engineer. Terkait R-L01, R-S10. |

### 4.4 Risiko Keamanan Siber

| ID | Risiko | P | D | Skor | Mitigasi (ditanamkan dalam pipeline DevSecOps) |
|---|---|---|---|---|---|
| R-S01 | **Secrets di repo** (API key gateway, token WA, DB password). | 4 | 5 | 20 | Gitleaks di pre-commit + CI (sudah di `security.yml`). Secrets di GitHub Environments + Docker secrets/SOPS. |
| R-S02 | **Dependency vulnerabilities** (npm, pip, image base). | 5 | 3 | 15 | Dependabot, Trivy fs & image, `npm audit`, `pip-audit`. Gate: CRITICAL/HIGH memblokir merge. |
| R-S03 | **Odoo di-expose langsung ke internet** (XML-RPC, `/web/database/manager`). | 4 | 5 | 20 | Odoo hanya di jaringan internal; publik lewat Traefik dengan path allowlist; `list_db = False`; database manager dinonaktifkan; admin password kuat. |
| R-S04 | **Brute force OTP / enumeration nomor HP** di `request-otp`. | 4 | 3 | 12 | Rate limit per IP & per nomor (Traefik + Redis), OTP 6 digit TTL 3 menit, max 3 attempt (sudah di BRD), captcha setelah 3 request, jangan bocorkan "nomor tidak terdaftar". |
| R-S05 | **Injection via Text-to-SQL** (prompt injection → `DROP`, atau akses tabel sensitif). | 4 | 4 | 16 | Lihat R-T06. User DB read-only + `max_execution_time`, blok DDL/DML di parser, whitelist view. |
| R-S06 | **Upload gambar berbahaya** (polyglot, zip bomb, EXIF payload). | 3 | 3 | 9 | Validasi MIME magic bytes, re-encode gambar (sharp/Pillow), batas ukuran, simpan di MinIO bukan filesystem app, scan ClamAV opsional. |
| R-S07 | **Supply chain: GitHub Actions dari pihak ketiga** dibajak. | 3 | 4 | 12 | Pin actions ke SHA, `permissions:` minimal per job, OIDC bukan long-lived token, Dependabot untuk actions. |
| R-S08 | **Image container tanpa provenance**; staging/prod menjalankan image yang bukan hasil CI. | 3 | 4 | 12 | Build hanya di CI, push ke GHCR, SBOM (Syft), sign (Cosign keyless), deploy memverifikasi signature. |
| R-S09 | **Webhook payment/3PL dipalsukan.** | 3 | 5 | 15 | Verifikasi signature HMAC setiap webhook, IP allowlist, idempotency key, replay window. |
| R-S10 | **Insider threat**: admin Odoo bisa ubah harga/stok/poin tanpa jejak. | 3 | 4 | 12 | Audit log (OCA `auditlog`; **diperbarui ADR-031: modul sendiri `efn_audit`**), 4-eyes untuk perubahan pricelist, alert anomali ke manager (sudah disebut BRD, perlu dijadikan requirement konkret). |
| R-S11 | **Tidak ada pentest** sebelum go-live (hanya disebut di Cetak Biru fase 4). | 4 | 4 | 16 | DAST (OWASP ZAP) tiap deploy staging + pentest manual pihak ketiga sebelum tiap go-live mayor. |
| R-S12 | **Kasir/kiosk device tidak dikelola** (Android tanpa MDM, browser bebas). | 3 | 3 | 9 | Kiosk mode, MDM open source (misal Headwind MDM) atau minimal kebijakan device, Wi-Fi terpisah untuk POS. |
| R-S13 | **Komponen inti ditinggalkan upstream** (MinIO community diarsipkan Apr 2026; build terakhir rentan CVE High yang hanya ditambal di edisi proprietary). Pola yang sama bisa terjadi pada komponen lain. | 4 | 4 | 16 | ADR-030: build dari fork komunitas aktif (SILO) di pipeline sendiri + filter L7 + PoC jalur keluar (SeaweedFS). Pantau status upstream semua komponen inti tiap kuartal (repo diarsipkan, rilis terakhir, CVE terbuka) dan catat di ADR terkait. |
| R-S14 | **Pemisahan tugas refund online lintas sistem bergantung pada governance (SEC-111).** Pembatalan SO online berbayar di Odoo (Sales Manager) memicu refund `approval_required`; persetujuan dilakukan grup host `efn-cs-approver`. Identitas Odoo (`odoo-uid`) tidak dipetakan ke akun host, sehingga satu orang yang memegang kedua peran dapat membatalkan dan menyetujui refund-nya sendiri ("barang/uang keluar" tanpa empat mata). *(Baru 10 Okt 2026, review r10 re-review akhir.)* | 2 | 4 | 8 | **Kontrol prosedural** (`docs/runbooks/online-orders.md#sec-111`, `key-custody.md` §2): larangan rangkap Sales Manager ↔ `efn-cs-approver`, register peran di luar repo, review akses bulanan dengan saksi Finance/Internal Audit (SQL Odoo + `getent group`), jejak `efn_audit` (grup Odoo) + journald/Loki wrapper + `online_admin_audit`, alert `EfnOnlineCsDualRole`/`EfnOnlineCsRefundVolumeHigh`, rekonsiliasi refund vs mutasi bank (Finance). **Kontrol teknis (backlog, ±1–2 hari):** tabel peta `odoo_uid ↔ akun host` dikelola Superadmin, BFF menolak approve bila cocok. Syarat aktivasi produksi online no. 5 (r10). Pemilik: Superadmin + Finance. Terkait R-S10, R-O05. |
| R-S15 | **Portal vendor membuka permukaan serangan baru ke internet** (ADR-035, 10 Okt 2026): halaman login, reset password, dan route portal Odoo dapat dijangkau dari internet lewat `portal-gateway`. Skenario: credential stuffing/phishing akun vendor, celah pra-autentikasi Odoo di masa depan, kebocoran dokumen antar-vendor/antar-PT lewat bug record rule portal, sesi lintas host (sesi portal dipakai ke backend), beban worker Odoo mengganggu POS. *(Baru 10 Okt 2026.)* | 3 | 5 | 15 | Allowlist route default-deny (diuji CI), 2FA wajib (`all_required`), signup mati, akun per kontak vendor, WAF (OWASP CRS) + rate limit, mTLS gerbang → Odoo, `efn_portal_guard` (hanya grup vendor-portal di host portal; internal & KDS ditolak), log tanpa query/body + alert, patch Odoo High/Critical ≤ 7 hari, sakelar darurat "tutup portal" (vendor kembali ke email), **review security + pentest pihak ketiga sebelum vendor pertama**. Sementara: vendor via email (V0). Pemilik: devsecops + security-reviewer. Terkait R-S03, R-S11, R-T11. |

### 4.5 Risiko Operasional & Data

| ID | Risiko | P | D | Skor | Mitigasi |
|---|---|---|---|---|---|
| R-O01 | **Master data produk tidak seragam antar-PT** (SKU, barcode, UoM, kategori) membuat inter-company dan DWH kacau. | 5 | 4 | 20 | Program MDM: 1 katalog induk di holding, mapping SKU per PT, governance owner per kategori. Mulai sebelum coding. |
| R-O02 | **Migrasi data dari sistem lama** (stok, piutang, member) tidak disebut sama sekali. | 4 | 4 | 16 | Inventaris sistem saat ini, rencana migrasi, dry-run 2x, cut-over plan. |
| R-O03 | **ETL 15 menit membuat Odoo lambat** jika query full-table. | 3 | 3 | 9 | Incremental (`write_date` watermark), baca dari replica. |
| R-O04 | **Grading A/B/C berdampak harga** tetapi aturan konversi grade → pricelist belum ditulis. | 4 | 3 | 12 | Definisikan matrix grade × komoditas → harga beli dan harga jual, serta siapa yang berwenang mengubah. |
| R-O05 | **Retur, void, refund, komplain** tidak ada di satu pun flow. | 5 | 3 | 15 | Tambahkan flow: void sebelum bayar, refund setelah bayar (POS & online), retur barang, komplain pesanan 3PL, dampak ke poin MGM (clawback). |
| R-O06 | **Cash management kasir** (shift, kas awal, setoran, selisih) tidak disebut. | 4 | 3 | 12 | Odoo POS session punya ini; tetapkan SOP dan laporan selisih per shift. |

---

## 5. GAP KEBUTUHAN & PERTANYAAN PENGGALIAN

Daftar ini dirancang untuk sesi workshop dengan tiap divisi. Tandai jawaban langsung di dokumen ini.

### 5.1 Struktur Korporasi & Keuangan
1. Berapa PT saat ini, berapa brand per PT, dan apakah ada PT holding yang bisa menjadi *merchant of record*?
2. Chart of Accounts: seragam untuk semua PT atau berbeda? Siapa yang mengelola?
3. Metode transfer pricing antar-PT dan siapa yang berwenang mengubahnya?
4. Periode dan mekanisme settlement poin lintas-brand antar PT (bulanan? netting?).
5. Apakah ada sistem akuntansi/ERP saat ini yang datanya harus dimigrasi (Accurate, Jurnal, Excel)?
6. Siapa auditor eksternal, dan format laporan konsolidasi apa yang mereka butuhkan?

### 5.2 Produk, Harga & Master Data
7. Berapa SKU retail dan menu F&B saat ini? Sudah punya barcode GS1 atau internal?
8. Satu katalog induk atau tiap PT punya katalog sendiri? Siapa pemilik data produk?
9. Hierarki harga: harga dasar, harga member, harga promo, harga per-outlet/zona, diskon kuantitas. Mana yang menang jika bertumpuk?
10. Bagaimana harga jual turunan dari Grade A/B/C ditentukan? Otomatis (persentase) atau manual?
11. Price Advantage Index: harga kompetitor mana yang jadi acuan jika ada 5 laporan berbeda untuk produk yang sama (median? terbaru? per wilayah)?

### 5.3 Pelanggan, Loyalty & MGM
12. Satu identitas pelanggan untuk semua brand (nomor HP sebagai primary key)? Bagaimana dengan pelanggan tanpa HP di POS?
13. Nilai poin: 1 poin = Rp berapa? Kedaluwarsa? Minimum redeem? Bisa ditransfer?
14. MGM: basis reward (order value / margin), persentase, cap bulanan, masa berlaku hubungan referral, kategori yang dikecualikan, syarat minimal transaksi downline?
15. Kapan reward "settle"? (Cetak Biru: H+1 tanpa retur. Retur groceries bisa 7 hari.)
16. Apakah reward MGM juga berlaku untuk transaksi offline di POS tanpa app (hanya nomor HP)?
17. Komunikasi ke pelanggan: WA, email, push, SMS? Prioritas dan fallback? Budget per pesan?

### 5.4 POS F&B
18. Jenis layanan per outlet: dine-in, takeaway, drive-thru, delivery. Semua outlet sama?
19. Stasiun dapur: berapa dan apa (hot kitchen, bar, dessert)? Routing item ke stasiun berdasarkan kategori?
20. Table management: layout meja, merge/split bill, pindah meja, open bill (bayar belakangan)?
21. Modifier menu (level pedas, tanpa bawang, extra topping) dan dampaknya ke resep/BoM?
22. Service charge dan PB1: dibebankan ke pelanggan atau sudah include? Berbeda per outlet/daerah?
23. Pembayaran di meja via QRIS: siapa yang menutup bill, pelanggan atau kasir?
24. Printer: struk, kitchen printer backup saat KDS mati, label.

### 5.5 POS Retail
25. Timbangan terintegrasi untuk fresh product (barcode berat)? Protokol?
26. Scan & Go di app: bagaimana anti-theft check di pintu keluar?
27. Shift kasir, kas awal, pecahan, setoran, approval void/refund oleh supervisor?
28. Promo mekanik: buy X get Y, bundling, tebus murah, member price, cashback e-wallet gateway?
29. Pembayaran: tunai, debit/kredit EDC (terintegrasi atau manual input), QRIS statis/dinamis, e-wallet, voucher, poin, split payment?

### 5.6 Supply Chain, Warehouse & Farming
30. Berapa warehouse dan lokasi fisik? Multi-step receipt (receive → QC → stock)?
31. QC Intake: parameter per komoditas (moisture, reject %, grade) — siapa yang menentukan standar? Apakah ada alat ukur yang bisa dibaca otomatis?
32. Pembayaran ke petani: harga floor, termin, metode (transfer/tunai), bukti potong pajak?
33. Apakah petani perlu akses sistem di fase 1, atau cukup staf EFN yang input?
34. Perishable: FEFO, expiry alert, markdown otomatis mendekati expiry, waste logging?
35. Central Kitchen: berapa, kapasitas, jadwal produksi, apakah produksi berdasarkan forecast atau pesanan outlet?
36. Armada same-day internal: ada? Berapa kendaraan? Perlu route planning?

### 5.7 Super-App & Brand Apps
37. Brand apps standalone: wajib di fase 1 atau boleh menyusul? Berapa brand yang butuh app sendiri?
38. Fitur benchmark Cetak Biru §4.2 (food builder, live map, recipe-to-cart, expiry alert): mana yang MVP?
39. Delivery zone & ongkir: radius per outlet, flat/jarak, subsidi ongkir?
40. Slot pengiriman groceries (same-day/next-day): kapasitas per slot per toko?
41. Push notification, deep link, app store accounts (Apple Developer, Google Play) siapa yang memegang?
42. Login: nomor HP + OTP saja, atau juga Google/Apple sign-in?

### 5.8 Price Hunter
43. Reward awal per laporan valid? Cap harian/bulanan per user?
44. Kompetitor mana yang dipantau (whitelist)? Produk mana (top 200 SKU)?
45. Siapa yang menggunakan data kompetitor dan bagaimana (dynamic pricing otomatis atau rekomendasi ke pricing team)?
46. Kebijakan penolakan dan banding (appeal) jika AI menolak foto valid?

### 5.9 Analitik & AI
47. 10 dashboard pertama yang dibutuhkan C-level (nama, metrik, grain, refresh)?
48. Siapa pengguna Text-to-SQL, pertanyaan tipikal apa, dan apa yang terjadi jika jawabannya salah?
49. Retensi data DWH (tahun)? Anonimisasi data pelanggan untuk analitik?

### 5.10 Infrastruktur, Keamanan & Operasi
50. Lokasi hosting: data center Indonesia (kepatuhan PDP & latensi)? Provider yang disetujui?
51. RPO/RTO yang bisa diterima bisnis per sistem (POS, Super-App, ERP, DWH)?
52. Jam operasional outlet (untuk maintenance window)?
53. Siapa yang on-call? Tim IT internal berapa orang dan skill-nya (Python/Odoo, Node, DevOps)?
54. Kebijakan password/SSO untuk staf internal (Odoo, Metabase, Grafana)?
55. Koneksi internet outlet: provider, backup (4G), bandwidth?
56. Perangkat POS: Android tablet / mini PC / Windows? Printer dan scanner existing?

### 5.11 Yang Belum Disebut Sama Sekali di 6 Dokumen
- Retur, refund, void, komplain (R-O05).
- Migrasi data (R-O02).
- Backup/DR (R-T08).
- Observability (R-T09).
- Master data governance (R-O01).
- Perpajakan Indonesia dan Coretax (R-L03).
- UU PDP (R-L01).
- Customer service / helpdesk / ticketing.
- CMS konten (banner, artikel, promo page).
- HR: jadwal shift kasir & kitchen (disebut out-of-scope, tapi POS butuh user/shift).
- Notifikasi internal (stok rendah, pesanan baru, QC gagal).
- Testing strategy (unit, integration, UAT, load test).
- Training & dokumentasi pengguna.
- Lisensi open source dan kewajiban AGPL (R-L08).

---

## 6. ARSITEKTUR TARGET (KONSOLIDASI)

Satu versi yang merekonsiliasi keenam dokumen. Detail visual ada di artifact arsitektur dan `docs/02_TARGET_ARCHITECTURE.md`.

### 6.1 Prinsip
1. **Satu Odoo CE 19, multi-company** sebagai system of record untuk produk, stok, akuntansi, POS.
2. **NestJS BFF** sebagai satu-satunya pintu untuk Super-App/brand apps; Odoo tidak pernah diakses langsung dari internet.
3. **Asynchronous by default** untuk OCR, notifikasi, MGM, webhook 3PL/payment (RabbitMQ).
4. **PostgreSQL adalah source of truth, ClickHouse adalah turunan.** Tidak ada tulisan langsung dari API ke DWH.
5. **AI di luar jalur transaksi.** OCR dan Text-to-SQL adalah worker terpisah; jika mati, transaksi tetap jalan.
6. **Semuanya container, semuanya lewat CI.** Tidak ada deploy manual ke server.
7. **Security shift-left**: scan secret, dependency, image, IaC, dan DAST di pipeline; gate merge.

### 6.2 Komponen & Pilihan Teknologi Final

| Layer | Komponen | Pilihan | Catatan |
|---|---|---|---|
| Edge | CDN/WAF | Cloudflare (free/pro) | Proteksi DDoS, TLS edge, cache aset statis. |
| Edge | Reverse proxy | Traefik v3 | Let's Encrypt otomatis, rate limit middleware, routing per domain/brand. |
| Frontend | Web Super-App & brand sites | Next.js 15 (App Router) | Multi-tenant via domain → brand config. |
| Frontend | Mobile | Expo (React Native) | Satu codebase iOS/Android; EAS Build gratis tier terbatas atau self-build. |
| Frontend | POS, Kiosk, KDS, Customer Display | Odoo POS (CE) + custom `efn_*` (tanpa OCA, ADR-031) | Berjalan di browser Chromium kiosk mode. |
| Frontend | QC Intake mobile | PWA Next.js (offline-capable) | Lebih cepat dibangun daripada app native terpisah. |
| Backend | BFF / API | NestJS (TypeScript) | Auth/OTP, cart, checkout orchestrator, MGM, price hunter, 3PL/payment connector, notifikasi. |
| Backend | ERP | Odoo CE 19 + addons `efn_*` (tanpa OCA/pihak ketiga, ADR-031) | Multi-company, inter-company, MRP, POS, l10n_id. |
| Backend | Workers | NestJS worker + Python worker | Konsumen RabbitMQ: OCR, notif, MGM settle, ETL. |
| Data | OLTP | PostgreSQL 16 (Odoo) + PostgreSQL 16 (platform) | Dua DB logis; satu cluster di awal, pisah saat beban naik. PgBouncer di depan. |
| Data | Cache/OTP/session | Redis 7 | TTL OTP 3 menit, rate-limit counters. |
| Data | Broker | RabbitMQ 3.13 | Quorum queues, DLQ. |
| Data | Object storage | MinIO | Foto price hunter, backup, attachment Odoo (filestore). |
| Data | OLAP | ClickHouse 24 | Data marts. |
| Data | ETL | Python (dlt / SQLAlchemy) terjadwal | Incremental dari replica. Debezium di fase lanjut. |
| BI | Dashboard | Metabase OSS | Embedding ke app internal bila perlu. |
| AI | OCR | PaddleOCR → fallback VLM via Ollama | Worker Python. |
| AI | Text-to-SQL | Ollama (Qwen2.5-Coder 7B/14B) + semantic layer | Read-only, whitelist view. |
| Identity | SSO internal | Keycloak (opsional fase 2) | SSO untuk Odoo, Metabase, Grafana, RabbitMQ UI. |
| Observability | Metrics/logs/alerts | Prometheus, Grafana, Loki, Promtail, Alertmanager, Uptime Kuma | Semua container ship log ke Loki. |
| Security | Secrets | GitHub Environments → Docker secrets / SOPS+age | Tidak ada `.env` di repo. |
| Security | Runtime | CrowdSec (bouncer Traefik), Trivy, ClamAV (opsional) | |
| Platform | Orkestrasi | Docker Compose (dev/staging), Docker Swarm atau k3s (prod) | Mulai Compose; naik ke Swarm saat >1 node app. |
| CI/CD | GitHub Actions + GHCR | Lihat `.github/workflows/` | |

### 6.3 Topologi Node Produksi (fase 1)

| Node | Peran | Komponen | Spesifikasi minimal |
|---|---|---|---|
| `edge-app-01` | Edge & aplikasi | Traefik, Next.js, NestJS API & workers, Redis, RabbitMQ | 8 vCPU, 32 GB, NVMe 200 GB |
| `erp-db-01` | ERP & DB primary | Odoo (web+cron+longpolling), PgBouncer, PostgreSQL primary | 8 vCPU, 32 GB, NVMe 500 GB |
| `data-ai-01` | Analitik & AI | ClickHouse, Metabase, Ollama, OCR worker, ETL | 8 vCPU, 64 GB, GPU 16 GB (opsional fase 1), NVMe 1 TB |
| `db-replica-backup-01` | HA & backup | PostgreSQL replica (hot standby), MinIO, pgBackRest, Prometheus/Grafana/Loki | 4 vCPU, 16 GB, HDD/SSD 2 TB |

Jaringan: semua node dalam satu private network (VLAN/WireGuard). Hanya `edge-app-01` port 80/443 yang publik. SSH hanya via bastion/WireGuard dengan key.

---

## 7. USULAN RE-PHASING ROADMAP

Prinsip: setiap fase menghasilkan sesuatu yang **dipakai di produksi** oleh minimal satu outlet, dan fondasi DevSecOps ada **sebelum** fitur pertama.

| Fase | Durasi | Deliverable | Kriteria selesai |
|---|---|---|---|
| **0. Fondasi & Keputusan** | Bulan 1 | Keputusan D-01..D-16 (ADR), MDM katalog induk, repo + CI/CD + Docker (artefak ini), staging environment, observability, backup, audit modul Odoo CE 19 vs OCA, PoC Kitting & OTP | Semua ADR disetujui; `docker compose up` staging hijau; restore test backup berhasil. |
| **1. ERP Core & POS Retail Pilot** | Bulan 2-4 | Odoo multi-company (2 PT), inter-company OCA, l10n_id & pajak, POS Retail offline-first di 1 toko, cash management, retur/refund, ETL dasar + 3 dashboard Metabase | 1 toko retail live 4 minggu tanpa transaksi hilang; laporan harian otomatis. |
| **2. POS F&B, Kitting, OTP Anti-Fraud** | Bulan 4-6 | MRP Kitting di CK, POS F&B + KDS + QR order di 1 outlet, NestJS auth/OTP, WhatsApp Cloud API, OTP gate di POS (opsi offline diputuskan), loyalty wallet dasar (earn/burn tanpa MGM) | 1 outlet F&B live; OTP sukses rate >95%; stok paket akurat ±2%. |
| **3. Super-App Web & Payment** | Bulan 6-8 | Next.js super-app (katalog gabungan, cart, checkout per-PT dengan satu payment via merchant of record), 1 payment gateway, GoSend/GrabExpress/Lalamove, pickup; mobile Expo beta | 100 order online pertama; rekonsiliasi payment otomatis cocok 100%. |
| **4. MGM 2-Tier & Price Hunter** | Bulan 8-10 | Engine MGM (settle H+N, clawback), referral di app & POS, Price Hunter async (OCR + anti-fraud), ClickHouse marts lengkap | Legal sign-off MGM; OCR auto-valid rate >80% (naik ke 90% iteratif). |
| **5. Farming Hub & AI Analyst** | Bulan 10-12 | QC Intake PWA, master petani & pembayaran, grading → pricelist, Text-to-SQL internal, brand apps standalone pertama | 1 collection center live; manajemen memakai Text-to-SQL mingguan. |
| **6. Hardening & Scale** | Bulan 12+ | Pentest eksternal, HA PostgreSQL (Patroni), Swarm/k3s multi-node, GoFood/GrabFood (bila partner approved), food builder/recipe-to-cart, Keycloak SSO | Uptime 99.9% terukur 3 bulan berturut. |

Durasi total realistis: **12 bulan** untuk cakupan penuh, dengan nilai bisnis pertama di bulan ke-4. Dibanding 8-9 bulan di dokumen asli, selisihnya adalah harga untuk tidak go-live dengan Phantom BoM yang salah dan tanpa backup.

---

## 8. TINDAK LANJUT SEGERA (2 MINGGU KE DEPAN)

1. Tunjuk Product Owner + Arsitek; jadwalkan workshop per divisi memakai Bagian 5.
2. Putuskan D-01 s.d. D-16; tulis sebagai ADR di `docs/decisions/`.
3. Koreksi FSD §1 (Kitting) dan §2 (OCR async) sesuai Bagian 3.
4. Spin up staging dari `infra/compose` di 1 VPS; verifikasi CI/CD berjalan end-to-end.
5. Audit Odoo CE 19: install `pos_restaurant`, `pos_self_order`, `mrp`, cek OCA `multi-company`, `pos`, `account-financial-reporting` untuk versi 19.
6. Konsultasi legal (UU PDP, MGM) dan pajak (PB1/PPN, Coretax, transfer pricing).
7. Buat TCO 3 tahun yang memisahkan license fee (Rp0) dan OpEx.
