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
| R-B12 | **Spam/pesanan iseng lewat QR meja** (W-07, G22): slip QR difoto/dibagikan, tamu iseng, atau pesanan ke meja yang salah → makanan terbuang, dapur kacau, tamu lain terganggu. *(Baru 19 Okt 2026, usulan draf kontrak G22 §H.)* | 3 | 3 | 9 | Token QR per sesi meja (HMAC, TTL ≤ 4 jam, dicabut saat bayar/cetak ulang, `docs/04` §14.A.2); batas kiriman/item/nominal per sesi + rate limit; **default konfirmasi pelayan** (L-71); ALTCHA opsional (`FNB_CAPTCHA_MODE`); kill switch per POS/PT; runbook `docs/runbooks/fnb-qr-orders.md` (A3/A4/C3). Pemilik: Ops F&B + BFF. Terkait R-S03, R-B08. **Diperbarui 19 Okt 2026 (r15, kontrak v9.1):** review r15 tidak menemukan temuan Critical/High pada pemesanan QR. Status mitigasi:
1. Batas kiriman diselaraskan **10** per sesi di BFF & Odoo (L-76).
2. Kiriman QR selalu order baru dan tidak menimpa order pelayan (L-81).
3. Filter catatan identik BFF/Odoo: NFKC + buang zero-width/bidi (SEC-168, upaya terbaik).
4. `print_no` monoton agar QR tidak buntu setelah retensi 400 hari (SEC-167, R-T20).
5. Rate limit event sesudah guard (SEC-173).

