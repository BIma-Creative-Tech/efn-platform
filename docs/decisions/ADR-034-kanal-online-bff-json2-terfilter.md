# ADR-034: Kanal Penjualan Online lewat BFF + `/json/2` Terfilter (bukan `website_sale`)

- **Status:** Proposed
  - **Catatan keputusan pemilik proyek 10 Okt 2026 (status tetap Proposed; tanda tangan arsitektur IT + Security + PO belum):** jawaban Q-ON1..Q-ON13 tercatat di `docs/decisions/README.md` (sesi tanya-jawab). Yang **mengubah desain** ADR ini: reservasi stok 1 jam (Q-ON4 → `stock_hold_mode`, `ONLINE_PAYMENT_TIMEOUT_SECONDS=3600`, Odoo `payment_timeout_minutes` ≥ 70), presisi IDR global Rp1 (Q-ON6), F&B online via SO + jembatan KDS (Q-ON2/E-29a), delivery pickup + 3PL + kurir internal (Q-ON3), faktur atas nama/NPWP pembeli opsional (Q-ON7 → PII masuk Odoo, prinsip §10.1 butir 4 direvisi), promo standar `sale_loyalty` di quote (Q-ON10), katalog & pricelist **per brand** (Q-ON12), poin online earn + redeem sebagai alat bayar, dan **API key kanal 30 hari + rotasi otomatis** (R-T15 berubah dari PLACEHOLDER 90 h). Paket kerja: `docs/10_RENCANA_GELOMBANG_18_PLUS.md`.
- **Tanggal:** 2026-10-09
- **Pemutus (klien):** IT + Security (arsitektur & transport); PO (lingkup pilot); Finance + Tax untuk bagian yang menyentuh ADR-019 (dana titipan)
- **Referensi review:** R-T10 (saga, skor 16), R-L03 (PB1 vs PPN), R-O05 (refund parsial), R-S03 (Odoo terekspos), R-S09 (webhook), R-T02; temuan #36–#45 `docs/08_ODOO_FIT_GAP.md` §8.2; SEC-58 (permukaan RPC)
- **Terkait:** ADR-001, ADR-002, ADR-016, ADR-019 (lihat catatan revisi di sana), ADR-020, ADR-022, ADR-023, ADR-024, ADR-026, ADR-027, ADR-029, ADR-031, ADR-032 (jalur F&B online). Kontrak: `docs/04_SERVICE_CONTRACTS.md` §10. Fit-gap: `docs/08_ODOO_FIT_GAP.md` §8 (E-28).

> **Ringkasan manajemen.** Super-App akan menjual barang PT Retail secara online (pilot: ambil di 1 toko, bayar sekali). Odoo tetap menjadi sumber harga, pajak, stok, dan dokumen penjualan, tetapi **pelanggan tidak pernah berhubungan langsung dengan Odoo**: aplikasi web memanggil BFF, dan BFF memanggil **10 fungsi khusus** di Odoo lewat jalur internal yang hanya meloloskan fungsi itu. Toko online bawaan Odoo (`website_sale`) **tidak dipakai** karena harus membuka Odoo ke internet (dilarang ADR-023) dan tidak mendukung satu keranjang lintas PT. Biaya: ±4–5 minggu engineer Odoo + ±3–4 minggu BFF + ±2–3 minggu frontend + ±1–1,5 minggu DevSecOps + ±1 minggu QA (sebagian paralel; ±6–7 minggu kalender). Software Rp0.

