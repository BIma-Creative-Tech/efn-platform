# Transfer Data Pribadi Lintas Negara — Bahan Keputusan DPO/Legal

- **Status:** Bahan keputusan untuk **DPO + Legal**. **Belum diputuskan klien** (6 Okt 2026); default sementara di §5 dipakai sampai ada keputusan.
- **Tanggal:** 2026-10-06 · Penyusun: efn-architect
- **Terkait:** `PDP_DATA_INVENTORY.md` §2.6, ADR-011 (WhatsApp Cloud API), ADR-002 (Cloudflare di edge), ADR-026 (payment gateway-agnostic), ADR-028 (ALTCHA — menghindari transfer), ADR-004 (VPS di Indonesia), `docs/01` R-L01

> **Ringkasan manajemen.** Server EFN berada di Indonesia, tetapi beberapa layanan pihak ketiga memproses data pelanggan di luar negeri: **WhatsApp (Meta)** untuk OTP/notifikasi dan **Cloudflare** sebagai pelindung website. Payment gateway kelak juga perlu dinilai. **GitHub** hanya menyimpan kode, bukan data pelanggan. UU PDP membolehkan transfer lintas negara dengan syarat tertentu; dokumen ini merangkum data apa yang mengalir, dasar hukum yang mungkin, dan opsi pengurangannya, agar DPO/Legal dapat memutuskan. Sementara itu, desain sudah meminimalkan data yang dikirim.

## 1. Kerangka (rujukan kerja — **nomor pasal diverifikasi Legal**)
UU PDP mengatur transfer ke luar wilayah hukum Indonesia secara bertingkat (rujukan kerja: Pasal 56): (1) negara tujuan memiliki tingkat pelindungan setara atau lebih tinggi; bila tidak, (2) ada pelindungan yang memadai dan mengikat (mis. perjanjian/klausul kontrak, aturan korporat); bila tidak, (3) persetujuan subjek data. Peraturan pelaksana & daftar negara setara: **verifikasi status terkini oleh Legal**. Pengendali tetap bertanggung jawab atas pemroses.