Penyalahgunaan **poin** QR oleh pemegang token dipisah ke **R-B14**. Implementasi butir 3–5 dikerjakan paralel G22, **belum diverifikasi** test/re-review. |
| R-B13 | **Fraud kasir pada jalur uang baru G20** — bayar pesanan online di kasir (W-30/E-45) dan retur tanpa struk dengan persetujuan Store Manager (W-24b/E-41): kolusi kasir–Store Manager, PIN Store Manager dibagikan, refund pelunasan ke laci lain, kode bayar dipakai setelah batal. *(Baru 19 Okt 2026.)* | 3 | 3 | 9 | Server-side: lookup kode bayar tanpa data pelanggan + rate limit 5/15 menit + klaim lookup; refund POS pelunasan hanya dengan otorisasi BFF setelah approver CS & outlet sama (SEC-145, L-60); bayar setelah batal → `review` (L-57); PIN diverifikasi server, kunci 15 menit, approver ≠ kasir, catatan append-only 730 h (L-51/L-54); SoD SOD-02 Kasir ⟂ Store Manager (W-31). **Syarat aktivasi:** notifikasi approver + laporan harian retur tanpa struk (SEC-146), SOP lockout & lookup ganda (SEC-150/152). Pemilik: Ops outlet + Finance + Internal Audit. Terkait R-S10, R-S14, R-O05, R-O06. **Diperbarui 19 Okt 2026 (r15):** jalur baru G22 yang berkaitan dengan kasir adalah **refund order QR sebelum sesi ditutup** (SEC-164). Tanpa perbaikan, poin tetap di-earn penuh atas belanja yang uangnya sudah dikembalikan, dan ini membuka peluang kolusi kasir. Mitigasi kontrak K3: `eligible_amount` di `close.orders[]` dihitung neto refund dan order neto ≤ 0 tidak dikirim. Refund setelah close → reverse `earn_channel:"qr"`. Reverse yang datang sebelum BFF memproses close tidak boleh hilang atau dobel. **Pemblokir aktivasi poin QR**, dikerjakan paralel, belum diverifikasi. Redeem poin online tanpa step-up (SEC-170) dipisah ke **R-B15**. |
| R-B14 | **Pencurian/penguncian poin QR meja** (W-07 poin, G22, review r15). Penyebabnya gabungan empat temuan: (1) klaim poin sesi meja bersifat "siapa cepat": cukup Bearer + token meja (slip difoto dari meja sebelah, tamu sebelumnya), dan staf tidak punya jalur melihat/reset klaim (SEC-169); (2) earn QR berebut dengan earn OTP kasir karena `pos_member_attached` tidak dikirim (SEC-165); (3) sesi yang tidak tertutup menyerap order tamu berikutnya di meja yang sama (SEC-166); (4) earn atas order yang sudah di-refund (SEC-164). Dampaknya poin berpindah ke orang yang salah, member sah kehilangan poin (pemulihan manual), keluhan CS, dan liabilitas poin yang tidak semestinya. *(Baru 19 Okt 2026.)* | 3 | 3 | 9 | **Poin QR OFF** (`FNB_QR_POINTS_ENABLED=false`, default production; L-82 b). Pemesanan QR tanpa poin boleh dipilot. Kontrak v9.1: `pos_member_attached` wajib dan BFF tidak meng-earn pengklaim QR untuk order ber-member POS (K2, SEC-165); lekat order hanya ke sesi `open`, `date_order ≥ opened_at`, dan hanya order kiriman QR/jangkar/dibawa (SEC-166); neto refund (K3, SEC-164); plafon earn harian/per order; anomali `qr_pos_member_conflict`. **Syarat aktivasi:** SEC-164/165/166 terverifikasi (test lintas modul + re-review security) **dan** keputusan SEC-169 (G23: reset klaim oleh staf, OTP kasir mengalahkan klaim, batas perangkat/waktu klaim, indikator "sudah diklaim" di POS; ±2–4 hari Odoo+BFF+FE, perkiraan architect). Runbook `fnb-qr-orders.md` §D. Skor setelah aktivasi dengan semua mitigasi: usulan 2×2 = 4. Pemilik: BFF + Odoo engineer + Ops F&B. Terkait R-B12, R-B13. |
| R-B15 | **Redeem poin online oleh pengambil alih sesi tanpa step-up** (W-12, G22, SEC-170). Perangkat yang dipinjam atau sesi yang dicuri dapat menukar hingga `redeem_max_pct` saldo untuk pesanan bayar-di-kasir lalu mengambilnya. Env `ONLINE_REDEEM_STEPUP_MIN_POINTS` yang disebut kontrak v9.0 ternyata belum ada di kode. *(Baru 19 Okt 2026.)* | 2 | 3 | 6 | **Risiko residu diterima sementara (L-82 a), ditunda ke G23.** JWT 1 jam tanpa refresh token, ALTCHA, plafon `redeem_max_pct` + porsi tunai ≥ Rp1, reservasi atomik `lockMember`, kode ambil, dan poin online **OFF** di production (`ONLINE_LOYALTY_REDEEM_ENABLED=false`). G23 menambahkan step-up `ONLINE_REDEEM_STEPUP_MIN_POINTS` (memakai `consumeStepUp`/`X-Step-Up-Token`) + notifikasi transaksional "poin Anda dipakai" (±1–2 hari BFF + 0,5 hari FE, perkiraan architect). Tinjau ulang sebelum poin online diaktifkan di production. Pemilik: BFF + Superadmin. Terkait R-S04, R-B13. |
| R-B16 | **Nomor WhatsApp tidak resmi diblokir / pelanggaran ToS Meta** (ADR-044, supermarket). Baileys melanggar ketentuan WhatsApp; Meta dapat memblokir nomor kapan saja tanpa banding dan tanpa SLA; library bisa berhenti bekerja saat protokol berubah. Dampak: kanal promo & notify-me hilang mendadak, member melihat nomor baru (dicurigai penipuan), potensi klaim kontraktual dari Meta. *(Baru 18 Nov 2026.)* | 4 | 2 | 8 | Dipakai **hanya** untuk promo & notify-me; nomor & container terpisah dari WABA resmi (OTP/pesanan/loyalty tetap Cloud API, sehingga ban tidak menyentuh transaksi); batas teknis anti-ban (10/menit, 300/hari, 1/member/hari, jitter); kanal lain tetap jalan (push, inbox, email); **OFF di production sampai penerimaan risiko tertulis pemilik proyek + Legal** (Q-SM14). Dampak dinilai 2 karena terisolasi; P tinggi karena pemakaian massal pesan promo adalah pola yang paling sering diblokir (penilaian architect, belum ada data). Tinjau bila ban ≥ 2× dalam 90 hari. Pemilik: pemilik proyek + Marketing. Terkait R-S21, R-B17. |
| R-B17 | **Promo personal berlebihan / anggaran voucher bocor** (ADR-047): cap frekuensi & anggaran kampanye **default nonaktif** (keputusan pemilik proyek 18 Nov 2026); segmen otomatis yang salah atau kampanye yang disetujui terburu-buru bisa mengirim terlalu banyak pesan (keluhan, opt-out massal, risiko ban R-B16) atau voucher melebihi anggaran. *(Baru 18 Nov 2026.)* | 3 | 3 | 9 | Setiap kampanye wajib approval marketing di Odoo (4-eyes di atas ambang, Q-SM24); batas teknis per kanal tetap aktif walau cap bisnis nonaktif; laporan hasil kampanye & realisasi voucher (`fact_campaign_result`); `PROMO_CAMPAIGN_ENABLED=false` di production sampai DPO. Rekomendasi architect: aktifkan cap frekuensi sebelum kampanye pertama ke > 1.000 member. Pemilik: Marketing + Finance. Terkait R-B16, R-L13. |

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
| R-T18 | **Hook enkripsi ORM PII Odoo terlewati jalur tulis lain** (ADR-036, E-38): modul CE/kustom yang mendefinisikan ulang field cakupan (`phone`, `email`, `vat`, `name`, …) atau menulis lewat SQL/flush yang tidak tertangkap → data pribadi tersimpan **plaintext diam-diam**. **Terbukti nyata** di PoC G19 (`base_vat` mendefinisikan ulang `vat`, L-39). *(Diusulkan ADR-036 18 Okt; dimasukkan ke register 19 Okt 2026.)* | 2 | 4 | 8 | Fail-closed (SEC-137: tulis ke record terlindungi ditolak bila field bukan `EfnPiiChar`) + CRITICAL saat registry dimuat; modul penghubung per modul CE (`efn_pii_base_vat`, `efn_pii_account`); alarm baca `plaintext_in_protected`; query verifikasi `count_unbound` + alert `EfnOdooPiiUnbound`/`EfnOdooPiiPlaintextResidual`; **gerbang CI `odoo-test-pii`** (AC-4: semua modul + 3 kunci uji acak, ada di `needs` gerbang akhir `ci.yml`). Skor 3×4 = 12 sebelum AC-4, kini 2×4 = 8. Wajib diulang setiap menambah modul Odoo atau upgrade mayor (R-T13). Pemilik: efn-odoo-engineer + devsecops. Terkait R-L01, R-L10. |
| R-T19 | **Kebocoran frekuensi token nama/blind index** (ADR-036 §0.5): pemegang dump DB tanpa kunci dapat melihat kesamaan token dan menebak awalan nama populer atau kesamaan HP antar-record. *(Usulan ADR-036; dimasukkan 19 Okt 2026.)* | 2 | 2 | 4 | Kunci HMAC terpisah per jenis (`odoo_pii_bidx_key`, `odoo_pii_name_key`), padding token deterministik per record (SEC-149), tanpa trigram/infiks, dump & WAL-G terenkripsi, akses dump dibatasi. **Risiko diterima** (ADR-036, DPIA DR-22). Pemilik: IT + Security. |
| R-T20 | **Ketergantungan pemesanan QR & poin online pada rantai BFF → gateway → Odoo** (G22): BFF/Odoo mati, API key QR kedaluwarsa (R-T15), circuit breaker terbuka, atau outbox event tertunda → tamu tidak bisa memesan dari ponsel, poin QR/online tertunda, refund order QR `earn_pending`. *(Baru 19 Okt 2026.)* | 3 | 2 | 6 | Bukan jalur kas: POS, bayar, dan dapur tetap jalan (pesan manual ke pelayan, `503 ordering_unavailable`); tanpa antrean kiriman (tidak ada pesanan ganda), rekonsiliasi `efn_qr_status`; TTL token sebagai pengaman bila event tutup sesi tertunda; rotasi API key 30 h + alert (W-11); runbook `fnb-qr-orders.md` C1/C2. Alert deadman sweeper sesi & kiriman `submitting` macet = **usulan** (devsecops/data-ai). Pemilik: BFF + devsecops. Terkait R-T10, R-T15. **Diperbarui 19 Okt 2026 (r15/QA G22):** muncul tiga penyebab gangguan ketersediaan yang pasti atau mudah terjadi:
1. **SEC-167:** kunci idempotensi buka sesi berbasis hitungan. Mulai hari ke-401 cetak QR buntu di setiap meja aktif. Mitigasi: `print_no` monoton yang tidak ikut retensi, BFF `n ≤ 99999999`. Syarat sebelum 400 hari produksi QR.
2. **QA P3:** `efn_qr_session_hours` lebih pendek dari TTL token BFF membuat tamu memegang QR "sah" yang ditolak. Mitigasi: syarat konfigurasi jam Odoo × 3600 ≥ `FNB_TABLE_SESSION_TTL_SECONDS` (L-82 d, runbook §1).
3. **QA P3:** `orders[]` melebihi batas membuat poin sesi hilang diam-diam. Mitigasi: batas 100 entri / body 16 KB di kedua sisi.

