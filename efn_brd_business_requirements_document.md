# BUSINESS REQUIREMENTS DOCUMENT (BRD)
## Platform Ecosystem & One-Stop Solution Era Food and Nourishment (EFN)

---

## 1. Lembar Kendali Dokumen (Document Control)

| Informasi | Detail |
| :--- | :--- |
| **Nama Proyek** | EFN One-Stop Solution Enterprise Ecosystem |
| **Nama Perusahaan** | Era Food and Nourishment (EFN) |
| **Versi Dokumen** | 1.0 - Final Draft |
| **Status Dokumen** | Disetujui (Approved) |
| **Target Implementasi**| Core Phase: Odoo CE 19 + Super-App + POS + Agentic AI + ClickHouse DWH |
| **Arsitektur Utama** | 100% Open Source Architecture (Zero License Fee) |

---

## 2. Ringkasan Eksekutif (Executive Summary)

**Era Food and Nourishment (EFN)** adalah holding enterprise yang membawahi berbagai unit bisnis dan brand di sektor **Food & Beverage (F&B)** serta **Supermarket Retail (Groceries & Non-Groceries)**. Setiap brand berada di bawah entitas hukum (PT) tersendiri.

Untuk mendukung pertumbuhan bisnis secara masif, efisien, dan transparan, EFN membutuhkan sebuah **Ekosistem Digital One-Stop Solution** berbasis **Odoo Community Edition (CE) 19** yang dimodifikasi, dikombinasikan dengan **Super-App Multi-Brand (Next.js & React Native)**, **Point of Sale (POS) cerdas**, **Sistem Suplai Hulu-Hilir (Farming Hub)**, serta **Data Warehouse & AI lokal**.

### 3 Pilar Strategis Manajemen:
1. **Pemberdayaan Komunitas & Komparasi Harga (Price Hunter):** Memfasilitasi pelanggan untuk membandingkan harga pasar/kompetitor melalui aplikasi, memberikan crowdsourcing review, serta memberikan rewards berupa voucher jika memberikan data harga yang valid.
2. **Integrasi Hulu-Hilir Pertanian (Farming & Agri-Sourcing):** Mengelola unit usaha *farming* internal dan komunitas petani lokal untuk menyuplai retail groceries dan F&B, memastikan QC ketat di warehouse hulu, serta menjaga stabilitas harga bahan mentah.
3. **Program Rujukan Berjenjang (Member Get Member 2-Tier):** Menerapkan skema *referral* 2 tingkat (Tier 1 & Tier 2) dengan perlindungan sistem anti-fraud berbasis OTP multi-channel (WhatsApp/Email/Apps) di POS maupun Super-App.

---

## 3. Tujuan Bisnis & Indikator Keberhasilan (Strategic Objectives & KPIs)

### 3.1 Tujuan Bisnis
* **Efisiensi Biaya Lisensi:** Memanfaatkan teknologi 100% *Open Source* untuk meniadakan biaya lisensi bulanan per user (Odoo Enterprise, BigQuery, Tableau, dsb.).
* **Konsolidasi Multi-Entitas:** Menghubungkan seluruh transaksi antar-PT (*Inter-company transactions*) secara otomatis melalui modul buatan Agentic AI tanpa human error/double input.
* **Kecepatan & Integrasi Layanan (Omnichannel):** Memberikan pengalaman belanja terpadu di mana pelanggan dapat mengakses produk dari seluruh brand F&B dan Supermarket EFN melalui satu aplikasi utama (*Super-App*).
* **Pencegahan Kecurangan (Anti-Fraud Zero Tolerance):** Mengunci transaksi promosi dan referral di POS menggunakan konfirmasi OTP real-time untuk mematikan celah kecurangan oknum kasir/staf internal.

### 3.2 Key Performance Indicators (KPIs)
* **SLA Pengiriman F&B:** Instant delivery < 45 menit (via 3PL GoFood/GrabFood integration).
* **SLA Pengiriman Groceries:** Same-Day / Next-Day delivery dengan kalkulasi ongkir *Split-Order* otomatis.
* **Akurasi QC Farming:** > 98% ketersediaan stok bahan segar sesuai standar *Grading* A/B/C sebelum didistribusikan ke Warehouse Utama.
* **Tingkat Akselerasi Penjualan via MGM:** Peningkatan akumulasi pengguna baru minimal 25% melalui skema MGM 2-Tier dalam 6 bulan pertama.
* **Tingkat Otomasi Validasi Harga (Price Hunter OCR):** Validasi otomatis oleh AI Vision lokal (Ollama) mencapai > 90% tanpa campur tangan admin manual.

