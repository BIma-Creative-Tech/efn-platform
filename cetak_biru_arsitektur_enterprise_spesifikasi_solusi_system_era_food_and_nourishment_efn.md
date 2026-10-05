# CETAK BIRU ARSITEKTUR ENTERPRISE & SPESIFIKASI SOLUSI SYSTEM
## ERA FOOD AND NOURISHMENT (EFN)
**Versi:** 1.0 | **Status:** Dokumen Arsitektur Final | **Stack Utama:** Odoo CE 19, Next.js, PostgreSQL, Microservices API

---

## 1. RINGKASAN EKSEKUTIF & VISI PLATFORM

**Era Food and Nourishment (EFN)** memposisikan diri sebagai pemain utama dalam ekosistem *Food & Beverage (FnB)* serta *Supermarket Retail (Groceries & Non-Groceries)*. Untuk mendukung skala pertumbuhan bisnis multi-brand dan multi-unit, EFN membutuhkan arsitektur sistem informasi yang modern, modular, fleksibel, serta terintegrasi secara *end-to-end*.

Solusi ini dirancang sebagai **One-Stop Solution Ecosystem** berbasis **Odoo Community Edition (CE) 19** pada layer back-office / ERP, dikombinasikan dengan front-end performa tinggi menggunakan **Next.js** dan **PostgreSQL** untuk Super-App, Main Web, dan Brand Frontends. 

### Visi Utama Platform:
1. **Omnichannel Unified Experience:** Satu pintu masuk (*Super-App/Main Web*) bagi konsumen untuk mengakses seluruh brand resto dan retail supermarket under EFN, sekaligus mempertahankan identitas khusus (*standalone branding*) untuk setiap brand.
2. **End-to-End Agri-Supply Chain Integration:** Mengintegrasikan rantai pasok dari petani (upstream) hingga ke rak retail / dapur resto (downstream) untuk stabilitas harga dan kualitas.
3. **Community-Driven Growth & Transparency:** Memberdayakan komunitas pengguna melalui transparansi harga pasar dan program *Member-Get-Member (MGM)* berjenjang (2-Tier).

---

## 2. DIAGRAM ARSITEKTUR SOLUSI & ALUR INTEGRASI

Berikut adalah arsitektur enterprise EFN yang memperlihatkan hubungan antara Front-End Services, API Gateway, Aggregator Layer, Core ERP (Odoo CE 19), Data Warehouse, serta Integrasi Pihak Ketiga (3PL / Payment Gateway).