Perbaikan cetak ulang saat sesi Odoo sudah `expired` (`reissue` diteruskan) diterapkan QA. Status: dikerjakan paralel, **belum diverifikasi**. |
| R-T21 | **Forecast supermarket tidak akurat / histori tidak memadai** (ADR-046): belum ada histori penjualan supermarket nyata (simulasi G48 hanya membuktikan mekanisme), SKU baru tanpa histori, hari OOS tersensor, tanggal Lebaran/cuti bersama dari library `holidays` tidak lengkap untuk tahun tertentu, atau jadwal kirim/MOQ salah isi → usulan order terlalu banyak (waste, near-expiry) atau terlalu sedikit (OOS). *(Baru 18 Nov 2026.)* | 4 | 3 | 12 | Semua usulan wajib approval manusia (kepala toko/buyer) + log bias; sakelar `efn_forecast_engine` v1/v2 per gudang, pindah v2 hanya bila WAPE backtest lebih baik dari v1 & seasonal naive (L-148); censoring hari OOS; override kalender manual (tanggal SKB pemerintah); shelf-life cap; KPI MAPE/WAPE/bias di Insight; worker gagal → usulan tidak dibuat, reorder rule terakhir berlaku. Pemilik: efn-data-ai-engineer + Category manager. Terkait R-T06, R-T11. |

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
| R-L10 | **PII pembeli tersimpan di Odoo** (Q-ON7/Q-P21, 10 Okt 2026): nama, NPWP, alamat, HP, email pembeli online yang meminta faktur masuk `res.partner` Odoo — menyimpang dari prinsip minimisasi §10.1 butir 4 (partner generik). Risiko: akses staf Odoo luas (Sales/Accounting lintas PT), erasure tidak lengkap (faktur 10 th), kebocoran lewat ekspor/laporan/ETL, dan staf mengetik PII di chatter. *(Baru 10 Okt 2026.)* | 3 | 4 | 12 | Disimpan **hanya atas permintaan pelanggan** (opsional di checkout); kolom PII **terenkripsi** (Q-E3, paket W-08) **sebelum** W-06 diaktifkan; record rule per PT; tidak disalin ke ClickHouse; erasure menganonimkan partner pembeli kecuali data wajib faktur 10 th; inventaris PDP v7 §2.2d; tinjauan Legal (DPO sementara) + review keamanan sebelum aktif. Pemilik: Legal/DPO + odoo-engineer. Terkait R-L01, R-S10. **Diperbarui 19 Okt 2026:** enkripsi dibangun (E-38a/b, G20–G21) dan profil pembeli hanya masuk Odoo bila `efn_pii` aktif (L-63); erasure partner pembeli **belum** (L-68, menunggu DPO Q-P24). |
| R-L11 | **Poin hangus & anonimisasi otomatis tanpa dasar/pemberitahuan yang sah** (W-17, 18-14, G22): job menghanguskan poin member yang 12 bulan tidak aktif dan (opsional) menganonimkan akunnya. Bila teks syarat program belum disetujui, notifikasi WA gagal/tidak terkirim (nomor mati, template digolongkan MARKETING tanpa consent), definisi "aktif" keliru, atau bug jangkar → member kehilangan nilai ekonomi secara massal, keluhan/sengketa konsumen, temuan UU PDP (keputusan otomatis, Pasal 10 ayat 1; retensi, Pasal 16(2) g) dan reputasi. *(Baru 19 Okt 2026, usulan draf kontrak G22 §H.)* | 3 | 4 | 12 | **OFF di production** (`LOYALTY_EXPIRY_ENABLED`, `LOYALTY_INACTIVE_ANONYMIZE`) sampai teks syarat program disetujui Legal (Q-F22-4) dan template disetujui Meta (L-73); pemberitahuan ≥ 30 hari dijamin desain (`due_at = GREATEST(...)`), info di aplikasi; dry-run pertama per lingkungan, pengaman massal `LOYALTY_EXPIRY_MAX_PER_RUN` + alert `EfnLoyaltyExpiryAborted`, deadman 26 jam; epoch = tanggal go-live (hangus pertama ≥ 12 bulan setelah go-live); cek ulang jangkar & pesanan/DSAR terbuka saat expire; jalur keberatan CS + SOP Finance (DPIA M-11, Q-P27); RoPA & DPIA `DPIA_DRAFT.md` §13. Pemilik: Legal/DPO + Finance + BFF. Terkait R-L01, R-B05. **Diperbarui 19 Okt 2026 (r15 SEC-171):** review r15 menilai gerbang production sudah kuat (Superadmin + `EFN_ALLOW_FEATURE_ON`). Celahnya: begitu gerbang dibuka, kode mengirim WA ke semua member bersaldo tanpa memeriksa consent, sehingga perubahan dasar hukum butuh rilis kode. Mitigasi kontrak v9.1 §14.D.4: `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT` **default `true`**. Notifikasi WA hanya untuk member dengan consent `marketing` aktif; yang lain mendapat info in-app dan `due_at` tetap ≥ 30 hari. Nilai `false` hanya dengan keputusan Legal tertulis (Q-P25/D-22-16). Implementasi paralel, **belum diverifikasi**. Syarat aktivasi tambahan di runbook `loyalty-inactivity-expiry.md`. |
| R-L12 | **Bundel migrasi ESB bocor** (G43, ADR-042): bundel export (data bisnis lima brand — produk, resep, harga pokok, saldo, penjualan per baris 24 bulan — dan nama partner B2B/supplier yang ternyata perorangan) tertinggal di laptop, VPS, arsip, tiket, atau chat → kebocoran rahasia dagang dan data pribadi (UU PDP). *(Usulan ADR-042; dimasukkan 18 Nov 2026.)* | 2 | 4 | 8 | AES-256-GCM + manifest HMAC (kunci `migration_bundle_key`, HKDF terpisah), plaintext hanya di `tmpfs`, whitelist kunci + validator fail closed (PIC/alamat/telepon/email/pelaku dokumen dibuang), segmen histori per company × bulan, hapus **≤ 90 hari setelah berita acara** (Q-ESB24), larangan bundel di repo/tiket/chat, overlay migrasi hanya selama jendela migrasi; inventaris PDP §2.8.14 (v18). Pemilik: devsecops + DPO. Terkait R-L01, R-S01, usulan R-S19. |
| R-L13 | **Tracking perilaku & device ID tanpa dasar sah / profiling berlebihan** (ADR-047): device ID first-party yang ditautkan ke akun adalah data pribadi (pseudonim), bukan anonim; inferensi "alasan tidak beli" (stok habis, harga vs Price Hunter/kebiasaan) dan segmen promo = profiling untuk pemasaran. Banner yang menyesatkan, pelacakan sebelum consent, retensi melebihi tujuan, atau erasure yang tidak menjangkau ClickHouse → temuan UU PDP, keluhan, reputasi. *(Baru 18 Nov 2026.)* | 3 | 4 | 12 | Purpose consent baru `tracking`; device ID dibuat klien **hanya setelah** consent, server menyimpan HMAC (`device_key`); tanpa PII di event (allowlist field, redaksi istilah pencarian); retensi staging 7 h / mentah 90 h / agregat anonim 2 th (k ≥ 10, usulan); erasure menghapus tautan, staging, dan baris ClickHouse by `subject_key`; halaman "kenapa saya mendapat promo ini" + opsi keluar personalisasi; `BEHAVIOR_TRACKING_ENABLED=false` di production sampai DPO + DPIA (Q-SM16, Q-SM17, Q-SM26). Pemilik: DPO + BFF + data-AI. Terkait R-L01, R-B17. |
| R-L14 | **Struktur DC di PT yang sama tidak sesuai ketentuan perizinan gudang / PPN** (ADR-045): KBLI & pendaftaran gudang DC belum dipastikan (KBLI 2025 mengubah 52101; kemungkinan 52109/52102 atau cukup lokasi ritel), perlakuan PPN perpindahan DC → outlet dalam satu PT dan NITKU per lokasi di bawah PMK-81/2024 belum dikonfirmasi; rujukan PER-07/PJ/2021 di brief ternyata mengatur PPN ekspor/impor (sumber sekunder). Bila salah: sanksi administratif gudang, koreksi pajak, atau harus pindah ke PT terpisah setelah go-live. *(Baru 18 Nov 2026.)* | 2 | 4 | 8 | Konfirmasi konsultan pajak (fit-gap Q-SM5) & Legal (Q-SM20) **sebelum go-live pilot**; perpindahan DC → outlet tetap transfer stok internal; jalur alternatif PT terpisah dibungkus satu titik `_efn_create_supply_document()` (biaya pindah ±3–5 minggu-engineer bila terjadi sebelum go-live). Pemilik: Finance + Legal. Terkait R-L03, R-L04. |

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
| R-S16 | **Pemberian akses data pelanggan/keuangan lewat peran tanpa pemeriksa kedua** (W-31/E-47, G21): sinkron jabatan → peran, pengecualian SoD, dan penandaan peran dikelola **satu Superadmin** (L-65, R-B11) dan Access Rights. Salah konfigurasi atau penyalahgunaan memberi akses CS/keuangan/audit lintas PT tanpa empat mata. *(Baru 19 Okt 2026, dari review r14 SEC-154/155/158/159.)* | 2 | 4 | 8 | Sinkron jabatan hanya peran non-sensitif (Kasir & Staf Gudang; daftar grup sensitif `efn_role.sensitive_group_xmlids`, L-67/L-74); pengecualian SoD maks 90 hari + alasan wajib + notifikasi otomatis ke Auditor + tampil di review akses bulanan; Auditor tanpa `base.group_allow_export` (SEC-155); semua perubahan grup/peran tercatat `efn_audit` (log kontrol tanpa GC); SOD-03 wajib `block` sebelum kebijakan poin (L-69). Sisa: notifikasi perubahan aturan SoD (SEC-158), penandaan admin sebagai "managed" (SEC-159). Pemilik: Superadmin + Internal Audit. Terkait R-S10, R-S14, R-B11. **Diperbarui 19 Okt 2026 (r15 SEC-172, L-82 c — DITERIMA):** peran **Sales Manager Online** (6 PT) kini memuat `stock.group_stock_user`, setara peran Merchandiser. Alasannya, pembatalan pesanan online membaca/menyentuh reservasi stok (`purchase_stock`). Konsekuensinya, pemegang peran dapat mengonfirmasi/membatalkan SO **dan** memvalidasi transfer stok. Alternatif `sudo()` terbatas ditolak karena melemahkan pemeriksaan akses standar. Kompensasi: review akses bulanan wajib mencakup pemegang peran ini dan sampel transfer yang mereka validasi; residu dicatat di README `efn_setup_role`. Aturan SoD "Sales Manager Online ⟂ validasi picking" (report_only) = opsi G23 bila Internal Audit memintanya (±0,5 hari functional). |