---

## 4. Ruang Lingkup Proyek (Scope of Work)

### 4.1 Dalam Ruang Lingkup (In-Scope)
1. **Super-App & Main Website Multi-Brand (Next.js & React Native):**
   * Katalog produk gabungan (F&B + Groceries) dengan penanganan *Unified Cart / Split Order*.
   * Manajemen akun pelanggan, dompet voucher, jejak order, dan fitur MGM 2-Tier.
   * Fitur Komunitas: Ulasan produk, komparasi harga, dan modul *Market Scout / Price Hunter*.
2. **Channel POS Multi-Brand (Restaurant & Supermarket):**
   * POS Retail Supermarket (Barcode Scanning, Quick Checkout).
   * POS F&B (Order on Table via QR Code, Self-Service Kiosk, Kitchen Display System / KDS, Order Display System / ODS, Waiting List, & Table Booking).
   * Sistem penguncian tombol diskon/MGM yang mewajibkan input OTP dari HP pelanggan.
3. **Core ERP (Odoo CE 19 Custom Add-ons):**
   * Modul Custom Multi-Company Inter-company Transfer (didevelop dengan bantuan Agentic AI).
   * Modul Custom MGM 2-Tier Calculation Engine.
   * Modul Custom Kitting & Phantom BoM (Pengubahan bahan baku mentah menjadi satuan menu siap olah untuk F&B Central Kitchen/Outlet).
   * Modul Supply Chain Pertanian & QC Intake.
4. **Data Warehouse & Business Intelligence (ClickHouse & Metabase):**
   * Data Pipeline ETL otomatis setiap 10 menit dari PostgreSQL Odoo ke ClickHouse.
   * Dashboard Analitik: Customer Profiling, Sales Performance per Brand/Store, Inventory Turnover, dan Comparison Market Price.
5. **AI Vision & LLM lokal (Ollama / Llama 3.2 Vision / Tesseract):**
   * Pengecekan foto price tag kompetitor secara otomatis (OCR).
   * Chatbot Text-to-SQL untuk kemudahan query data manajemen secara internal.

### 4.2 Luar Ruang Lingkup (Out-of-Scope)
* Pembangunan jaringan armada logistik fisik 3PL sendiri (EFN memanfaatkan integrasi API 3PL eksternal seperti GoFood, GrabExpress, Lalamove, dsb.).
* Pembuatan hardware POS/Kiosk secara fisik (EFN hanya menyediakan software application yang berjalan di atas OS Android/Linux/Windows standard).

---

## 5. Kebutuhan Stakeholder Berdasarkan Divisi (Stakeholder Requirements)

### 5.1 Manajemen Eksekutif & C-Level
* **Laporan Konsolidasi Lintas-PT:** Menampilkan laporan keuangan dan arus kas gabungan dari semua entitas brand secara real-time.
* **Dashboard Data Mart:** Visualisasi tren penjualan, tingkat retensi pelanggan, performa pasokan petani, dan perbandingan harga dengan pasar/kompetitor.

### 5.2 Divisi Operasional F&B
* **Standardisasi Satuan Menu (Kitting):** Outlet toko hanya memproses persediaan dalam bentuk porsi "Siap Olah" yang dikirim dari Central Kitchen, bukan gramasi bahan mentah terpisah.
* **Sistem Antrean & Dapur Terintegrasi:** Integrasi instan antara Order-on-Table QR, Kiosk, POS Kasir dengan KDS (Dapur) dan ODS (Pengambilan Pesanan).

### 5.3 Divisi Operasional Supermarket Retail
* **Manajemen Stok Real-time:** Pencatatan lalu lintas persediaan dari Vendor/Internal Farming ke Main Warehouse hingga ke Rak Display Store.
* **Manajemen Multi-Harga:** Mampu mengatur harga promo, harga member, dan diskon kuantitas.

### 5.4 Divisi Supply Chain & Farming Hub
* **QC Intake di Hulu:** Staf lapangan/warehouse farming memiliki aplikasi mobile untuk memasukkan data hasil pemeriksaan kualitas (Persentase Reject, Moisture, Grading A/B/C) sebelum barang masuk ke sistem persediaan.
* **Manajemen Mitra Petani:** Pencatatan identitas kelompok tani, riwayat setoran harvest, dan jadwal pembayaran transparan.

