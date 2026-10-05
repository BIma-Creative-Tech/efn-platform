# EXECUTIVE MASTER BLUEPRINT & CONCEPT DOCUMENT
## Era Food and Nourishment (EFN) Enterprise Ecosystem
**Versi:** 2.0 (Consolidated & Updated)  
**Lisensi Stack:** 100% Open Source Architecture (Zero Software License Fee)  

---

## 1. Visi Ringkas & Arsitektur Utama
Era Food and Nourishment (EFN) membangun ekosistem digital *One-Stop Solution* berbasis **Odoo Community Edition (CE) 19** yang dikustomisasi, terintegrasi dengan **Super-App Multi-Brand (Next.js & React Native)**, **Point of Sale (POS) cerdas**, **Data Warehouse (ClickHouse)**, serta **AI Engine Lokal (Ollama Llama 3.2 Vision)**.

### Strategi AI EFN:
* **Agentic AI (Developer Assistant):** Digunakan pada fase *development* oleh tim IT internal untuk memprogram modul kustom Odoo CE 19 (Multi-Company, MGM 2-Tier, Kitting, dan OTP Interceptor) tanpa biaya lisensi Odoo Enterprise.
* **Local LLM & Vision AI (Production):** Dijalankan pada server *on-premise/private cloud* EFN menggunakan Ollama untuk pemrosesan OCR Price Hunter dan analitik data *Text-to-SQL* yang aman tanpa kebocoran data.

---

## 2. Tiga Pilar Strategis Bisnis

1. **Komunitas & Price Hunter (Crowdsourcing Komparasi Harga):**
   * Pelanggan mengambil foto label harga kompetitor via Super-App.
   * AI Vision lokal mengekstrak data harga (OCR) dan melakukan validasi otomatis.
   * Pelanggan yang memberikan data valid menerima *e-voucher* otomatis. Data harga masuk ke Data Warehouse untuk analisis pasar.

2. **Farming Hub & Integrasi Supply Chain Hulu-Hilir:**
   * Pengelolaan pasokan dari unit usaha tani internal dan komunitas petani lokal.
   * Aplikasi Mobile QC Intake di Warehouse Hulu untuk mencatatkan *moisture*, % *reject*, dan *Grading* (A/B/C) sebelum stok diakui di Odoo.
   * Penstabilan harga bahan segar dan pasokan langsung ke Supermarket dan Central Kitchen F&B.

3. **Program Member Get Member (MGM) 2-Tier & Anti-Fraud OTP:**
   * Skema rujukan berjenjang: Tier 1 (Direct Referral) dan Tier 2 (Indirect Referral).
   * Penguncian fitur promo/diskon/MGM di POS Kasir dan Apps yang mewajibkan validasi kode OTP ke nomor WhatsApp/Email terdaftar pelanggan, mematikan celah kecurangan kasir internal.

---

## 3. Matriks Tech Stack 100% Open Source

| Komponen | Teknologi | Peran & Keunggulan |
| :--- | :--- | :--- |
| **Frontend Super-App** | Next.js & React Native | Unified Cart, Multi-Brand Storefront, & Split-Order Engine |
| **API Gateway** | Node.js (Express/NestJS) | Orchestrator lalu lintas data, integrasi 3PL (GoFood/Grab) |
| **Core ERP & POS** | Odoo CE 19 Custom | Backend operasi, inventoris, akuntansi, dan POS offline-first |
| **Database Utama** | PostgreSQL & Redis | RDBMS transactional & In-Memory Caching OTP Session |
| **Data Warehouse** | ClickHouse | Analytics DB berkecepatan tinggi untuk data agregat |
| **Business Intelligence**| Metabase / Apache Superset | Visualisasi dashboard manajemen & C-Level |
| **Local AI Engine** | Ollama (Llama 3.2 Vision) | OCR Price Tag & Text-to-SQL analytics tanpa biaya API |

---

## 4. Rangkuman Dokumen Proyek Terintegrasi
Dokumen ini mengonsolidasikan seluruh rangkaian dokumentasi teknis dan bisnis EFN:
* **BRD (Business Requirements Document):** Ruang lingkup bisnis, KPI, kebutuhan pemangku kepentingan, dan mitigasi risiko.
* **FSD (Functional Specification Document):** Detail alur kerja Kitting & Phantom BoM serta API OCR Price Hunter.
* **TSD (Technical Specification Document):** Topologi jaringan, arsitektur microservices, dan strategi keamanan OTP/data.

---
*EFN Master Blueprint - Consolidated Version 2.0*