```
+---------------------------------------------------------------------------------------------------+
|                                     CLIENT LAYER / FRONT-END                                       |
|  +---------------------------+  +---------------------------+  +-------------------------------+  |
|  |   EFN Main Super-App      |  |     EFN Main Website      |  | Brand Web & Apps (Standalone) |  |
|  |  (Next.js PWA / React N)  |  |      (Next.js SSR)         |  |   (Brand A, Brand B, Resto)   |  |
|  +-------------+-------------+  +-------------+-------------+  +---------------+---------------+  |
+----------------|------------------------------|--------------------------------|------------------+
                 |                              |                                |
                 +------------------------------+--------------------------------+
                                                |
                                                v
+---------------------------------------------------------------------------------------------------+
|                                  API GATEWAY & SERVICE MESH (Kong)                                 |
|   - Authentication (JWT / OAuth2)    - Rate Limiting           - Multi-tenant Brand Router        |
|   - Real-time Price Comparison Sync   - Delivery Dispatcher     - MGM Commission Calculation       |
+-----------------------------------------------+---------------------------------------------------+
                                                |
        +---------------------------------------+---------------------------------------+
        |                                       |                                       |
        v                                       v                                       v
+-----------------------+               +-----------------------+               +-----------------------+
|  POS & STORE PLATFORM |               |   COMMUNITY & AGRI    |               |  MGM & LOYALTY ENGINE |
| (FnB & Retail Kiosk)  |               |      FARMER HUB       |               |     (Microservice)    |
| - QR Table Order      |               | - Farmer Portal       |               | - 2-Tier Referral     |
| - KDS (Kitchen Display|               | - Yield Forecast      |               | - E-Wallet & Cashbacks|
| - ODS (Order Display) |               | - Price Intelligence  |               | - Cross-brand Earning |
| - Booking & Waitlist  |               | - Community Reviews   |               |   & Redemption        |
+-----------+-----------+               +-----------+-----------+               +-----------+-----------+
            |                                       |                                       |
            +---------------------------------------+---------------------------------------+
                                                |
                                                v
+---------------------------------------------------------------------------------------------------+
|                                   CORE ERP (ODOO CE 19 HUB)                                       |
|  +--------------------+  +--------------------+  +--------------------+  +--------------------+  |
|  |  Inventory & WMS   |  | Procurement & PO   |  | Multi-Company Fin. |  | Custom Agri-Supply |  |
|  +--------------------+  +--------------------+  +--------------------+  +--------------------+  |
|  +--------------------+  +--------------------+  +--------------------+  +--------------------+  |
|  | Central Price List |  | Centralized CRM    |  | Inter-Company Auto |  | Production / MRP   |  |
|  +--------------------+  +--------------------+  +--------------------+  +--------------------+  |
|                                                                                                   |
|  [ Database: PostgreSQL HA Cluster (Master-Replica) ]                                             |
+-----------------------------------------------+---------------------------------------------------+
                                                |
                     +--------------------------+--------------------------+
                     | (CDC / Debezium / ETL)                              | (REST / Webhooks)
                     v                                                     v
+-----------------------------------------------+  +------------------------------------------------+
|          DATA WAREHOUSE & DATA MARTS          |  |          THIRD-PARTY INTEGRATION LAYER         |
|  +-----------------------------------------+  |  |  +------------------------------------------+  |
|  | ClickHouse / PostgreSQL Analytics DB    |  |  |  | 3PL Delivery: GoFood, GrabExpress, Shopee|  |
|  +-----------------------------------------+  |  |  +------------------------------------------+  |
|  | Data Marts: Sales, Inventory, RFM, Agri |  |  |  | Payment: Midtrans, Xendit, QRIS National |  |
|  +-----------------------------------------+  |  |  +------------------------------------------+  |
|  | BI Tool: Metabase / Apache Superset     |  |  |  | Competitor Price Scraping Engine         |  |
|  +-----------------------------------------+  |  |  +------------------------------------------+  |
+-----------------------------------------------+  +------------------------------------------------+
```

---

## 3. POIN KEUNGGULAN STRATEGIS (USP) KHUSUS MANAJEMEN

Untuk memenangkan persaingan pasar dan memberikan nilai investasi (*ROI*) tinggi di mata Manajemen EFN, platform ini mengusung 4 Poin Keunggulan Utama yang tidak dimiliki oleh kompetitor retail/FnB konvensional:

| No | Keunggulan Strategis (USP) | Penjelasan & Dampak Bisnis |
| :--- | :--- | :--- |
| **1** | **Farm-to-Fork Price & Quality Index** | Integrasi langsung antara komunitas petani dan sistem harga retail EFN. EFN mampu memotong *middleman* hingga 30%, memberikan jaminan harga termurah bagi konsumen, serta marjin lebih stabil bagi EFN. |
| **2** | **Unified Cross-Brand MGM Engine (2-Tier)** | Konsumen dapat mengajak teman untuk belanja *groceries* di retail EFN, dan mendapatkan bonus poin yang bisa di-spend untuk makan di resto/FnB milik EFN. Ini menciptakan *ecosystem lock-in* yang kuat. |
| **3** | **Crowdsourced Price Comparison Intelligence** | Fitur transparansi harga berbasis komunitas yang membandingkan harga produk EFN dengan kompetitor (misal: Indomaret, Alfamart, Hypermart) secara real-time. Membangun kepercayaan (*trust*) publik setingkat lebih tinggi. |
| **4** | **Hybrid Scalable Stack (Next.js + Odoo CE 19)** | Penghematan biaya lisensi ERP hingga miliaran rupiah per tahun dengan memanfaatkan Odoo CE 19 yang dikustomisasi, sembari mempertahankan performa front-end secepat kilat (*sub-second loading*) menggunakan Next.js. |

---

## 4. DETAIL FITUR PLATFORM FRONT-END & CHANNELS

### 4.1 Main Website & Main Apps (Super-App EFN)
Super-App & Web Portal EFN menjadi hub tunggal yang menggabungkan seluruh brand FnB dan Retail Supermarket under EFN.

