# Security & Compliance Review — Kanal online SMD + situs `supermarket` (23 Nov 2026)

- **Lingkup:** perubahan belum di-commit di worktree `efn-platform-smweb`, branch `feat/supermarket-web` (basis `origin/main` c5ac674). Keputusan user 23 Nov 2026: SMD dapat kanal online (pickup + pre-order) tanpa QR meja/reservasi, plus situs tenant `supermarket`.
- **Metode:** review statis kode/konfigurasi saja (reviewer tanpa shell). Hasil test yang disebut (vitest 612, Jest BFF, deploy 426/426, rotate-key 152/152, Odoo 8/8, `compose config`) adalah laporan pemanggil dan **tidak dijalankan ulang** oleh reviewer.
- **Reviewer:** efn-security-reviewer (read-only).

## Ringkasan eksekutif

Tidak ada temuan Critical atau High. Kontrol inti sudah benar dan konsisten dengan pola kanal WTZ: user teknis `efn_online_smd` hanya punya satu company dan satu grup kanal, tanpa API key di data. Secret hanya di-mount lewat overlay bila `SMD` ada di `ONLINE_CHANNELS`, dan kanal mati secara default. SMD ditolak di `FNB_QR_COMPANIES`/`BOOKING_COMPANIES`, dan katalog mock terisolasi per kanal. Temuan terpenting ada di sisi **kepatuhan**, bukan keamanan teknis: produk non-halal ikut dipublikasikan ke kanal online tanpa penanda "tidak halal", dan kanal online tidak menegakkan capability outlet (SEC-01, Medium). Sisanya Low/Info: faktur PPN SMD yang di-hard-code sebelum status pajak final, mock yang berbeda dari data Odoo, dan amplifikasi panggilan katalog di landing.

## Tabel temuan

| ID | Severity | Lokasi | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-01 | Medium | `scripts/dev/seed_demo_supermarket.py:2537`, `:2560-2565`, `:246-249`; `services/odoo/addons/efn_online_order/**` (tidak ada referensi assortment/capability) | Produk babi (`non_halal_allowed`) dipublikasikan ke kanal online SMD tanpa label "tidak halal". Alkohol hanya dikecualikan di skrip seed, tanpa pagar di Odoo. Quote/stok online tidak memeriksa capability outlet. | R-L07 |
| SEC-02 | Low | `services/api-gateway/src/orders/invoice-request.ts:20`; `efn_setup_supermarket_online/demo/efn_online_smd_demo.xml:6-7` | Faktur atas nama (NIK/NPWP) untuk PT SMD di-hard-code aktif, padahal status PKP/nama badan hukum (Q-SM1) dan pemetaan PPN sembako (Q-SM5) masih placeholder | R-L03, R-L01 |
| SEC-03 | Low | `services/api-gateway/src/odoo-rpc/odoo-online.mock.ts:116`, `:120-126`, `:148-161`; `smd-channel.spec.ts:82` | Mock SMD berbeda dari Odoo: `bank_transfer` vs `qris,va,ewallet`, 3 vs 5 outlet, 10 vs 13 kategori. Test memberi jaminan atas perilaku yang tidak ada di Odoo. | R-S11 (asurans) |
| SEC-04 | Low | `services/superapp-web/app/page.tsx:31-49`; `services/api-gateway/src/config/app-config.ts:2581` | Satu tampilan landing = ±10 panggilan katalog BFF per IP pengunjung. Batas `CATALOG` 120/60 s per IP → pengguna di balik CGNAT cepat mendapat landing tanpa data. | — (ketersediaan) |
| SEC-05 | Info | `efn_setup_supermarket_online/data/pos_category_data.xml:7-13` | `pos.category` CE tanpa `company_id`, sehingga 13 kategori SMD terlihat di POS brand lain (sudah didokumentasikan) | R-T14 (UX) |
| SEC-06 | Info | `components/supermarket/SupermarketLanding.tsx:152-165`; `app/belanja/page.tsx:140,147` | Kueri pencarian via GET (`/belanja?q=`) masuk access log Traefik/Loki dan `SearchTracker`. Isinya bisa berupa PII yang diketik pengguna. Pola ini sudah ada sebelumnya. | R-L01 |

---

## Detail temuan

### SEC-01 — Medium — Produk non-halal & alkohol: tanpa label dan tanpa pagar di kanal online

