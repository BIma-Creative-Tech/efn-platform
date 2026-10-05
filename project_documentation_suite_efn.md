# PROJECT DOCUMENTATION SUITE: ERA FOOD AND NOURISHMENT (EFN)

**Dokumen Versi:** 1.0 (Final Draft)
**Tanggal Penyusunan:** Oktober 2026
**Status:** Menunggu Persetujuan Manajemen

---

## BAGIAN 1: PROJECT DOCUMENT (PROJECT CHARTER)

### 1.1 Informasi Proyek
* **Nama Proyek:** EFN Omnichannel & ERP Platform (Open Source Initiative)
* **Sponsor Proyek:** Board of Directors EFN
* **Tujuan Proyek:** Membangun ekosistem digital terpusat (Super-App, POS, dan ERP) untuk seluruh brand F&B dan Retail Supermarket EFN menggunakan 100% teknologi *Open Source* guna mencapai efisiensi lisensi Rp 0, mengintegrasikan rantai pasok (*farm-to-shelf*), dan meningkatkan retensi pelanggan melalui skema *Member-Get-Member* (MGM).

### 1.2 Ruang Lingkup (Scope)
* **In-Scope:** Pembuatan Super-App (Web & Mobile), POS F&B & Retail, Custom Odoo CE 19 (Multi-Company & MGM), Modul Farming, Integrasi 3PL/Payment, Data Warehouse, dan implementasi Local AI (LLM & Vision).
* **Out-of-Scope:** Pengadaan perangkat keras (hardware) kasir, operasional harian logistik 3PL, dan pembuatan sistem *Payroll/HRIS* di luar integrasi dasar.

### 1.3 Timeline & Fase Proyek
* **Fase 1 (Bulan 1-2): Setup & ERP Foundation.** Instalasi Odoo CE 19, pembuatan *custom module* (Multi-company) dibantu Agentic AI, dan setup PostgreSQL.
* **Fase 2 (Bulan 3-4): POS & Anti-Fraud.** Modernisasi POS, pembuatan middleware Node.js, dan implementasi wajib OTP (WhatsApp/Email) untuk transaksi.
* **Fase 3 (Bulan 5-6): Super-App & 3PL.** Rilis Next.js Web & Mobile Apps, integrasi keranjang belanja lintas brand, dan routing pengiriman 3PL.
* **Fase 4 (Bulan 7-8): Farming, Price Hunter, & Analytics.** Rilis aplikasi petani (QC), fitur *Market Scout* (AI OCR OCR lokal), dan peluncuran ClickHouse + Metabase.

---

## BAGIAN 2: BUSINESS REQUIREMENTS DOCUMENT (BRD)

### 2.1 Latar Belakang & Masalah Bisnis
EFN memiliki beberapa brand FnB dan Retail Supermarket sebagai entitas (PT) terpisah. Manajemen menghadapi kendala berupa biaya lisensi sistem yang mahal, kesulitan memantau konsolidasi stok antar perusahaan, celah *fraud* pada program loyalitas di kasir, dan ketergantungan pada *supplier* tengkulak yang membuat harga tidak stabil.

### 2.2 Kebutuhan Bisnis Utama (Business Requirements)
* **BR-01: Zero License Fee.** Perusahaan mewajibkan penggunaan teknologi *open-source* tanpa biaya lisensi per-user/per-tahun (menolak SAP, Oracle, atau Odoo Enterprise).
* **BR-02: Konsolidasi Multi-Entitas.** Sistem harus mampu menangani transaksi antar-perusahaan secara otomatis (misal: Main Warehouse Retail menyuplai Central Kitchen FnB) tanpa *double input* oleh admin.
* **BR-03: Skema Loyalty MGM 2-Tier.** Sistem harus memiliki program *referral* 2 tingkat yang transparan. Poin dari belanja ritel harus bisa dipakai di FnB, dan sebaliknya.
* **BR-04: Transparansi Harga via Komunitas.** Perusahaan membutuhkan data *crowdsourcing* harga kompetitor dari pelanggan untuk menyesuaikan strategi harga EFN secara dinamis.
* **BR-05: Farm-to-Fork Traceability.** Perusahaan membutuhkan kepastian pasokan dari mitra petani dengan sistem QC digital untuk menekan *shrinkage* (penyusutan barang).

---

## BAGIAN 3: FUNCTIONAL SPECIFICATION DOCUMENT (FSD)

Dokumen ini menerjemahkan BRD menjadi fitur-fitur fungsional pada aplikasi.

### 3.1 Super-App & Main Website (Next.js)
* **FSD-1.1 Unified Cart & Split Routing:** Pelanggan dapat memasukkan Kopi (FnB) dan Sayur (Retail) ke satu keranjang. Saat *checkout*, sistem memecah pesanan secara transparan: FnB dikirim via GoFood/Grab instan, Sayur dikirim via kurir *same-day* internal/Lalamove.
* **FSD-1.2 Market Scout (Price Hunter):** Fitur kamera in-app. Pengguna memotret label harga di supermarket kompetitor. Jika valid, pengguna menerima poin EFN langsung ke dompet digital mereka.