* **Unified Multi-Brand Catalog:** User dapat berpindah antara belanja supermarket (*Groceries & Non-Groceries*) dan pemesanan makanan (*FnB Makanan/Minuman*) dalam satu *checkout Session*.
* **Smart Location & Nearest Hub Routing:** Otomatis mendeteksi lokasi user dan mengarahkan pesanan ke store retail atau outlet resto EFN terdekat.
* **Unified Loyalty Wallet:** Poin belanja retail bisa dipakai untuk klaim voucher FnB atau Sebaliknya.
* **Integrated 3PL & Self-Delivery Dispatcher:** Pilihan pengiriman instan via GoSend/GrabExpress/Lalamove, pesanan resto via GoFood/GrabFood API, atau *Self Pick-Up* / *Drive-Thru*.

### 4.2 Standalone Brand Website & Apps
Setiap brand di bawah EFN tetap memiliki panggung digital independen untuk menjaga *brand equity* dan keunikan *user experience*.

#### Benchmarking Fitur Standalone (Vs Raksasa Dunia):

```
+------------------+----------------------------------+------------------------------------+
| Kategori         | Benchmark Global                 | Fitur EFN Brand Platform           |
+------------------+----------------------------------+------------------------------------+
| FnB Storefront   | Domino's Pizza / Starbucks       | - Real-time Pizza/Food Builder     |
|                  |                                  | - Order Tracker Visual (Live Map)  |
|                  |                                  | - Pre-Order & Scheduled Catering   |
|                  |                                  | - Seamless One-Tap Reordering      |
+------------------+----------------------------------+------------------------------------+
| Retail Groceries | Whole Foods / Target / Amazon    | - Smart Shopping List Builder      |
|                  | Fresh                            | - Barcode Scanner in-App (Scan&Go) |
|                  |                                  | - Expiry Date Discount Alerts      |
|                  |                                  | - Recipe-to-Cart Automatic Adder   |
+------------------+----------------------------------+------------------------------------+
```

### 4.3 Point of Sale (POS) Ecosystem

Solusi POS dirancang untuk dua jenis operasional: **POS Retail Barcode** dan **POS FnB Resto**.

#### Integrated POS Capabilities:
1. **QR Order on Table (FnB):** Pelanggan mendiag scan QR di meja, memilih menu, langsung membayar via QRIS/E-Wallet, dan pesanan otomatis masuk ke kitchen.
2. **Self-Order Kiosk:** Standalone Kiosk layar sentuh di area resto atau supermarket untuk memangkas antrean kasir.
3. **Kitchen Display System (KDS):** Layar dapur interaktif pengganti kertas resi. Mengelompokkan pesanan berdasarkan stasiun (Dapur Panas, Minuman, Dessert) dan mengukur *Preparation Time*.
4. **Order Display System (ODS):** Layar pemanggilan pesanan siap ambil untuk pelanggan di area *takeaway*.
5. **Waiting List & Table Booking:** Management reservasi meja terintegrasi dengan SMS/WhatsApp Notifier untuk memberi tahu ketersediaan meja pelanggan.
6. **Retail POS Barcode High-Speed:** Kasir retail dengan dukungan offline-first mode, barcode scanner omni-directional, dan pemrosesan transaksi $< 3$ detik.

---

## 5. MODUL ERP & CUSTOM ENGINE (ODOO CE 19 BASE)

Core Back-Office menggunakan Odoo Community Edition (v19) yang diperkaya dengan *custom module development* Python/XML:

```
                  +--------------------------------------------------+
                  |               ODOO CE 19 CORE MODULES            |
                  |  - Sales            - Purchase                   |
                  |  - Inventory (WMS)  - Accounting & Invoicing     |
                  |  - Point of Sale    - Manufacturing (MRP)        |
                  +------------------------+-------------------------+
                                           |
                                           v
                  +--------------------------------------------------+
                  |               EFN CUSTOM MODULES                 |
                  +--------------------------------------------------+
                  | 1. custom_efn_multibrand_consolidation           |
                  | 2. custom_efn_agri_farmer_management             |
                  | 3. custom_efn_price_intelligence_community       |
                  | 4. custom_efn_mgm_tiering_engine                 |
                  | 5. custom_efn_3pl_omnichannel_connector          |
                  +--------------------------------------------------+
```