**Bukti (diverifikasi):**
- `seed_demo_supermarket.py:235-249`: kategori `imported` memuat "Sosis Babi Impor" dan "Bacon Babi Asap Impor" dengan capability wajib `non_halal_allowed`.
- `seed_demo_supermarket.py:2537`: `ONLINE_EXCLUDED_KINDS = ("alcohol",)`. Hanya alkohol yang dikecualikan. Produk `imported` (termasuk babi) di-link ke `channel_smd` (`:2563-2565`) dan diberi kategori online "Impor Premium".
- Grep `assortment|capabilit` di `efn_online_*/**/*.py` = **0 hasil**. Katalog, quote, dan place kanal online tidak memeriksa `efn_required_capability_ids` terhadap capability gudang outlet. Pemeriksaan itu hanya ada di `efn_assortment` (transfer/POS).
- Pengecualian alkohol hanya ada di skrip seed dev. Tidak ada constraint Odoo yang mencegah Ops mem-publish produk alkohol ke kanal online secara manual. Komentar di skrip sendiri menyatakan publikasi manual "tidak dicabut".
- Katalog/landing tidak punya field atau label "tidak halal". Produk babi tampil di rak "Pilihan minggu ini" atau lorong "Impor Premium" bercampur dengan produk lain (`page.tsx:73-77`).

**Skenario:** pelanggan membuka `supermarket.<domain>`. Rak landing atau lorong "Impor Premium" menampilkan "Sosis Babi Impor" dengan tampilan yang sama seperti produk lain, tanpa keterangan tidak halal. Ops mem-publish "Bir Lager" ke kanal SMD, dan produk itu bisa dipesan tanpa verifikasi usia untuk diambil di outlet. Mitigasi parsial: `stock_check=free_qty` membuat quote `out_of_stock` di outlet yang tidak pernah menerima stok produk tersebut, karena transfer diblokir `efn_assortment`. Jadi pemesanan di outlet tanpa izin **kemungkinan besar** tertahan. Status: PLAUSIBLE, belum diverifikasi di DB. Masalah label dan pagar alkohol tetap ada.

**Dampak kepatuhan:** UU 33/2014 jo. PP 39/2021 (Jaminan Produk Halal) mewajibkan keterangan "tidak halal" pada produk berbahan tidak halal yang diperdagangkan, termasuk penyajian di kanal penjualan. Penjualan minuman beralkohol tunduk pada pengendalian Permendag (20/M-DAG/PER/4/2014 dan perubahannya): izin outlet dan batas usia 21+. Ketentuan kanal online perlu dikonfirmasi Legal.

**Rekomendasi:**
1. Seed: tambahkan pengecualian berbasis capability, bukan hanya `kind`. Jangan publish produk yang `efn_required_capability_ids` memuat `alcohol_license` atau `non_halal_allowed` sampai label tersedia.
2. Odoo (`efn_online_order`, lewat fit-gap `efn-odoo-functional`): tambahkan constraint pada `product.template.efn_online_channel_ids` yang menolak produk dengan capability terlindungi (`PROTECTED_CAPABILITIES` di `efn_assortment`) untuk kanal online, kecuali flag eksplisit per kanal yang disetujui Legal. Sebagai alternatif, periksa capability gudang outlet di quote (`not_available` per outlet).
3. Bila produk non-halal tetap dijual online: kirim penanda `non_halal: true` di katalog (§10.3.2) dan tampilkan label "Tidak halal" di kartu/detail produk. Pisahkan juga lorongnya. Catat sebagai pertanyaan Legal (Q-SM baru).

### SEC-02 — Low — Faktur PPN atas nama untuk PT SMD aktif sebelum status pajak final

**Bukti:** `invoice-request.ts:20` `INVOICE_COMPANIES = ['GL', 'BCR', 'SMD']` adalah konstanta tanpa saklar per-PT. Komentar demo `efn_online_smd_demo.xml:6-7` menyebut PPN dari `l10n_id` dengan "tarif PLACEHOLDER Q-SM5". Produk seed mewarisi `taxes_id` default company (`seed_demo_supermarket.py:1362`), termasuk sayur, buah, daging, dan beras.