| R-S17 | **Akun admin ESB dipakai untuk ekstraksi datamart** (G41, ADR-040). Akun yang tersedia untuk `esb-extract` adalah akun admin ESB per brand; API ESB memiliki endpoint tulis (`push`, `import`, `authorize`). Bug, salah konfigurasi, atau bocornya password memberi akses **tulis** ke ERP produksi klien (stok, harga, dokumen). *(Usulan 17 Nov 2026.)* | 3 | 4 | 12 | Klien HTTP GET-only (kecuali `/auth/login`) ditegakkan di kode + test (L-106); password per brand hanya di `/run/secrets/esb_core_password_<brand>`, tidak di-log; satu-satunya egress ke ESB = `esb-extract` (allowlist host); `ESB_ENABLED=false` di production sampai klien membuat **user API read-only** (Q-ESB1), lalu password admin dirotasi. Skor setelah user read-only: usulan 1×3 = 3. Pemilik: IT klien + devsecops. Terkait R-S01, R-S10. |
| R-S21 | **Supply chain library WhatsApp tidak resmi & pengambilalihan sesi** (ADR-044). Baileys = library komunitas (MIT; garis 7.x masih *release candidate* menurut sumber sekunder) yang memegang kredensial sesi akun WhatsApp. Paket tersusupi (typosquat/fork jahat), pembaruan berisi kode pencuri sesi, atau volume sesi bocor → nomor diambil alih untuk penipuan atas nama brand, atau container dipakai sebagai pijakan ke jaringan internal. *(Baru 18 Nov 2026; R-S18..R-S20 dicadangkan G42/G43.)* | 2 | 4 | 8 | Paket resmi di-pin **exact** + lockfile, pembaruan hanya lewat PR yang dibaca manusia (changelog + diff); SBOM + Trivy di CI; sesi terenkripsi AES-256-GCM (`wa_unofficial_session_key`); container tanpa akses PG/Odoo/ClickHouse/Redis BFF, network hanya RabbitMQ + proxy egress allowlist WhatsApp; endpoint pairing hanya WireGuard + mTLS + token admin; prosedur logout + pairing ulang bila curiga; OFF di production sampai Q-SM14. Pemilik: devsecops + security-reviewer. Terkait R-S02, R-S07, R-B16. |