### Rincian Custom Modules:
1. **`custom_efn_multibrand_consolidation`:**
   * Mengelola *Inter-company Billing*, *Cross-Brand Stock Transfer*, dan Laporan Keuangan Konsolidasi otomatis antar unit bisnis FnB dan Retail.
2. **`custom_efn_agri_farmer_management`:**
   * Manajemen kontrak petani, pencatatan prediksi panen, standardisasi kualitas (*Quality Control Grading*), dan penetapan harga beli dasar (*Base Purchasing Price*).
3. **`custom_efn_price_intelligence_community`:**
   * Sinkronisasi data harga barang di pasar / kompetitor, verifikasi *user crowdsourced review*, dan penyesuaian harga *dynamic pricing* di retail EFN.
4. **`custom_efn_mgm_tiering_engine`:**
   * Perhitungan otomatis komisi/reward rujukan 2-Tier berdasarkan akumulasi transaksi terverifikasi di Odoo Sales & POS.
5. **`custom_efn_3pl_omnichannel_connector`:**
   * Middleware penghubung antara Odoo POS/Sales dengan API GoFood, GrabFood, ShopeeFood, Lalamove, dan GoSend.

---

## 6. INISIATIF STRATEGIS MANAJEMEN (DEEP-DIVE)

### 6.1 Modul Komunitas & Komparasi Harga Pasar

Modul ini memadukan partisipasi pelanggan (*crowdsourcing*) dan sistem scraping harga otomatis.

* **Crowdsourced Price Review:** Pelanggan dapat mengunggah foto struk belanja kompetitor atau memberikan ulasan harga pasar lokal.
* **Point Incentive for Reviewers:** User yang memberikan review valid mengenai perbandingan harga mendapatkan reward *EFN Coin/Points*.
* **Dynamic Price Comparison Matrix:**
  $$\text{Price Advantage Index (PAI)} = \left( \frac{\text{Competitor Market Price} - \text{EFN Price}}{\text{Competitor Market Price}} \right) \times 100\%$$
  Jika $\text{PAI} > 0$, sistem otomatis menayangkan badge *"Termurah x% dari Pasar"* pada aplikasi.

### 6.2 Modul Agroteknologi & Manajemen Petani (Farmer Hub)

Membangun ketahanan pasokan (*Supply Chain Resilience*) langsung dari rantai paling hulu.

```
[Komunitas Petani / Outgrowers] ---> [EFN Farmer Portal App] ---> [QC & Collection Center]
                                                                        |
                                                                        v
[Supermarket Retail Shelves] <--- [EFN Central Distribution Hub] <--- [Odoo WMS Receive]
             |
             v
[Resto FnB Kitchens]
```

* **Farmer Portal App:** Aplikasi sederhana bagi petani mitra untuk menginput luas lahan, estimasi tanggal panen, dan komoditas (misal: cabai, sayur organik, beras).
* **Guaranteed Offtaker Mechanism:** EFN memberikan kepastian harga beli (*Floor Price*) untuk menjaga kesejahteraan petani mitra.
* **Direct-to-Shelf Traceability:** Setiap produk segar di retail memiliki QR Code yang jika discan menampilkan info: *Asal Daerah Petani, Tanggal Panen, & Traceability Quality Certificate*.

### 6.3 System Loyalty Member-Get-Member (MGM) 2-Tier

Sistem rujukan bertingkat yang transparan dan *fraud-proof* untuk mengakselerasi akuisisi pengguna secara organik.

#### Skema MGM 2-Tier:

```
[User A (Referrer Utama)]
   |-- Invites --> [User B (Tier 1 Referral)]  ====> Earns 5% Cashback on B's Purchases
                      |-- Invites --> [User C (Tier 2 Referral)] ====> Earns 2% Cashback on C's Purchases
                                                                       (User A also earns 2% on C)
```

#### Aturan Bisnis & Algoritma Engine:
* **Tier 1 Direct Reward ($T_1$):**
  $$Reward_{A1} = \text{Total Order Value} \times 5\%$$