**Skenario:** di pilot, pelanggan meminta faktur atas nama dan mengirim NIK atau NPWP. BFF meneruskannya ke Odoo PT SMD, yang nama badan hukum dan status PKP-nya masih placeholder (Q-SM1). Faktur diterbitkan dengan PPN 11% atas barang kebutuhan pokok yang dibebaskan PPN (PP 49/2022). Akibatnya pelanggan membayar pajak yang tidak terutang (harga incl. lebih tinggi) dan dokumen pajak salah. NIK juga terkumpul untuk entitas yang belum final. Ini bertentangan dengan prinsip minimisasi UU PDP.

**Rekomendasi:** sebelum `SMD` masuk `ONLINE_CHANNELS` di env pilot, pastikan Q-SM1 (PKP, nama badan hukum) dan Q-SM5 (pemetaan PPN/bebas PPN per kategori) terjawab. Alternatifnya, jadikan `INVOICE_COMPANIES` dapat dikonfigurasi (`ONLINE_INVOICE_COMPANIES`, default tanpa SMD) dan nyalakan setelah Finance setuju. Odoo tetap menjadi guard kedua (`efn_pii` + `efn_business_type`).

### SEC-03 — Low — Mock SMD tidak mencerminkan data Odoo

**Bukti:**
- Mock: `MOCK_PAYMENT_MODES.SMD = ['qris', 'bank_transfer']`, 3 outlet `SMD01..SMD03`, 10 kategori.
- Odoo demo: `payment_modes = qris,va,ewallet`, 5 outlet, 13 kategori.
- `parsePaymentModes` (`odoo-online.parse.ts:203`) hanya mengenal `qris|counter|bank_transfer`. Kanal SMD nyata akan menawarkan **QRIS saja** (`va`/`ewallet` dibuang secara fail-closed, perilaku yang benar). Sementara itu `smd-channel.spec.ts:82` menguji `bank_transfer` yang di Odoo memerlukan `efn_online_transfer`, rekening PT, dan Verifikator Pembayaran (SoD, K25-07), dan semuanya belum dipasang.

**Dampak:** tidak ada celah langsung, tetapi test hijau memberi jaminan palsu soal jalur bayar SMD. Demo dengan mock juga menampilkan transfer bank yang tidak tersedia di Odoo.

**Rekomendasi:** samakan mock dengan data Odoo: `['qris']` (atau tambahkan dukungan `va`/`ewallet` di BFF lewat kontrak §12.2 terlebih dahulu), 5 outlet, dan 13 kategori. Ubah ekspektasi spec sesuai perubahan itu.

### SEC-04 — Low — Amplifikasi panggilan katalog di landing supermarket

**Bukti:** `SupermarketHome` (`page.tsx:31-49`) memanggil `channels`, `categories`, 6× `products` (limit 50), dan 1× `products` umum, jadi ±9–10 request per render. `catalog-server.ts:3-4` meneruskan `x-forwarded-for` pengunjung, sehingga semuanya dihitung ke kuota IP pengunjung `CATALOG: [120, 60]` (`app-config.ts:2581`).

**Skenario:** di jaringan seluler ber-CGNAT (umum di Indonesia), ratusan pengguna berbagi satu IP publik. Sekitar 12 tampilan landing per menit dari IP itu sudah menghabiskan kuota. Landing kemudian tampil tanpa harga, foto, atau toko (degradasi diam-diam), dan halaman `/belanja` ikut kena 429 untuk semua pengguna di IP itu.

**Rekomendasi:** gunakan satu endpoint agregat (mis. produk per kategori dalam satu panggilan) atau cache server-side 60 s untuk data katalog publik yang tidak bergantung pada pengguna (fetch tanpa XFF, dengan kuota service terpisah). Cara lain: kurangi `limit` per lorong (perlu ≤ 2 produk + hitungan) dan pakai field hitungan dari BFF.

### SEC-05 — Info — Kategori POS SMD global

`pos.category` CE 19 tanpa `company_id`, sehingga nama kategori SMD terlihat di POS brand lain yang tidak memakai `limit_categories`. Data ini tidak sensitif dan sudah didokumentasikan di XML/README beserta mitigasinya. Tidak perlu tindakan selain mengikuti saran README.

### SEC-06 — Info — Kueri pencarian di URL

Form landing adalah `GET /belanja?q=` (React meng-escape output, sehingga **tidak ada XSS**: `belanja/page.tsx:103,139` merender `{q}` sebagai teks, dan `sanitizeQuery` membuang karakter kontrol serta memotong panjangnya). Kueri tetap masuk access log Traefik/Loki dan `SearchTracker`. Pastikan kebijakan retensi log mencakup query string, atau mask parameter `q` di access log, dan `SearchTracker` tetap di bawah flag `BEHAVIOR_TRACKING_ENABLED` (OFF di production).