### 4.5 Risiko Operasional & Data

| ID | Risiko | P | D | Skor | Mitigasi |
|---|---|---|---|---|---|
| R-O01 | **Master data produk tidak seragam antar-PT** (SKU, barcode, UoM, kategori) membuat inter-company dan DWH kacau. | 5 | 4 | 20 | Program MDM: 1 katalog induk di holding, mapping SKU per PT, governance owner per kategori. Mulai sebelum coding. |
| R-O02 | **Migrasi data dari sistem lama** (stok, piutang, member) tidak disebut sama sekali. | 4 | 4 | 16 | Inventaris sistem saat ini, rencana migrasi, dry-run 2x, cut-over plan. |
| R-O03 | **ETL 15 menit membuat Odoo lambat** jika query full-table. | 3 | 3 | 9 | Incremental (`write_date` watermark), baca dari replica. |
| R-O04 | **Grading A/B/C berdampak harga** tetapi aturan konversi grade → pricelist belum ditulis. | 4 | 3 | 12 | Definisikan matrix grade × komoditas → harga beli dan harga jual, serta siapa yang berwenang mengubah. |
| R-O05 | **Retur, void, refund, komplain** tidak ada di satu pun flow. | 5 | 3 | 15 | Tambahkan flow: void sebelum bayar, refund setelah bayar (POS & online), retur barang, komplain pesanan 3PL, dampak ke poin MGM (clawback). |
| R-O06 | **Cash management kasir** (shift, kas awal, setoran, selisih) tidak disebut. | 4 | 3 | 12 | Odoo POS session punya ini; tetapkan SOP dan laporan selisih per shift. |
| R-O07 | **Keluhan pengguna atas pencarian/urutan data terenkripsi** (ADR-036): nama pelanggan/vendor tidak bisa dicari potongan di tengah kata dan tidak terurut abjad → staf memakai *workaround* ekspor CSV/spreadsheet yang membawa data pribadi keluar dari kontrol. *(Usulan ADR-036; dimasukkan 19 Okt 2026.)* | 3 | 2 | 6 | Pencarian nama lengkap, HP/email/NPWP lengkap, dan awalan kata ≥ 3 huruf tetap jalan; pelatihan pengguna; opsi O2 urutan "terbaru dulu" (+1–2 hari, Q-PII11); Auditor tanpa hak ekspor massal (SEC-155); audit ekspor CSV `res.partner` (ADR-036 tahap 2, DPIA M-10). Pemilik: functional + Ops. Terkait R-B08, R-L01. |
| R-O08 | **Akun, jurnal, dan produk akuntansi PLACEHOLDER G20–G22 tidak ditinjau Finance sebelum go-live**: kliring bayar di kasir (`1199xx`, `KKSR`), Liabilitas/Beban/Pendapatan Poin Kedaluwarsa per PT, Utang/Piutang Poin Antar-PT, Selisih Pembulatan Pajak, metode Poin EFN, fiscal position F&B. Akun salah → kliring tidak nol, RECON liabilitas poin menyimpang permanen (SEC-160), laporan per PT salah, koreksi jurnal setelah tutup buku. *(Baru 19 Okt 2026.)* | 3 | 4 | 12 | Semua akun configurable per PT (tanpa akun di kode, ADR-027); cron akrual tidak menjurnal tanpa akun (galat + aktivitas Finance); alert `EFN_LOYALTY_RECON`; checklist aktivasi per PT (Finance menandatangani daftar akun sebelum `ONLINE_ENABLED`/kebijakan poin pertama/`LOYALTY_EXPIRY_ENABLED`); UAT tutup buku bulan pertama di staging. Pemilik: Finance + efn-odoo-functional. Terkait R-L03, R-T17, R-O05. |
| R-O09 | **ESB mengubah skema/perilaku API** (G41, ADR-040): ESB Core adalah SaaS pihak ketiga; nama field, paging (mis. batas tetap 20 baris), kode status dokumen, atau endpoint laporan bisa berubah tanpa pemberitahuan → ekstraksi gagal diam-diam, mart basi/salah, angka migrasi G43 dan forecast G44 menyimpang. *(Usulan 17 Nov 2026.)* | 3 | 3 | 9 | Parser toleran + gagal eksplisit (`extract_run.errors`/`status`), test kontrak dengan fixture sintetis, `v_esb_data_quality` (umur data, alias tak terpetakan, non-EA, cabang tanpa penjualan) + alert umur > 36 jam (usulan), raw JSON 36 bulan agar transform bisa diulang tanpa memanggil ESB lagi, rekonsiliasi bulanan sebelum cutover (L-114). Pemilik: efn-data-ai-engineer. Terkait R-O02. |
| R-O10 | **Beban ekstraksi mengganggu server ESB / akun API diblokir** (G41): backfill 24 bulan ±26–30 panggilan per toko-hari; tanpa kendali, operasional toko yang masih memakai ESB bisa melambat atau vendor memblokir akun, sehingga migrasi tertunda. *(Usulan 17 Nov 2026.)* | 3 | 3 | 9 | Token bucket per brand (2 req/dtk), 4 worker, backoff 429/5xx, `ESB_MAX_CALLS_PER_RUN`, backfill hanya 22:00–05:00 WIB, satu brand per waktu, checkpoint resumable (L-111); izin tertulis klien/vendor (Q-ESB7) sebelum backfill production; perkiraan durasi: ±4–5 malam per 10 toko. Pemilik: IT klien + efn-data-ai-engineer. Terkait R-O02, R-O09. |
| R-O12 | **Saldo awal migrasi ESB → Odoo ganda/salah** (G43, ADR-042): nilai persediaan masuk dua kali (dari `stock.quant` hasil opname **dan** saldo akun persediaan TB ESB), saldo AR/AP per partner ≠ total dokumen terbuka, atau valuasi otomatis CE 19 untuk penyesuaian stok bertanggal mundur tidak sesuai harapan → neraca pembukaan salah, koreksi jurnal setelah tutup buku bulan pertama. *(Usulan ADR-042 18 Nov; dimasukkan 18 Nov 2026.)* | 3 | 4 | 12 | Satu jurnal pembukaan **draft** per PT dengan lawan akun "Saldo Awal – Kliring"; akun valuasi *Inventory adjustment* = kliring selama cutover; saldo persediaan TB tidak dimuat (atau dengan lawan kliring yang sama); kliring wajib = 0 setelah posting; posting 4-eyes Finance (`efn_migration` tidak memosting); uji valuasi bertanggal mundur di dry-run (Q-ESB16); rekonsiliasi butir 2–4 pemblokir. Fungsi saldo awal di importer default mati sampai data E6+ ada. Pemilik: Finance + efn-odoo-engineer. Terkait R-O02, R-O08, R-L03. |
| R-O13 | **Arsip histori ESB di Odoo bias/tidak lengkap** (G43, ADR-042 + amendemen A1): pemetaan cabang/produk/menu salah, menu tak terpetakan, outflow tanpa scrap, parameter `efn_mig_esb.cutover_date.<CODE>` diubah manual, atau irisan pemuatan bulk gagal sebagian → usulan min/max forecast menyimpang dan laporan histori penjualan di Odoo berbeda dari gudang data. *(Usulan ADR-042; diperluas amendemen 18 Nov 2026.)* | 3 | 3 | 9 | ClickHouse = sumber angka resmi; rekonsiliasi butir 5a–5d (flow, penjualan harian, baris, query pemeriksa company) = 0 sebagai syarat berita acara; ganti-per-irisan idempoten (satu transaksi per company × gudang × bulan); menu arsip untuk menu tidak aktif; parameter cutover hanya diisi skrip (perubahan manual dicatat di berita acara); usulan forecast tetap disetujui planner (K25-12). Pemilik: efn-data-ai-engineer + efn-odoo-engineer + Ops brand. Terkait R-O02, R-O01, R-O09. |
| R-O14 | **Kesalahan restriksi DC / master data assortment** (ADR-045, `efn_assortment`): daftar izin, capability outlet (chiller/freezer/boleh non-halal/izin alkohol), region kontrak supplier, atau flag DSD salah isi. Terlalu ketat → produk tidak pernah diusulkan (OOS diam-diam); terlalu longgar → alkohol ke outlet tanpa izin, produk non-halal ke outlet halal-only, frozen ke outlet tanpa freezer (pelanggaran izin, keamanan pangan, waste). *(Baru 18 Nov 2026; R-O11..R-O13 dicadangkan G43.)* | 3 | 4 | 12 | Satu fungsi pengecekan dipakai usulan **dan** konfirmasi picking DC → outlet; alasan penolakan tercatat per baris usulan; override hanya *Assortment Manager* dengan alasan teraudit dan **tidak tersedia** untuk alkohol/non-halal (Q-SM22); laporan pelanggaran & "produk tidak pernah diusulkan" harian; test per aturan; review master data bulanan oleh Category manager; izin alkohol bermasa berlaku (fit-gap Q-SM13). Pemilik: Category manager + efn-odoo-engineer. Terkait R-O01, R-L07. |

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