* **Tier 2 Indirect Reward ($T_2$):**
  $$Reward_{A2} = \text{Total Order Value} \times 2\%$$
* **Multi-Brand Earning & Spending:** Poin yang didapat dari transaksi retail User B dapat dispend oleh User A di seluruh resto FnB EFN atau sebaliknya.
* **Anti-Fraud Guard:** Poin baru dikreditkan setelah status order di Odoo POS/Sales berubah menjadi `Done/Paid` dan tidak ada retur selama $H+1$ hari.

---

## 7. ARSITEKTUR DATA WAREHOUSE, DATA MART & ANALYTICS

Untuk kebutuhan *Business Intelligence (BI)* skala enterprise, EFN memisahkan beban *Operational (OLTP)* dan *Analytical (OLAP)*.

```
+-----------------------------------+
|     OPERATIONAL DB (OLTP)         |
|  - Odoo PostgreSQL                |
|  - Next.js Platform PostgreSQL    |
+-----------------+-----------------+
                  |
                  | (ETL / Change Data Capture via Debezium)
                  v
+-----------------------------------+
|      DATA WAREHOUSE (OLAP)        |
|  - ClickHouse / PostgreSQL Analytics
+-----------------+-----------------+
                  |
                  +-------------------------------------------------+
                  |                                                 |
                  v                                                 v
+-----------------------------------+             +-----------------------------------+
|            DATA MARTS             |             |            DATA MARTS             |
| 1. Sales & Revenue Mart           |             | 3. Customer RFM Profiling Mart    |
| 2. Inventory & Waste Mart         |             | 4. Agri Yield & Volatility Mart   |
+-----------------+-----------------+             +-----------------+-----------------+
                  |                                                 |
                  +------------------------+------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                         DASHBOARD & PRESENTATION LAYER                            |
|             (Metabase / Apache Superset / Executive Mobile BI Dashboard)          |
+-----------------------------------------------------------------------------------+
```

### Rincian Data Marts:
1. **Sales & Revenue Data Mart:** Analisis Sales Velocity, Gross Margin per Brand, Basket Size Analysis (Cross-selling antara Groceries dan FnB).
2. **Inventory & Waste Data Mart:** Tracking *Stock Turn Ratio*, barang mendekati kadaluarsa (*Near-Expiry Tracker*), dan rasio *Food Waste* di dapur FnB.
3. **Customer RFM Profiling Mart:** Pengelompokan pelanggan berdasarkan *Recency, Frequency, Monetary* untuk *hyper-personalized marketing campaigns*.
4. **Agri-Supply & Volatility Mart:** Pemantauan fluktuasi harga komoditas pasar vs harga beli petani mitra vs marjin retail.

---

## 8. ARSITEKTUR INTEGRASI & API READINESS

Sistem EFN dibangun dengan prinsip **API-First Architecture** dengan ketersediaan spec OpenAPI 3.0 / Swagger.

```
+-----------------------------------------------------------------------------------+
|                                 EFN API GATEWAY                                   |
+--------------------+----------------------+-------------------+-------------------+
| REST API           | GraphQL API          | Webhooks Event    | Third-Party Open  |
| (CRUD Operations)  | (Complex Querying)   | (Real-time Push)  | API Developer Hub |
+--------------------+----------------------+-------------------+-------------------+
```

### Integrasi Utama Pihak Ketiga:
* **3PL Logistics & Delivery Aggregators:**
  * Auto-dispatch order ke GoSend / GrabExpress / Lalamove berdasarkan kriteria *Lowest Fare* atau *Fastest Driver Arrival*.
  * Sync menu, ketersediaan stok, dan pesanan masuk dari GoFood, GrabFood, dan ShopeeFood langsung ke KDS POS EFN.
* **Payment Gateways & E-Wallets:**
  * Support Dynamic QRIS, Credit Card, Virtual Account (BCA, Mandiri, BRI, BNI), ShopeePay, GoPay, OVO, Dana.
* **Supplier & Farmer Portal APIs:**
  * Open API untuk supplier skala besar mengunggah e-Faktur dan e-Delivery Order (e-DO) secara otomatis.

---

## 9. SPESIFIKASI INFRASTRUKTUR & CLOUD DEPLOYMENT