---

## Kontrol yang sudah benar (terverifikasi)

- **Least privilege user teknis:** `efn_online_smd` memakai `company_ids` = hanya `company_smd`, `group_ids` = `base.group_user` + `group_online_channel`, tanpa password dan tanpa API key di XML (`efn_online_smd_demo.xml:39-45`). Constraint model `_check_efn_users` (`efn_online_channel.py:378-414`) menegakkan satu company dan allowlist grup. Test `test_technical_user_single_company_channel_group` memeriksa `api_key_ids` kosong. Akses RPC mewajibkan user ∈ `channel.user_ids` dan company kanal ∈ `user.company_ids` (`:455`).
- **Data baru multi-company:** jurnal, pricelist, partner, tim, kalender, dan kanal semuanya ber-`company_id` SMD. Outlet mengikuti kanal. Tidak ada model baru, sehingga tidak perlu record rule atau ACL baru. Partner generik tanpa HP/email/NPWP (UU PDP) dan diuji.
- **Independensi SMD:** tidak bergantung pada `efn_setup_online` maupun modul ESB (diuji transitif). Semua xmlid berasal dari modul SMD.
- **Secret:** `odoo_api_key_online_smd` hanya di overlay `docker-compose.online-smd.yml` → `/run/secrets`. `deploy.sh:645-652` hanya menyertakan overlay bila SMD ada di `ONLINE_CHANNELS`. `secrets.list` = `host:disabled`. Env literal dan placeholder `disabled` ditolak saat start (spec). `check-encryption.sh:266-274` memastikan secret hanya di-mount ke BFF dan path env cocok. CI memuat semua overlay `online-*`.
- **Kanal default mati:** `ONLINE_CHANNELS` default `GL`, `ONLINE_ENABLED` default `false`. Allowlist `EFN_ONLINE_CHANNEL_CODES` (deploy-validate) dan `ALLOWED_CHANNELS` (rotate-key) disinkronkan.
- **SMD tidak bisa masuk QR meja/reservasi:** `FNB_COMPANY_CODES = ['PB','BCF']`. SMD di `FNB_QR_COMPANIES`/`BOOKING_COMPANIES` → `ConfigError`, juga saat fitur mati (spec `smd-channel.spec.ts:45-53`). Rotate-key tidak punya `QR-SMD`/`BOOKING-SMD`/`PROMO-SMD`.
- **Isolasi mock:** `productsOf`/`outletsOf` diterapkan di catalog, stock, image, dan quote. Produk/outlet kanal lain → `not_found` (diuji dua arah).
- **K25-07:** Odoo `payment_modes` tanpa `counter`, diuji. Mode tak dikenal dibuang fail-closed oleh BFF.
- **Rotate-key 8→16:** aman. Regex `{0,15}` membatasi panjang, setiap kode wajib ada di `ALLOWED_CHANNELS` (11 entri) dan unik, sehingga daftar efektif ≤ 11. Tidak ada jalur injeksi atau traversal baru. Precheck tetap menolak user ber-grup admin dan user yang dipakai bersama.
- **Frontend:** tanpa `dangerouslySetInnerHTML`. Semua data katalog dirender lewat JSX. `img src` hanya dari `imageUrl()` (sha tervalidasi, same-origin). Nilai `style` (tone/tema) berupa konstanta kode, dan tema lewat `themeToCssVars` ber-regex. CSS supermarket tanpa aset eksternal: dua `url("data:image/svg+xml,…")` diizinkan CSP `img-src 'self' data: blob:` (`next.config.mjs:16`), font self-host (`font-src 'self'`), `form-action 'self'` cocok dengan form GET `/belanja`. Holding `app.` tidak menampilkan SMD (`channelsForBrand` + `companies` tanpa SMD).
- **Alkohol** tidak dipublikasikan oleh seed (lihat SEC-01 untuk pagar yang belum ada).

## Belum diverifikasi

- Perilaku runtime Odoo (`free_qty` di outlet tanpa capability), render landing di browser (CSP violation report), dan hasil test yang disebut pemanggil tidak dijalankan ulang oleh reviewer.
- Isi runbook `odoo-online-api-key.md` dan diff `docs/04` v21.3 hanya dibaca sekilas melalui referensi, tidak direview baris per baris.