### 5.12 Sistem lama ESB Core & migrasi (G41, 17 Nov 2026)
Menindaklanjuti Q5 dan R-O02: sistem lama kelima brand F&B = **ESB Core + ESB POS** (ADR-040). Rincian, konteks, dan
default sementara ada di `docs/04` §25.9; nomor mengikuti pola per gelombang (Q-WTZ, Q-PAY).
- **Q-ESB1** User API **read-only** per brand di ESB (tanpa hak tulis)? Sampai ada, production `ESB_ENABLED=false` (R-S17).
- ~~**Q-ESB2** Brand "WP" di ESB = **Wetzel's Pretzels** (company `WTZ`)?~~ **Terjawab (18 Nov 2026): ya, `WTZ`.**
- **Q-ESB3** Pemetaan setiap cabang **Bacha** ke `BCR` (butik ritel) atau `BCF` (kafe)?
- **Q-ESB4** **11 cabang PB** tanpa data produk FIN di laporan resep ESB: memang tidak menjual produk FIN atau resep belum lengkap?
- **Q-ESB5** Nama PT, NPWP, dan jenis pajak **Im Donut** (`IMD`) dan **Curry Up** (`CUP`)?
- **Q-ESB6** **Tanggal cutover** ESB → Odoo per brand?
- **Q-ESB7** **Izin beban backfill 24 bulan** ke server ESB (2 req/dtk per brand, malam 22:00–05:00 WIB, satu brand per waktu)? (R-O10)
- **Q-ESB8** Daftar cabang **dapur pusat (CK)** per brand selain PB?
- **Q-ESB9** Hak EFN atas data & penggunaan API massal menurut kontrak ESB; tersedia ekspor resmi yang lebih ringan?