Platform EFN mengadopsi arsitektur **Cloud-Native Containerized Infrastructure** dengan dukungan *High Availability (HA)* dan *Auto-Scaling*.

### Topologi Infrastruktur Cloud (GCP / AWS / Private Cloud):

```
+-----------------------------------------------------------------------------------+
|                         CLOUDFLARE CDN / WAF / DDOS PROTECTION                    |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                              LOAD BALANCER (NGINX / KONG)                         |
+-----------------------------------------+-----------------------------------------+
                                          |
       +----------------------------------+----------------------------------+
       |                                                                     |
       v                                                                     v
+---------------------------------------+   +---------------------------------------+
|  FRONTEND KUBERNETES CLUSTER (EKS/GKE)|   |   BACKEND KUBERNETES CLUSTER (EKS/GKE)  |
|  - Next.js Web/Apps Pods (Auto-scale) |   |   - Odoo CE 19 Application Pods       |
|  - Node.js API Microservices Pods     |   |   - Python Custom Services            |
+-------------------+-------------------+   +-------------------+-------------------+
                    |                                           |
                    +---------------------+---------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                                DATA & CACHING LAYER                               |
|  +-------------------------------------+  +------------------------------------+  |
|  | PostgreSQL HA (Primary + Replicas)  |  | Redis Cluster (Session & Cache)    |  |
|  +-------------------------------------+  +------------------------------------+  |
|  +-------------------------------------+  +------------------------------------+  |
|  | RabbitMQ / Kafka (Message Broker)   |  | S3-Compatible Object Storage       |  |
|  +-------------------------------------+  +------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

### Stack Teknologi & Komponen:

| Component | Technology / Tool Chosen | Description / Purpose |
| :--- | :--- | :--- |
| **Frontend Web & PWA** | Next.js (React), TailwindCSS | Ultra-fast Server-Side Rendering (SSR) & Incremental Static Regeneration (ISR). |
| **Mobile Apps** | React Native / Next.js PWA Hybrid | Cross-platform iOS & Android single codebase. |
| **Core ERP Engine** | Odoo CE 19 (Python 3.11/3.12) | Back-office, Accounting, WMS, Procurement, Core Sales. |
| **Operational DB** | PostgreSQL 16/17 (PgBouncer HA) | Database transaksional utama dengan Read/Write Splitting. |
| **Analytics DB** | ClickHouse / PostgreSQL OLAP | Data Warehouse performa tinggi untuk analitik jutaan baris data. |
| **Message Broker** | RabbitMQ / Apache Kafka | Pengelolaan event asinkronus (Queueing Order, Notifikasi, MGM calculation). |
| **Caching & In-Memory** | Redis Cluster | Caching katalog produk, price-list, dan session management. |
| **API Gateway** | Kong API Gateway | Authentication, rate-limiting, routing, dan security guard. |
| **Containerization** | Docker + Kubernetes (K8s) | Orchestration, auto-healing, dan auto-scaling saat traffic melonjak. |

---

## 10. KESIMPULAN & LANGKAH IMPLEMENTASI NEXT STEPS

Spesifikasi sistem ini dirancang untuk memberikan **Era Food and Nourishment (EFN)** keunggulan kompetitif mutlak melalui:
1. Penghematan efisiensi biaya ERP via Odoo CE 19.
2. Kecepatan dan user experience mutakhir via Next.js Super-App.
3. Keterikatan pelanggan jangka panjang melalui Komunitas, Transparansi Harga, dan MGM 2-Tier.
4. Keunggulan pasokan langsung dari petani (*Agri-Supply Dominance*).

### Tahapan Pelaksanaan Proyek (Roadmap):
* **Fase 1 (Bulan 1-3):** Setup Core Odoo CE 19 ERP, Database Schema, Modernisasi POS (FnB & Retail), dan API Gateway Base.
* **Fase 2 (Bulan 4-6):** Development Next.js Super-App & Brand Apps, Modul MGM 2-Tier, dan Integrasi 3PL Delivery/Payment.
* **Fase 3 (Bulan 7-8):** Development Farmer Portal, Modul Komunitas & Komparasi Harga, dan Integrasi Data Warehouse / Analytics.
* **Fase 4 (Bulan 9):** User Acceptance Testing (UAT), Security Penetration Test, Load Testing, dan National Rollout.