## Konteks
- Dokumen asli (`efn_fsd_detailed_flows.md`, FSD-1.1 di `project_documentation_suite_efn.md`) meminta *unified cart* dengan satu checkout untuk item beberapa PT, tanpa membahas pajak per PT, settlement, maupun cara Super-App membaca Odoo. `docs/02 §3.4` hanya menggambar alur tingkat tinggi.
- CE 19 punya `website_sale` (toko online Odoo) dan API eksternal. Pilihan di antara keduanya menentukan permukaan serangan Odoo, beban server, dan seberapa banyak logika harga/pajak yang berpindah ke BFF.
- Batasan yang sudah berlaku: Odoo hanya lewat WireGuard (ADR-023); Odoo sumber kebenaran harga & pajak, tanpa tarif di kode (ADR-027 §5); payment gateway-agnostic, provider `mock` sampai ADR-016 (ADR-026); satu pembayaran, SO & pajak per PT (ADR-019 opsi A); tanpa modul pihak ketiga (ADR-031); enkripsi + mTLS di setiap hop (ADR-022, docs/04 §8.5).
- Temuan verifikasi source 19.0 (docs/08 §8.2): `payment.transaction` jalur `sale` hanya mengonfirmasi bila **tepat satu SO per transaksi** (#40); `sale_loyalty` dapat mengubah total saat konfirmasi (#37); `sale` membawa route portal publik `/my/orders` (#38); API key `/json/2` default berlaku 1 hari (#42); `/json/2` = `auth='bearer'`, metode di path URL.

## Opsi yang dipertimbangkan

### 1. Di mana kanal penjualan dibangun

| Opsi | Kelebihan | Kekurangan | Biaya / waktu (asumsi) |
|---|---|---|---|
| **A. `website` + `website_sale` (+ `_stock`, `_collect`)** sebagai toko online Odoo, Next.js hanya tampilan atau tidak dipakai | Fitur katalog, keranjang, pickup bawaan; kode Odoo custom lebih sedikit (±2–3 minggu) | Route publik Odoo harus dibuka ke internet (**melanggar ADR-023**, R-S03 skor 20); keranjang website = satu company/website → **tidak bisa satu keranjang lintas PT**; pembayaran `payment.transaction` 1 SO per transaksi (#40); tema & login ganda (QWeb vs Next.js, OTP WA vs login Odoo); data pelanggan (nama/HP/alamat) masuk `res.partner` Odoo | Hemat ±2 minggu Odoo, tetapi butuh hardening & WAF Odoo publik, plus kerja ulang UX; **ditolak** |
| **B. BFF + metode khusus `efn.online.*` (E-28)** — rekomendasi | Odoo tetap privat; BFF tidak bisa mengirim harga/pajak/partner bebas (hanya metode tervalidasi, pola SEC-58); multi-PT = satu SO per PT (ADR-019); data pelanggan tetap di BFF (Odoo hanya partner generik, UU PDP); bisa diuji sebagai kontrak | Modul custom ±4–5 minggu (E-28); BFF harus mengorkestrasi saga; katalog perlu salinan di BFF | ±4–5 mgg Odoo + ±3–4 mgg BFF; Rp0 lisensi |
| C. BFF memanggil ORM generik (`sale.order.create/write`, `product.product.search_read`) lewat API eksternal | Tidak ada modul custom | BFF memegang hak tulis `sale.order` → dapat mengirim harga/pajak apa pun (melanggar ADR-027 §5, SEC-58); validasi tersebar di BFF; tidak idempoten; hak baca model umum terbuka | Paling cepat ±2 mgg; **ditolak** (keamanan) |
| D. Portal `sale` bawaan untuk pelanggan | — | Route publik berbasis `access_token` (#38); tidak ada katalog/keranjang | **Ditolak** |

### 2. Transport BFF → Odoo

| Opsi | Kelebihan | Kekurangan |
|---|---|---|
| XML-RPC `/xmlrpc/2/object` atau JSON-RPC `/jsonrpc` | Dikenal luas | Nama model & metode ada di **body** → router L7 tidak bisa membatasi metode tanpa mem-parse XML/JSON; menurut dokumentasi resmi Odoo 19 kedua endpoint ini **deprecated** dan dijadwalkan dihapus di versi berikutnya (**belum diverifikasi di source** pada gelombang ini); sudah diblok di Traefik (docs/04 §7.4, §8.7) |
| **`/json/2/<model>/<metode>` + API key per user teknis per PT** — rekomendasi | Model & metode di **path** → router internal dapat memakai *allowlist path persis* (10 path); autentikasi bearer standar (`res.users.apikeys`, scope `rpc`); masa berlaku key diatur grup (`api_key_duration`); `RPC_ALLOWLIST` & test SEC-58 yang sudah ada tetap berlaku | Key kedaluwarsa (default 1 hari bila grup tak diatur, #42) → wajib runbook rotasi & alert 401; `base.group_user` masih bisa membaca model umum bila path tidak difilter → filter wajib, bukan opsional |
| Controller HTTP custom `/efn/online/*` dengan HMAC sendiri | Permukaan paling sempit | Logika autentikasi dibangun sendiri (risiko bug), tanpa manajemen key standar; tetap butuh hardening yang sama |

Jaringan: BFF **tidak** diberi `--allow-cn` langsung ke sidecar `odoo-tls` (yang meneruskan seluruh HTTP Odoo). Ditambahkan **router internal `odoo-rpc-gateway`** (HAProxy, pola `s3-gateway` ADR-030): TLS + mTLS (hanya CN `api-gateway`), `POST` saja, allowlist 10 path persis setelah normalisasi (pelajaran SEC-41), header DB dipaksa, cookie dibuang, batas ukuran; lalu mTLS ke `odoo-tls` sebagai CN `odoo-rpc-gateway`.

### 3. Sinkronisasi katalog

| Opsi | Kelebihan | Kekurangan |
|---|---|---|
| Proxy langsung (setiap request katalog pelanggan → Odoo) | Selalu segar | Beban Odoo naik mengikuti trafik internet; Odoo lambat/mati = katalog mati; amplifikasi DoS ke ERP |
| **Pull delta periodik oleh BFF** (`efn_online_catalog(since, cursor)`, 5 menit) — rekomendasi pilot | Beban Odoo kecil & terkendali; katalog tetap tampil saat Odoo gangguan; sederhana | Harga tampil bisa tertinggal ≤ 5 menit → **dikompensasi quote**: harga yang ditagih selalu dari `efn_online_quote`/`place` (Odoo), bukan dari cache |
| Push (Odoo outbox `catalog.changed`) | Segar tanpa polling | Kode outbox tambahan; tetap perlu pull untuk rekonsiliasi → **fase 3b** sebagai *hint* yang memicu pull |

Stok tidak di-cache lama (≤ 30 detik), dicek ulang saat quote dan saat paid.

## Keputusan
1. **Opsi B**: kanal online = BFF + modul `efn_online_order` (E-28) dengan 10 metode RPC khusus; `website*`, `delivery`, dan jalur `payment.transaction` sale **tidak dipasang/dipakai**. Odoo = sumber kebenaran harga, pajak (fiscal position outlet pemenuhan, ADR-027), stok, SO, faktur, nota kredit. BFF hanya menjumlahkan `amount_payable` per SO menjadi satu payment intent dan **tidak pernah menghitung pajak**.
2. **Transport `/json/2`** dengan API key per user teknis per PT (secret `odoo_api_key_online_<channel>`), lewat **`odoo-rpc-gateway`** (mTLS, allowlist 10 path persis). Event balik Odoo → BFF lewat HTTPS ke BFF (`POST /v1/pos/online/order-events`, service key + HMAC body + replay window + idempotensi).
3. **Katalog = pull delta 5 menit** ke tabel BFF + gambar disalin ke object storage (`catalog/`), di-re-encode BFF; *push hint* fase 3b.
4. Kontrak rinci (API, payload, saga, tabel, queue, metrik, paket kerja) = `docs/04 §10` v5.0. Default pilot (semua configurable, docs/04 §10.1): PT Retail saja, pickup 1 toko, delivery & F&B online nonaktif, harga tampil termasuk pajak, tanpa reservasi stok, batas bayar 15 menit, faktur saat paid, promo hanya pricelist, provider `mock`, poin tidak dipakai/didapat di online, dana titipan ADR-019 **A1**.

## Konsekuensi
- **Positif:** Odoo tidak terekspos; harga/pajak tidak bisa dimanipulasi klien maupun BFF; satu keranjang lintas PT tetap mungkin tanpa *breaking change* (bagian per kanal, `channel_code` bebas seperti `FNB-<brand>`); data pribadi pelanggan tidak masuk Odoo; katalog tetap tampil saat Odoo gangguan; POS tidak terdampak bila kanal online dimatikan (`ONLINE_ENABLED=false`).
- **Negatif / utang teknis:** satu komponen jaringan baru (`odoo-rpc-gateway`) dan satu secret per PT dengan rotasi berkala (≤ 90 hari, PLACEHOLDER); saga terdistribusi (BFF ↔ Odoo ↔ gateway) butuh rekonsiliasi & alert *order stuck*; katalog di BFF adalah salinan (≤ 5 menit tertinggal); fitur toko online Odoo (ulasan, wishlist, SEO) tidak tersedia — dibangun di Next.js bila diminta.
- **Dampak dokumen:** `docs/02 §3.4` (diagram diganti), `docs/04 §10` (baru), ADR-019 (catatan revisi), `docs/08 §8` tetap sebagai fit-gap; delta E-28 terhadap docs/08 dicatat di docs/04 §10.4.4 (kontrak menang).

## Risiko terkait
R-T10 (saga — mitigasi: setiap langkah idempoten, outbox/inbox, rekonsiliasi `efn_online_status`), R-S03 (Odoo publik — dicegah), R-S09 (webhook — HMAC + replay + idempotensi), R-O05 (refund per SO, kumulatif ≤ porsi), R-L03 (pajak per PT dari FP outlet), R-L01 (PII minimal di Odoo), usulan **R-T15** (nomor berikut di register docs/01; R-T13 sudah dipakai ganda, R-T14 = multi-tenant brand) "API key kanal online kedaluwarsa → checkout berhenti" (mitigasi: alert 401 + runbook rotasi dua kunci).

## Pertanyaan terbuka untuk klien
Q-ON1..Q-ON13 (`docs/08 §8.7`; ringkas di `docs/decisions/README.md`). Yang paling memengaruhi ADR ini: **Q-ON2** (F&B online: SO + jembatan KDS vs `pos.order` lewat E-27 — bersama ADR-032), **Q-ON4** (reservasi stok & kebijakan stok kurang), **Q-ON9** (dana titipan: MDR, settlement, A1 manual vs A2 otomatis), **Q-ON12** (katalog per PT vs per brand). Untuk IT/Security: masa berlaku API key kanal (PLACEHOLDER 90 hari) dan siapa pemegang rotasi.

## Kapan ditinjau ulang
- Saat Q-ON2 dijawab (jalur F&B online) atau delivery (Q-ON3/Q-39/Q-40) masuk lingkup.
- Saat vendor gateway dipilih (ADR-016): bila mendukung split per sub-akun → ADR-019 opsi C, portions intent dipakai adapter.
- Setelah 100 order online pertama: rekonsiliasi Σ porsi = Σ faktur per PT 100% cocok, tidak ada order *stuck* > 15 menit tanpa alert.
- Bila Odoo menghapus/mengubah `/json/2` di versi berikutnya (cek saat upgrade).