**Migrasi ESB → Odoo (G43, ADR-042 Accepted 18 Nov 2026).** Rincian & default di `docs/04` §27.12. Risiko terkait
R-O12, R-O13, R-L12 (§4).
- **Q-ESB10** (Finance/Ops) Cabang Bacha "B2B Consignment" masuk **BCR** atau **BCF**? Barang konsinyasi tetap milik Bacha sampai laporan penjualan mitra?
- **Q-ESB11** (Ops/Finance) CK di akun WTZ **dan** CUP = satu dapur fisik bersama? PT pemiliknya? (default: dua gudang, tanpa inter-company)
- **Q-ESB12** (Ops per brand) Komposisi paket menu (isi tetap vs pilihan)? Kandidat dikirim dari pola penjualan.
- **Q-ESB13** (Ops) Warehouse/Storage = situs terpisah atau ruang di CK/kantor? Stok cabang Damage/Expired/Temporary dimigrasi (karantina) atau dihapusbukukan sebelum cutover?
- **Q-ESB14** (Ops) Produk FIN buatan toko tetap diproduksi di toko (MO outlet) atau dipindah ke CK?
- **Q-ESB15** (Ops/Finance) Harga menu sama di semua cabang atau per cabang/kota? Harga takeaway = dine-in?
- **Q-ESB16** (Finance) Metode biaya per kategori (Standard/AVCO/FIFO), valuasi otomatis/manual, akun "Saldo Awal – Kliring". (R-O12)
- **Q-ESB17** (CK/QC) Qty komponen BOM ESB bruto (sudah termasuk yield) atau neto? Perlu bahan pengganti?
- **Q-ESB18** (Finance) Laba-rugi per outlet (analitik per cabang) sejak cutover?
- **Q-ESB19** (Marketing/DPO) Data member & poin saat ini di mana (ESB, Eraspace, lain)? Ada bukti consent?
- ~~**Q-ESB20** (Manajemen) Histori penjualan cukup di Insight atau wajib di dalam Odoo?~~ **Terjawab 18 Nov 2026:** wajib di Odoo, rinci bila mungkin → arsip `efn.legacy.sales.daily` (24 bulan) + `efn.legacy.sales.line` (per baris, parameter `efn_mig_esb.sales_line_months` default 24), bukan transaksi Odoo (ADR-042 amendemen A1).
- **Q-ESB21** (Finance/IT) TB dan daftar faktur piutang B2B terbuka lewat API ESB, atau ekspor resmi Finance per tanggal cutover?
- **Q-ESB22** (Ops) PO, transfer, delivery yang masih berjalan saat cutover: diselesaikan di ESB sebelum freeze (rekomendasi) atau dibuat ulang di Odoo?
- ~~**Q-ESB23** (PO) Urutan cutover per brand; IMD/CUP boleh migrasi master sebelum Q-ESB5?~~ **Terjawab 18 Nov 2026:** pilot = **Paris Baguette (PB)**; brand supermarket di luar G43. Sisa (urutan setelah PB, master IMD/CUP sebelum Q-ESB5) mengikuti default ADR-042 §3.6 sampai PO menyatakan lain.
- **Q-ESB24** (Finance/DPO) Penandatangan berita acara rekonsiliasi per brand (usulan Finance + Ops brand + PO) dan retensi bundel migrasi **≤ 90 hari setelah berita acara** diterima? (R-L12)

**Penjualan level struk ESB OMS (G69, ADR-040 §6b Proposed, 25 Nov 2026).** Rincian di `docs/04` §33.9. Q-ESB1 kini juga
mencakup OMS. Risiko terkait R-S17, R-O09, R-O10, R-L01.
- **Q-OMS1** (IT klien / PIC ESB) Token statis `core-api.esb.co.id` untuk material usage & member — di luar G69.
- **Q-OMS2** (IT klien / vendor ESB) Izin beban intraday OMS (hari berjalan tiap 15 menit 07–23 WIB, PB)? Sampai ada, production `ESB_OMS_ENABLED=false`. (R-O10)
- **Q-OMS3** (DPO/Finance) Retensi data struk: raw 36 bulan, mart 10 tahun (usulan, sama G41)?

### 5.13 VPS Bima Creative untuk dev & ekstraksi ESB (ADR-043, 18 Nov 2026)
Rincian di ADR-043 §6. Tahap B (ekstraksi ESB di VPS) diblokir sampai Q-VPS1..Q-VPS4 + Q-ESB1/7/9 terjawab. Usulan
risiko baru (belum masuk tabel §4): **R-S19** pihak ketiga memegang kredensial & data ESB, **R-S20** agen AI/akun dev
membaca data/secret klien di host bersama, **R-O11** ketergantungan & penghapusan data di VPS konsultan.
- **Q-VPS1** Penyedia VPS, negara & kota data center, sub-pemroses, sertifikasi.
- **Q-VPS2** Spesifikasi (vCPU/RAM/disk/AES-NI), dukungan LUKS/volume terenkripsi, VM terpisah, snapshot otomatis penyedia.
- **Q-VPS3** Daftar pemegang root/sudo & akses panel penyedia (per orang), MFA, pemegang passphrase LUKS.
- **Q-VPS4** Persetujuan tertulis klien + DPO dan DPA; pengendali penandatangan (Holding atau tiap PT brand).
- **Q-VPS5** VPS B atas nama Bima Creative atau atas nama EFN/klien; siapa menanggung sewa.
- **Q-VPS6** Batas & bukti penghapusan data di VPS (usulan: paling awal dari pindah ke `efn-data` / akhir kontrak / cutover terakhir + 30 hari / permintaan klien).
- **Q-VPS7** `insight-web` di VPS untuk tim proyek: boleh? siapa penggunanya?
- **Q-VPS8** Apakah VPS sama dengan host gateway 9router (rekomendasi: tidak satu host dengan B)?
- **Q-VPS9** Backup B: tujuan off-site & kustodian kunci, atau terima "tanpa backup, bangun ulang dari ESB".
### 5.14 Supermarket (Gelombang 0, 18 Nov 2026)
Menindaklanjuti keputusan pemilik proyek 18 Nov 2026 (ADR-044..048, revisi ADR-041). §5.13 dicadangkan untuk G43
(branch terpisah). Q-SM1..Q-SM13 milik fit-gap `docs/fit-gap/G45_SUPERMARKET.md` §7 (data outlet, timbangan GS1,
markdown, **pajak Q-SM5**, konsinyasi, kiosk, cycle count, jadwal kirim, **izin alkohol Q-SM13**); rincian & default
pertanyaan architect di `docs/04` §28.12.
- **Legal:** **Q-SM14** penerimaan risiko tertulis WhatsApp tidak resmi (bersama pemilik proyek; R-B16) · **Q-SM20** KBLI &
  pendaftaran gudang DC (R-L14) · **Q-SM21** kebijakan produk non-halal per outlet.
