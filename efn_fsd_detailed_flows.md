# Functional Specification Document (FSD) - Detailed Flows
## Era Food and Nourishment (EFN)

Dokumen ini merupakan turunan dari FSD utama, berfokus pada detail alur logika (logic flow) untuk dua fitur paling krusial yang menjembatani operasional fisik dan kecerdasan buatan dalam ekosistem EFN.

---

## 1. Alur Logika Kitting & Phantom BoM (Supply Chain Inter-Company)

**Konteks Bisnis:** 
F&B Outlet tidak boleh disibukkan dengan perhitungan bahan mentah (gramasi) saat melakukan stok opname. Semua bahan harus dikirim dari Central Kitchen (F&B) atau Warehouse (Supermarket) dalam bentuk "Paket Satuan Menu" (Kitting).

**Aktor / Sistem Terlibat:**
*   **Odoo Inventory & Manufacturing (MRP):** Mengelola Phantom BoM.
*   **Agentic AI Script:** Mengotomatisasi SO (Sales Order) dan PO (Purchase Order) antar PT.
*   **POS Kasir:** Memotong stok.

**Detail Alur Kerja (Workflow):**

1. **Setup Data Induk (Master Data) di Odoo:**
   *   Item A: "Ayam Utuh" (UoM: Kg) - *Stok di Supermarket*
   *   Item B: "Tepung Bumbu" (UoM: Kg) - *Stok di Supermarket*
   *   Item C: "Paket Ayam Geprek Siap Goreng" (UoM: Porsi) - *Stok di Central Kitchen & Outlet F&B*
   *   **Phantom BoM:** Di-set di Odoo bahwa 1 Porsi Item C = 0.2 Kg Item A + 0.05 Kg Item B.

2. **Trigger Pemesanan (Inter-Company Transfer):**
   *   F&B Central Kitchen membuat *Internal Requisition* untuk 100 Porsi "Paket Ayam Geprek".
   *   **[Auto-Action]** Agentic AI mendeteksi permintaan ini lintas-PT. AI men-generate *Purchase Order* (PO) di Odoo F&B dan *Sales Order* (SO) di Odoo Supermarket.

3. **Proses Kitting (Manufaktur Transparan):**
   *   Supermarket mengirimkan 20 Kg Ayam Utuh dan 5 Kg Tepung Bumbu ke Central Kitchen.
   *   Saat barang diterima (Goods Receipt) di Central Kitchen, Odoo MRP secara otomatis memicu *Manufacturing Order* (MO) berstatus *Done*.
   *   Sistem mengubah wujud 20 Kg Ayam + 5 Kg Tepung menjadi 100 "Paket Ayam Geprek" secara instan di sistem (*Phantom BoM effect*).

4. **Konsumsi di POS:**
   *   Outlet memesan 50 Paket dari Central Kitchen.
   *   Pelanggan membeli 1 Ayam Geprek di POS.
   *   Sistem Odoo POS hanya mengurangi 1 Porsi "Paket Ayam Geprek", tanpa perlu melakukan kalkulasi resep yang rumit di level toko (mencegah *lag* pada sistem kasir).

---

## 2. Alur API Validasi OCR "Price Hunter" (Agentic AI Vision)

**Konteks Bisnis:**
Memberikan *reward* kepada pelanggan yang melaporkan harga kompetitor secara valid. Membutuhkan sistem yang sepenuhnya otomatis tanpa admin manusia untuk mencegah penumpukan antrean validasi.

**Aktor / Sistem Terlibat:**
*   **Super-App (Next.js/React Native):** UI/UX Pelanggan.
*   **API Gateway (Node.js):** Orchestrator lalu lintas data.
*   **Ollama (Llama 3.2 Vision):** AI Engine *Open Source* yang berjalan di server EFN.
*   **ClickHouse (Data Warehouse):** Penyimpanan data harga.

**Detail Alur Kerja (Workflow):**

1. **Input Pelanggan (Data Entry):**
   *   Pelanggan di aplikasi membuka fitur *Market Scout*.
   *   Pelanggan mengambil foto *price tag* di rak supermarket kompetitor.
   *   Pelanggan mengetik: 
       *   Nama Produk: "Susu UHT 1L"
       *   Harga: "18500"
   *   Data (Foto + Teks Input) dikirim via *Multipart API Request* ke Node.js.

2. **Proses AI Vision (Extraction):**
   *   Node.js meneruskan foto ke Ollama Vision API lokal.
   *   *Prompt Engine:* "Extract the product name and price from this price tag image. Return JSON format strictly: {product_name: '', price: ''}".
   *   Ollama Vision memproses gambar (estimasi 2-4 detik) dan mengembalikan JSON: `{"product_name": "Susu UHT Ful Krim 1L", "price": "18.500"}`.

3. **Validasi Kesesuaian (Matching Logic di Node.js):**
   *   *Harga Check:* Bandingkan Input "18500" dengan AI "18.500" -> **Match**.
   *   *Nama Check:* Gunakan algoritma *Levenshtein Distance* (atau *Fuzzy String Matching*) untuk membandingkan "Susu UHT 1L" dengan "Susu UHT Ful Krim 1L". Jika similarity > 75% -> **Match**.

4. **Eksekusi Hasil (Reward & Data Storage):**
   *   Jika status **Match**:
       *   Node.js menembak API Odoo untuk men-generate *Promo Code/Voucher* senilai Rp2.000, lalu mengikatnya ke `user_id` pelanggan.
       *   Node.js mengirim insert query ke ClickHouse: `INSERT INTO competitor_prices (date, product, price, competitor_name, user_id, status) VALUES (...)`.
       *   Push Notification ke aplikasi pelanggan: "Validasi Berhasil! Voucher Rp2.000 sudah masuk ke dompet Anda."
   *   Jika status **Not Match** (misal blur atau bukan label harga):
       *   Push Notification: "Maaf, foto kurang jelas atau harga tidak sesuai. Silakan foto ulang."

---
*Dokumen ini dirancang untuk diserahkan kepada tim Developer (Backend & Odoo Engineer) sebagai acuan logika programming.*