### 3.2 Point of Sale (POS) & Anti-Fraud
* **FSD-2.1 OTP-Gated Promotion:** Saat kasir memilih tombol "Gunakan Poin MGM" di POS, sistem akan mengunci transaksi. Pelanggan menerima OTP via WhatsApp. Kasir (atau pelanggan via *Customer Display*) wajib memasukkan OTP tersebut untuk menyelesaikan transaksi.
* **FSD-2.2 FnB vs Retail Mode:** Satu aplikasi POS yang dapat di- *switch* modenya. Mode Retail mendukung *scanner barcode* kecepatan tinggi. Mode FnB mendukung *Kitchen Display System (KDS)* dan *Table Booking*.

### 3.3 Core ERP (Odoo CE 19 Customization)
* **FSD-3.1 Kitting/Phantom BoM:** Modul pengiriman dari Warehouse ke Outlet FnB. Warehouse memproses bahan mentah menjadi "Paket Menu" (Kitting). Outlet hanya bisa memesan SKU "Paket Menu" untuk memudahkan *stock opname* dan mencegah *fraud* bahan baku.
* **FSD-3.2 Inter-Company Automation:** Jika Entitas A (FnB) membuat PO ke Entitas B (Retail), modul akan otomatis membuat SO di Entitas B dengan harga *Transfer Pricing* yang sudah diatur oleh manajemen.

### 3.4 Modul Farming & QC
* **FSD-4.1 Mobile QC Input:** Staf di fasilitas penerimaan/petani menggunakan tablet untuk memasukkan persentase hasil panen (Grade A, B, Reject). Data ini otomatis men-*generate* *Goods Receipt Note (GRN)* di Odoo CE.

---

## BAGIAN 4: TECHNICAL SPECIFICATION DOCUMENT (TSD)

### 4.1 Arsitektur Sistem Dasar
Sistem EFN beroperasi menggunakan arsitektur *microservices-oriented* yang bertumpu pada API.
* **Frontend:** Next.js (SSR) & React Native.
* **API Gateway / Middleware:** Node.js (Express) untuk *routing* dan validasi *session*.
* **Backend Utama (ERP/POS):** Odoo Community Edition 19 (Python).
* **Database Transaksional:** PostgreSQL 16+.
* **In-Memory Store:** Redis (untuk caching OTP, Session, dan Shopping Cart).

### 4.2 Integrasi AI (Local / On-Premise)
* **Agentic AI (Development Phase):** Tim *engineer* internal akan menggunakan AI (seperti Qwen Coder / Cursor lokal) untuk men- *generate* kode *custom add-ons* Odoo (Modul Multi-Company, Modul MGM). AI ini tidak berjalan di sistem *live*.
* **Local LLM & Vision AI (Production Phase):** 
  * *Teknologi:* **Ollama** di- *host* di server internal, menjalankan **Llama 3.2 Vision** (atau LLaVA/Tesseract).
  * *Fungsi:* Menerima *request* HTTP berisi Base64 gambar *Market Scout*, mengekstrak teks nama barang dan harga (OCR), dan merespon ke Node.js dalam format JSON.
  * *Keamanan:* 100% *Offline/Air-gapped* dari internet publik. Data tidak keluar dari server EFN.

### 4.3 Data Warehouse & Analytics
* **ETL Pipeline:** Skrip Python (atau Debezium CDC) berjalan setiap 15 menit menarik data dari PostgreSQL (Odoo & Next.js).
* **Data Warehouse:** **ClickHouse** (Open Source, Columnar Database untuk analitik *query* super cepat miliaran baris).
* **BI Dashboard:** **Metabase** (dihubungkan langsung ke ClickHouse) untuk memvisualisasikan *Gross Margin*, *Customer RFM*, dan *Sales Velocity* lintas-entitas PT.

### 4.4 Topologi Infrastruktur (Bare Metal / VPS Deployment)
Untuk menghindari biaya Cloud (AWS/GCP) yang membengkak, EFN menggunakan *Private Server* (VPS performa tinggi / Bare Metal) dengan spesifikasi:

| Node Server | Peran (Roles) | Software Stack |
| :--- | :--- | :--- |
| **Node 1 (ERP & DB)** | Core Transaksional & Finance | Odoo CE 19, PostgreSQL (Master), PgBouncer |
| **Node 2 (Web & API)** | Frontend Serving & Gateway | Node.js, Next.js PM2, Redis, NGINX Reverse Proxy |
| **Node 3 (Analytics & AI)**| OLAP & Local LLM Inference | ClickHouse, Metabase, Ollama, Llama 3.2 Vision |
| **Node 4 (Backup/HA)** | Failover & Data Backup | PostgreSQL (Replica), S3-Compatible Local Storage (MinIO) |

### 4.5 Security & API Specifications
* **Authentication:** JWT (JSON Web Tokens) dengan masa berlaku 1 jam, di- *refresh* menggunakan *refresh token*.
* **API Endpoints (Sample):**
  * `POST /api/v1/auth/request-otp` (Trigger Redis & WhatsApp API)
  * `POST /api/v1/pos/apply-mgm-discount` (Membutuhkan validasi OTP)
  * `POST /api/v1/market-scout/analyze` (Upload gambar ke Ollama Vision API)
  * `POST /api/v1/checkout/split-cart` (Menangani *routing* FnB vs Retail ke 3PL)