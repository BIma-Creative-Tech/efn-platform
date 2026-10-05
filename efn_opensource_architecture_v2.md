# Dokumen Arsitektur & Infrastruktur EFN (Versi 2: 100% Open Source)

## 1. Konsep Penggunaan AI di EFN
Sesuai arahan terbaru, AI tidak berjalan sebagai *middleware* transaksi yang memberatkan server, melainkan dibagi menjadi dua fungsi utama:

### A. Agentic AI sebagai "Developer Assistant"
Digunakan di fase *Development* (oleh tim IT internal) untuk memprogram modul custom Odoo CE 19 agar setara dengan Odoo Enterprise.
*   **Target Modul 1:** Custom Multi-Company Add-on (mengakomodir tiap brand sebagai entitas/PT terpisah dengan *Inter-company transfer*).
*   **Target Modul 2:** Modul MGM 2-Tier (menghitung komisi/poin berantai tanpa membebani sistem POS).
*   **Target Modul 3:** Modul OTP Interceptor (menghubungkan tombol validasi promo di POS dengan sistem WhatsApp/Email secara *open source*).

### B. LLM & Vision AI untuk Operasional & Analitik
Digunakan di fase *Production* (berjalan di server lokal/private cloud EFN agar data rahasia aman).
*   **LLM OCR (Price Hunter):** Saat pelanggan mengunggah foto label harga kompetitor, sistem menggunakan AI Vision Open Source (misal: LLaVA atau Tesseract OCR) untuk membaca nama produk dan angka, lalu menvalidasinya secara otomatis.
*   **LLM Data Analyst:** Menggunakan LLM Open Source (misal: Llama 3 atau Qwen) yang dihubungkan ke Data Warehouse. Manajemen bisa mengetik pertanyaan "Tampilkan perbandingan penjualan ayam geprek vs ayam goreng di semua outlet bulan ini", dan AI akan mengubahnya menjadi *Query SQL* dan memunculkan grafiknya (*Text-to-SQL*).

---

## 2. Tech Stack 100% Open Source (Zero License Fee)

Untuk menekan biaya operasional seminimal mungkin, berikut adalah *stack* yang digunakan:

| Layer | Teknologi Open Source Pilihan | Alasan Pemilihan |
| :--- | :--- | :--- |
| **Frontend Super-App** | **Next.js** (Web) & **React Native / Expo** (Mobile Apps) | Performa tinggi, ekosistem sangat luas, dan gratis. Mudah untuk dibuat *multi-tenant* (satu *codebase* banyak brand). |
| **Backend / API Gateway** | **Node.js** (Express / NestJS) atau **Golang** | Cepat untuk mengatur *routing* API dari Frontend (Apps/POS) ke backend Odoo. |
| **Core ERP & POS** | **Odoo CE 19** (Python / PostgreSQL) | Gratis, tanpa lisensi per *user*. Semua kekurangan ditutupi dengan modul buatan Agentic AI. |
| **Database Utama** | **PostgreSQL** | Standar industri untuk Odoo dan Node.js, sangat stabil dan gratis. |
| **Caching & OTP Session** | **Redis** | Menangani keranjang belanja Super-App agar sangat cepat, serta menyimpan kode OTP sementara. |
| **Data Warehouse** | **ClickHouse** (Open Source) | Jauh lebih cepat dari PostgreSQL untuk analitik data besar, menggantikan Google BigQuery yang berbayar. |
| **Business Intelligence (BI)** | **Metabase** atau **Apache Superset** | Menggantikan Tableau/PowerBI. Langsung terhubung dengan ClickHouse untuk *dashboard* manajemen. |
| **AI LLM / OCR Engine** | **Ollama** (menjalankan **Llama 3.2 Vision** lokal) / **Tesseract OCR** | AI berjalan di *server* internal EFN (On-Premise/VPS Private), data perusahaan dan pelanggan tidak bocor ke OpenAI/Google. |

---

## 3. Topologi Alur Data (Sistem Anti-Fraud & Integrasi)

### A. Alur Pemesanan & OTP Anti-Fraud (POS & Super-App)
1.  **Pelanggan / Kasir** memasukkan pesanan dan mengklaim diskon MGM.
2.  **Sistem Backend (Node.js)** menahan transaksi (*Pending*), dan *generate* OTP.
3.  OTP dikirim via WhatsApp API (menggunakan *provider* yang murah atau integrasi BAILEYS Open Source) ke nomor terdaftar.
4.  Transaksi di POS Odoo hanya akan *Unlock* jika OTP yang dimasukkan *match* dengan data di **Redis**.

### B. Alur "Price Hunter" (Komunitas & Petani)
1.  Pelanggan/Petani foto harga pasar/kompetitor di Super-App (Next.js/React Native).
2.  Gambar dikirim ke server AI internal EFN (menjalankan Ollama/LLaVA Vision).
3.  AI mengekstrak (OCR) teks: "Bawang Merah - Rp 45.000/kg".
4.  Data masuk ke **PostgreSQL**. Jika tervalidasi, *trigger* modul Odoo untuk mengirimkan *E-Voucher* ke dompet pelanggan/petani di aplikasi.

### C. Alur Data Warehouse & Analitik Manajemen
1.  Semua transaksi dari berbagai database Odoo (tiap PT) diekstrak setiap 10 menit menggunakan *ETL Script* (Python).
2.  Data dimasukkan ke **ClickHouse** (Data Warehouse).
3.  Manajemen membuka **Metabase** (Dashboard) untuk melihat *Customer Profiling* atau performa *Farming vs Retail*.
4.  Manajemen juga bisa *chatting* dengan **AI LLM lokal** untuk menganalisa data tanpa perlu bisa *coding*.

---

## 4. Kebutuhan Infrastruktur Fisik / Cloud (Saran Hosting)

Karena semuanya Open Source, Anda hanya perlu menyewa *Virtual Private Server* (VPS) atau *Bare Metal Server* (seperti Contabo, Hetzner, atau Biznet Gio lokal untuk latensi rendah), tanpa perlu AWS/GCP yang mahal.

*   **Server 1 (ERP & Database):** Menjalankan Odoo CE 19 & PostgreSQL.
*   **Server 2 (Web & API):** Menjalankan Next.js, Node.js API Gateway, dan Redis.
*   **Server 3 (Analytics & AI):** Menjalankan ClickHouse, Metabase, dan Ollama (direkomendasikan server *Bare Metal* dengan GPU entry-level/mid-range jika ingin respon AI Vision cepat, atau CPU *high-core* jika AI dijalankan *asynchronous*).