- **DPO:** **Q-SM16** dasar tracking & teks banner (R-L13) · **Q-SM17** retensi event & ambang anonim agregat ·
  **Q-SM18** transfer nomor + isi promo ke WhatsApp tanpa DPA · **Q-SM25** consent untuk notify-me/price alert via
  email/WA · **Q-SM26** profiling & hak keberatan.
- **Konsultan pajak:** fit-gap **Q-SM5** + tambahan architect: PPN perpindahan DC → outlet dalam satu PT dan NITKU per
  lokasi (PMK-81/2024 + perubahan); konfirmasi bahwa PER-07/PJ/2021 bukan dasar pemusatan (R-L14).
- **IT klien / Ops / Marketing / Finance:** **Q-SM15** nomor WA terpisah · **Q-SM19** penyedia SMTP · **Q-SM22** override
  restriksi · **Q-SM23** usulan lewat cut-off · **Q-SM24** cap kampanye · **Q-SM27** histori & target akurasi (R-T21) ·
  **Q-SM28** ambang near-expiry & service level · **Q-SM29** persona Insight.

### 5.15 Production planning di atas ESB (G66, ADR-051 Proposed, 24 Nov 2026)
Rincian di ADR-051 §5 dan `docs/04` §30. Tulis ke ESB **production terkunci di kode** sampai Q-PP1 + Q-PP2 terjawab.
Usulan risiko baru (belum masuk tabel §4):
- **R-S23** Kredensial/akun tulis ESB disalahgunakan atau menulis ke environment yang salah (production saat uji).
  Mitigasi: allowlist host + method/path di kode + test, `PROD_WRITE_UNLOCKED=False`, banner environment di UI, akun
  non-admin (Q-PP1), secret terpisah dari `esb-extract`, subnet egress sendiri. Terkait R-S17.
- **R-O15** Dokumen produksi yang dibuat lewat API **langsung Authorized** di ESB (uji staging 24 Nov 2026) sehingga
  stok/HPP salah bila rencana keliru. Mitigasi: SoD dua orang di aplikasi, ringkasan dampak sebelum posting, posting
  eksplisit, pembatalan = DELETE dokumen, uji staging dulu, batas dokumen per posting.
- **R-T23** Rencana dihitung dari stok/BOM basi atau tidak lengkap (tanpa netting WIP, tanpa FEFO, tanpa transfer
  tertunda). Mitigasi: stok disegarkan di setiap hitung + umur maksimum saat submit/posting, peringatan per baris,
  keterbatasan tampil di UI. Terkait R-T06.

Pertanyaan terbuka klien **Q-PP1..Q-PP14**: `docs/04` §30.14 + §30.16 (paling menentukan: Q-PP1 akun API non-admin, Q-PP2
penerimaan "produksi tanpa tahap draft", Q-PP3 mode costing PB production, Q-PP4 sumber pesanan outlet).

**Keputusan pemilik proyek 25 Nov 2026 (ADR-051 amandemen A1 §8, `docs/04` §30.17):**
- ~~**Q-PP1** akun API non-admin~~ **Diganti:** login per pengguna ke ESB (token pengguna untuk semua baca/tulis; akun
  service dihapus). Syarat membuka tulis production kini = akun ESB **perorangan non-admin** per planner/kepala gudang + PR
  terpisah (Q-PP17); semua POST/DELETE akun admin ke production ditahan.
- ~~**Q-PP2** produksi tanpa draft~~ **Diterima:** gerbang = SoD dua orang di aplikasi.
- ~~**Q-PP6** daftar lokasi sumber~~ **Terjawab:** mengikuti izin cabang pengguna di ESB (`/branch/list/user`), tanpa
  daftar terpisah.
- Login ESB yang berhasil diduplikasi ke Odoo sebagai shadow user diarsipkan (rancangan fit-gap G66b; Q-ESBU1..Q-ESBU6).

Usulan risiko baru (belum masuk tabel §4):
- **R-S24** Perantara kredensial: password ESB pengguna terlihat sesaat oleh insight-web & prodplan. Mitigasi: TLS semua hop,
  tidak pernah disimpan/dicatat (test), nonce di body login, batas laju per IP + lockout per username, review security.
- **R-S25** Token ESB pengguna (ESB terjangkau internet) bocor dari prodplan. Mitigasi: hanya memori, ≤ 1 jam, tidak ke
  browser/PG/log, cookie buram + hash di server, logout ESB, tanpa core dump.
- **R-S26** Provisioning JIT membuat akun Odoo yang disalahgunakan. Mitigasi: user diarsipkan tanpa password/email/grup PP,
  aktivasi manual Superadmin, bot non-admin satu method lewat L7 gateway, flag default `false`.
- **R-O16** Token ESB 1 jam tanpa refresh → posting/job terhenti. Mitigasi: `needs_login` tanpa POST ganda, "Lanjutkan
  posting", cek sisa umur token sebelum tulis, alert (Q-PP15).
- **R-T24** Asumsi "data tersaring izin ESB" salah atau cache mencampur data antar-pengguna. Mitigasi: pembatasan cabang di
  aplikasi, cache per aktor, uji akun non-admin sebelum production (Q-PP16).

Pertanyaan baru **Q-PP15..Q-PP19** (`docs/04` §30.17.14): refresh token ESB, penyaringan per pengguna, akun perorangan
(staging minimal dua akun non-admin untuk uji SoD), lockout ESB, arti `authorized`/`userRoleID` admin.

---

## 6. ARSITEKTUR TARGET (KONSOLIDASI)

Satu versi yang merekonsiliasi keenam dokumen. Detail visual ada di artifact arsitektur dan `docs/02_TARGET_ARCHITECTURE.md`.

### 6.1 Prinsip
1. **Satu Odoo CE 19, multi-company** sebagai system of record untuk produk, stok, akuntansi, POS.
2. **NestJS BFF** sebagai satu-satunya pintu untuk Super-App/brand apps; Odoo tidak pernah diakses langsung dari internet.
3. **Asynchronous by default** untuk OCR, notifikasi, MGM, webhook 3PL/payment (RabbitMQ).
4. **PostgreSQL adalah source of truth, ClickHouse adalah turunan.** Tidak ada tulisan langsung dari API ke DWH. *(Pengecualian terbatas 17 Nov 2026, ADR-040 Proposed: job ekstraksi `esb-extract` menulis data sistem eksternal ESB ke `efn_esb`/`efn_dwh.*esb*`; bukan data transaksi EFN.)*
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