### 5.5 Divisi Pemasaran (Marketing & Community)
* **Skema MGM 2-Tier:**
  * Pelanggan A mengajak Pelanggan B (A mendapat Bonus Tier 1 dari transaksi B).
  * Pelanggan B mengajak Pelanggan C (B mendapat Bonus Tier 1, A mendapat Bonus Tier 2 dari transaksi C).
* **Modul Crowdsourcing Price Hunter:** Mengaktifkan peran komunitas pelanggan untuk memfoto harga di toko kompetitor, divalidasi oleh AI, dan otomatis dikirimkan e-voucher sebagai apresiasi.

### 5.6 Divisi IT & Keamanan Siber
* **Pencegahan Fraud (Anti-Fraud):** POS dan Apps terlindungi dari manipulasi kode referral oleh petugas kasir melalui mekanisme validasi OTP.
* **Privasi Data:** Seluruh data transaksi dan analitik pengguna disimpan di Private Cloud / On-Premise EFN (tidak diekspos ke pihak ketiga).

---

## 6. Arsitektur & Alur Bisnis Utama (Core Business Workflows)

```
[Komunitas / Petani] ---> [QC Mobile Intake] ---> [Farming Warehouse]
                                                      |
                                         (Inter-company Transfer / PO-SO)
                                                      |
                                                      v
                                           [Supermarket Warehouse]
                                                      |
                                          +-----------+-----------+
                                          |                       |
                                          v                       v
                              [Central Kitchen F&B]     [Supermarket Store]
                               (Kitting / Phantom BoM)            |
                                          |                       |
                                          v                       |
                                    [Outlet Resto]                |
                                          |                       |
                                          +-----------+-----------+
                                                      |
                                                      v
                                        [POS / Super-App / Kiosk]
                                                      |
                                             (OTP Verification)
                                                      |
                                                      v
                                            [ClickHouse DWH / BI]
```

---

## 7. Persyaratan Non-Fungsional (Non-Functional Requirements)

1. **Performa Sistem & Latensi:**
   * Waktu respon API POS Kasir < 1 detik untuk pencarian produk dan cetak struk.
   * Waktu pemrosesan OCR oleh AI Vision lokal < 5 detik per lembar foto price tag.
2. **Ketersediaan (Availability):**
   * Sistem POS Kasir harus memiliki mode *Offline-First* (bisa melakukan transaksi saat koneksi internet terputus dan otomatis sinkronisasi saat online kembali).
   * Uptime Super-App dan API Gateway minimum 99.9%.
3. **Keamanan Data (Security & Privacy):**
   * Enkripsi kata sandi menggunakan Argon2/Bcrypt.
   * Kode OTP disimpan dalam Redis dengan durasi kedaluwarsa (TTL) maksimal 3 menit.
   * Pembatasan percobaan OTP maksimal 3 kali untuk mencegah serangan *Brute-Force*.
4. **Kemudahan Perawatan & Skalabilitas (Scalability):**
   * Menggunakan arsitektur Containerization (Docker & Kubernetes/Docker Swarm) untuk mempermudah penambahan kapasitas server secara horizontal.
   * Bebas biaya lisensi berkala (*Zero Software Licensing Overhead*).

---

## 8. Manajemen Risiko & Strategi Mitigasi Bisnis

| Risiko Bisnis | Tingkat Dampak | Strategi Mitigasi |
| :--- | :--- | :--- |
| **Penyalahgunaan Kode Referral oleh Kasir Internal** | Tinggi | Penerapan kunci transaksi POS berbasis OTP ke nomor WhatsApp/Email pelanggan yang terdaftar. Peringatan Anomaly Detection dari AI ke Manager. |
| **Penyusutan (*Shrinkage*) Produk Pertanian Segar** | Tinggi | Wajib QC Intake di Warehouse Hulu sebelum penerimaan stok resmi di Odoo. Penggolongan Grade A/B/C yang secara otomatis membedakan penetapan harga jual. |
| **Kerumitan Logistik Pengiriman Gabungan (F&B + Groceries)** | Sedang | Sistem Super-App secara otomatis menerapkan *Split Order* di keranjang belanja. Pesanan F&B diteruskan ke GoFood/Grab, sedangkan Groceries dikirim via armada Same-Day. |
| **Kegagalan Pembacaan Label Harga oleh AI OCR** | Rendah | Menggunakan ambang batas *Similarity Matching* 75%. Jika di bawah 75%, sistem akan meminta pelanggan mengunggah ulang foto tanpa memberikan hukuman. |

---

*Dokumen BRD ini disusun sebagai acuan formal kesepakatan kebutuhan bisnis antara Manajemen Era Food and Nourishment (EFN) dan Tim Pengembang Sistem.*