## 2. Daftar pemroses & aliran data
| # | Pemroses | Fungsi | Data pribadi yang mengalir | Volume / frekuensi | Lokasi pemrosesan | Kontrol yang sudah ada |
|---|---|---|---|---|---|---|
| P1 | **Meta — WhatsApp Cloud API** (ADR-011) | OTP login & POS, notifikasi utilitas, (kelak) marketing | Nomor HP penerima, isi template (kode OTP, nama program, poin), metadata pengiriman | Setiap OTP/notifikasi (`docs/07` §D: 25–100 rb pesan/bulan) | Infrastruktur global Meta. **Opsi "Cloud API Local Storage"** (data at rest per nomor, mis. Indonesia; data in use hingga ±60 menit) — **sumber sekunder BSP (Clickatell, 360dialog, Infobip), verifikasi di dokumentasi resmi Meta** | Template minim (tanpa nama lengkap/alamat); nomor tidak pernah di log (mask); hanya Cloud API resmi |
| P2 | **Cloudflare** (DNS, proxy, WAF, TLS edge) | Lindungi `api.`/web publik dari DDoS & bot | **IP pengunjung, header, URL, dan isi request/response** (TLS diterminasi di Cloudflare → body seperti nomor HP saat request OTP, foto Price Hunter melewati CF dalam bentuk terdekripsi) | Setiap request publik | Jaringan edge global (PoP terdekat, termasuk Indonesia, tetapi tidak dijamin) | Odoo & alat internal **tidak** lewat CF (WireGuard, ADR-023); captcha ALTCHA self-hosted (ADR-028, menghindari Turnstile); origin hanya menerima dari CF (Full strict + AOP) |
| P3 | **Payment gateway** (vendor **belum ditentukan**, ADR-026) | QRIS/VA/e-wallet | `order_ref`, nominal; nama/HP/email **hanya bila vendor mewajibkan**; tidak ada data kartu | Setiap pembayaran online/QRIS POS | **Dinilai saat pemilihan vendor** (kriteria "lokasi data/DPA" di ADR-026) — vendor Indonesia berizin BI kemungkinan memproses di Indonesia, tetapi sub-pemroses cloud dapat di luar negeri | Abstraksi mengirim data minimum; secret di `/run/secrets` |
| P4 | **GitHub** (repo, Actions, Environments) | Kode, CI/CD, secret deploy | **Bukan data pelanggan.** Kode, data uji sintetis (nomor fiktif), secret deploy (terenkripsi GitHub) | — | AS/global | Larangan data produksi di fixture/log CI; gitleaks; secret hanya di Environments (kustodi Superadmin, ADR-022). Catatan: secret = kunci (mis. `pii_encryption_key`) **berada di GitHub** → bukan transfer data pribadi, tetapi relevan untuk risiko (lihat §4) |
| P5 | 3PL (fase 3) | Pengiriman | Nama, HP, alamat | Per order online | Indonesia (verifikasi) | Di luar cakupan sekarang |
| P6 | **9router → penyedia model (Anthropic/Google)** (G37, ADR-037) | OCR tahap 2 Price Hunter (baca label harga yang sulit) | **Foto label harga** ter-encode ulang (tanpa EXIF/GPS); bisa memuat latar (wajah/kartu orang lain). **Tidak** dikirim: nama/harga laporan, nomor HP/user_hash, id laporan, lokasi | Hanya laporan yang gagal OCR klasik (tahap 2) | 9router: VPS Indonesia; penyedia model: AS/global | **Mati default** (`OCR_LLM_PROVIDER=ollama`); worker butuh `OCR_LLM_CROSS_BORDER_OK=true`; production ditolak deploy sampai Q-T7; allowlist host; fallback Ollama lokal |
| P7 | **9router → penyedia model** (G38, ADR-038) | Text-to-SQL "Tanya Data" (membuat query dari pertanyaan staf) | Prompt tetap + skema tabel + **teks pertanyaan staf** (bisa memuat nama/nomor yang diketik staf). **Tidak** dikirim: hasil query, identitas penanya, PT | Setiap pertanyaan bila 9router dipilih | 9router: VPS Indonesia; penyedia model: AS/global | **Mati default** (`TEXT2SQL_LLM_PROVIDER=ollama`, `TEXT2SQL_ENABLED=false`); `TEXT2SQL_CROSS_BORDER_OK=true` wajib; production ditolak sampai Q-T7; form Odoo meminta staf tidak menulis data pribadi |
| P8 | **9router → penyedia model** (G39, ADR-039) | Ringkasan "Analisis bertahap" (asisten analisis Tanya Data) | Pertanyaan + **angka hasil query agregat** (penjualan/transaksi/poin/waste/yield/harga kompetitor per PT/outlet/hari, maks 30 baris per langkah; teks bebas diredaksi pola email/nomor). **Tidak** dikirim: identitas member/penanya, kolom identitas (tidak ada di `efn_t2s`) | Setiap analisis bila ringkasan 9router dipilih | 9router: VPS Indonesia; penyedia model: AS/global | **Mati default** (`TEXT2SQL_AGENT_ENABLED=false`, `TEXT2SQL_AGENT_SUMMARY_PROVIDER=none`); izin terpisah `TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK=true`; production ditolak sampai Q-T7. Data bisnis rahasia (bukan data pribadi) — tetap dicatat karena keluar negeri. Dev diizinkan pemilik 1 Nov 2026 (agregat tanpa PII) |
| — | ALTCHA, Ollama/OCR, Metabase, ClickHouse | — | Self-hosted di VPS Indonesia | — | Indonesia | Tidak ada transfer |

## 3. Opsi mitigasi per pemroses
| Pemroses | Opsi | Dampak | Biaya / usaha |
|---|---|---|---|
| Meta | M1. DPA/ketentuan pemrosesan Meta for Business + penilaian kesetaraan (Legal) | Dasar (2) | Kajian Legal |
| | M2. Aktifkan **Local Storage Indonesia** pada nomor WhatsApp Business (bila tersedia untuk akun) | Data at rest di Indonesia; data in use tetap transit global | Konfigurasi saat registrasi nomor; biaya: **validasi** |
| | M3. Persetujuan eksplisit di syarat & kebijakan privasi saat daftar | Dasar (3), cadangan | Teks consent |
| | M4. Minimisasi: OTP & utilitas tanpa nama/alamat; push notification app (FCM/APNs juga pemroses lintas negara — **catat bila dipakai**) | Mengurangi data | Rp0 |
| Cloudflare | C1. DPA Cloudflare (standar, termasuk klausul kontrak) + penilaian | Dasar (2) | Kajian Legal |
| | C2. **Data Localization Suite / Regional Services** (paket Enterprise) — dekripsi TLS hanya di region tertentu | Mengurangi transit; ketersediaan region Indonesia & harga **belum terverifikasi** | Biaya Enterprise (mahal) |
| | C3. CF **DNS-only** untuk endpoint yang membawa PII (mis. `api.` OTP/foto), proxy CF hanya untuk aset statis | Tidak ada dekripsi di CF untuk PII; kehilangan WAF/DDoS CF di endpoint itu → butuh rate limit & kapasitas origin | ±1 minggu devsecops; risiko DDoS naik |
| | C4. Pertahankan proxy penuh + kebijakan privasi menyebut Cloudflare | Paling sederhana | Rp0 |
| Payment | G1. Jadikan lokasi data & daftar sub-pemroses sebagai **kriteria wajib** pemilihan vendor (ADR-026) | Mencegah masalah sejak kontrak | — |
| GitHub | H1. Tetap: tanpa data pelanggan; audit berkala fixture/log CI | — | Rp0 |
| | H2. Kurangi kunci data di GitHub: kunci PII/WAL-G hanya di host + salinan offline Superadmin, bukan di Environments | Mengurangi dampak kompromi akun GitHub | ±2 hari devsecops; deploy lebih manual |

## 4. Risiko
- R-L01 (pelanggaran UU PDP): transfer tanpa dasar sah; kebijakan privasi tidak menyebut pemroses.
- Kompromi akun GitHub Environment → kunci PII & backup bocor (bukan transfer, tetapi dampak setara kebocoran) — mitigasi H2 + kustodi Superadmin (ADR-022).
- Perubahan sub-pemroses vendor tanpa pemberitahuan → klausul kontrak notifikasi.

## 5. Default sementara (dipakai sampai DPO/Legal memutuskan)
1. Meta: M1 + M3 + M4; M2 dievaluasi saat registrasi nomor produksi.
2. Cloudflare: C1 + C4 (proxy penuh); C3 disiapkan sebagai opsi bila DPO menolak dekripsi PII di CF.
3. Payment: G1.
4. GitHub: H1; H2 direkomendasikan.
5. Kebijakan privasi (`/privasi`) mencantumkan daftar pemroses ini, negara/region, dan tujuan.

## 6. Keputusan yang diminta dari DPO/Legal
- **Q-T1** Dasar transfer untuk Meta & Cloudflare (kesetaraan / kontrak / persetujuan).
- **Q-T2** Aktifkan Local Storage Indonesia WhatsApp (bila tersedia)?
- **Q-T3** Cloudflare: proxy penuh (C4), DNS-only untuk endpoint PII (C3), atau paket lokalisasi (C2)?
- **Q-T4** Wajibkan data payment gateway diproses di Indonesia sebagai syarat vendor?
- **Q-T5** Setuju H2 (kunci PII/backup tidak disimpan di GitHub)?
- **Q-T6** Perlu DPIA untuk aliran P1/P2?
- **Q-T7** (G37/G38/G39) Dasar transfer foto Price Hunter (P6), teks pertanyaan Tanya Data (P7), dan angka hasil agregat untuk ringkasan Analisis bertahap (P8) ke penyedia model via 9router? Opsi: (a) persetujuan eksplisit di syarat Price Hunter + kebijakan privasi, (b) kontrak/DPA penyedia, (c) tetap Ollama lokal saja. Sampai diputuskan: production memakai Ollama (dev boleh dengan foto uji, keputusan pemilik 1 Nov 2026).

## 7. Yang belum terverifikasi
Nomor pasal & peraturan pelaksana UU PDP; daftar region Local Storage WhatsApp (sumber sekunder: [Clickatell](https://guides.clickatell.com/release-notes/q3-jul-sep-2024/channel-updates/whatsapp-data-localization), [360dialog](https://docs.360dialog.com/docs/hub/local-storage), [Infobip](https://www.infobip.com/docs/whatsapp/compliance/data-privacy)); ketersediaan & harga Cloudflare Data Localization Suite untuk Indonesia; lokasi pemrosesan vendor payment.
