# Draf Penilaian Dampak Pelindungan Data Pribadi (DPIA) — EFN Platform

- **Status:** **DRAF architect untuk DPO (Legal, DPO sementara sejak 10 Okt 2026)**. Belum dinilai DPO dan belum ditandatangani. Bukan nasihat hukum.
- **Tanggal:** 2026-10-18 · **revisi 2026-10-19 (G22): §13 baru — RoPA & catatan DPIA anonimisasi karena retensi (D-22-14) dan notifikasi hangus (D-22-16); kegiatan P15–P18, risiko DR-23..DR-28, isian D-12..D-17** · Penyusun: efn-architect · Paket: W-28 (DPIA wajib sebelum go-live publik, keputusan pemilik proyek 10 Okt 2026 butir #56 / Q-T6; 18-37)
- **Dasar hukum penilaian:** UU No. 27 Tahun 2022 tentang Pelindungan Data Pribadi, **Pasal 34**. Teks diverifikasi dari pasal.id, 18 Okt 2026:
  - Ayat (1): "Pengendali Data Pribadi wajib melakukan penilaian dampak Pelindungan Data Pribadi dalam hal pemrosesan Data Pribadi memiliki potensi risiko tinggi terhadap Subjek Data Pribadi."
  - Ayat (2) huruf a–g: pemicu risiko tinggi (§2).
  - Ayat (3): ketentuan lebih lanjut diatur Peraturan Pemerintah. **Status PP pelaksana per Okt 2026 belum terverifikasi.** Format draf ini memakai kerangka umum (deskripsi pemrosesan, kebutuhan & proporsionalitas, risiko, mitigasi, residu) dan wajib disesuaikan bila PP/pedoman lembaga menetapkan format lain.
- **Dasar pemrosesan** mengikuti Pasal 20 ayat (2) huruf a–f (diverifikasi dari pasal.id, 18 Okt 2026): a persetujuan eksplisit; b kewajiban perjanjian; c kewajiban hukum; d kepentingan vital; e tugas kepentingan umum/kewenangan; f kepentingan sah lain. Pasal lain yang dirujuk dokumen kepatuhan EFN (hak subjek, notifikasi 3×24 jam, DPO) **belum diverifikasi nomornya** dan tetap tugas Legal.
- **Terkait:** `PDP_DATA_INVENTORY.md` **v8** (RoPA; sumber rincian per kolom; §2.8 = pemrosesan G20–G22), `docs/04` §14 + L-70..L-74 (G22), `docs/runbooks/fnb-qr-orders.md`, `PDP_DSAR_DESIGN.md`, `PDP_CONSENT.md`, `PDP_BREACH_RESPONSE.md`, `PDP_TRANSFER_LINTAS_NEGARA.md`, ADR-014, ADR-017, ADR-020, ADR-022, ADR-023, ADR-024, ADR-028, ADR-029, ADR-031 (`efn_audit`), ADR-032, ADR-035, **ADR-036 (Accepted 18 Okt)**, ADR-001 (revisi 18 Okt: 6 entitas), `docs/runbooks/{security-incident,log-redaction,loyalty-anomaly,key-custody,retention,online-orders}.md`, risk register `docs/01` (R-L01, R-L10, R-S10, R-B11, R-T08).

> **Ringkasan manajemen.** DPIA adalah penilaian tertulis yang **wajib menurut UU PDP** sebelum EFN memproses data pribadi dalam skala besar atau dengan teknologi baru. EFN memenuhi beberapa pemicunya: program member lintas brand, aplikasi dan kasir di banyak outlet, deteksi kecurangan poin otomatis, AI untuk membaca foto label harga, dan penggabungan data dari enam perusahaan untuk analitik. Draf ini memetakan **18 kegiatan pemrosesan** dan **28 risiko** beserta pengamannya (14 dan 22 pada versi 18 Okt; tambahan G22 di §13). Sebagian besar pengaman **sudah dibangun**: enkripsi di semua jalur dan penyimpanan, akses Odoo hanya lewat jaringan privat, identitas member berupa kode acak, log tanpa nomor HP, dan alur hapus akun. Sebagian lagi **dijadwalkan**: enkripsi data pribadi di Odoo (G20–G21), anonimisasi member tidak aktif 12 bulan, dan pemisahan tugas. Setelah pengaman terpasang, risiko yang tersisa paling besar ada pada: (1) **staf berhak yang menyalahgunakan akses**, (2) **satu orang Superadmin pemegang kunci**, dan (3) **vendor pembayaran/kurir yang belum dipilih**. **DPO perlu** mengisi bagian §10, menilai residu, dan menandatangani **sebelum go-live publik**.

> **Tambahan 19 Okt 2026 (G22).** Empat kegiatan baru dinilai: pesan lewat QR meja, poin di belanja online, **pemberitahuan WhatsApp sebelum poin hangus**, dan **anonimisasi akun setelah poin hangus karena 12 bulan tidak aktif**. Dua yang terakhir adalah **keputusan otomatis yang merugikan secara ekonomi** (poin hilang, akun dihapus). Karena itu keduanya **dimatikan di produksi** sampai Legal/DPO menyetujui teks syarat program dan menetapkan jalur keberatan, dan Meta menyetujui template pesan. Rincian dan pertanyaan untuk DPO ada di §13.

---

## 1. Ruang lingkup & pengendali

| Hal | Isi | Status |
|---|---|---|
| Sistem | Super-App (Next.js) + BFF (NestJS) + PostgreSQL platform; Odoo CE 19 (6 company, satu DB); RabbitMQ; worker OCR/AI (Ollama, jaringan privat); ETL → ClickHouse (DWH) + Metabase; object storage (SILO); observability (Loki/Prometheus/Grafana); WhatsApp Cloud API; Cloudflare; payment gateway (**belum dipilih**); 3PL (**belum dipilih**) | Arsitektur `docs/02` |
| Entitas | Holding EFN, PT Grand Lucky, PT Paris Baguette, Bacha Coffee Retail, Bacha Coffee F&B, FARM (ADR-001 revisi 18 Okt) | Diputuskan 18 Okt |
| **Pengendali per kegiatan** | Usulan architect: **Holding EFN** = pengendali data member, Super-App, poin, analitik grup; **tiap PT** = pengendali data transaksi, faktur, pelanggan kasir, vendor, dan karyawannya sendiri; Holding sebagai **prosesor** atau **pengendali bersama** untuk layanan bersama (hosting, IT) | ⚠ **DPO menetapkan** (Q-DPIA-1, sama dengan Q-AD1-5); butuh perjanjian antar-entitas |
| Prosesor eksternal | Meta (WhatsApp), Cloudflare, penyedia VPS (ADR-004), payment gateway & 3PL (belum), GitHub (kode saja, tanpa data pelanggan) | `PDP_TRANSFER_LINTAS_NEGARA.md` |
| Di luar lingkup | Sistem lama klien sebelum migrasi; CCTV/absensi fisik; sistem pihak ketiga franchisee | DPO konfirmasi |

## 2. Pemicu risiko tinggi (Pasal 34 ayat 2)

| Huruf | Pemicu | Berlaku di EFN? | Kegiatan (§3) |
|---|---|---|---|
| a | Keputusan otomatis berakibat hukum/signifikan | **Ya, terbatas.** Penahanan poin/transaksi oleh deteksi anomali (`hold`), penolakan OTP oleh rate limit/captcha, pembatalan otomatis pesanan tak dibayar. **(19 Okt) Hangus poin & anonimisasi akun karena tidak aktif** (job otomatis, nilai ekonomi hilang) — §13. Tidak ada penilaian kredit/penolakan layanan permanen otomatis | P2, P4, P7, **P17, P18** |
| b | Data pribadi spesifik | **Tidak sengaja dikumpulkan.** Risiko insidental: catatan dapur "alergi …" (kesehatan), foto Price Hunter memuat wajah orang | P5, P13 |
| c | Skala besar | **Ya.** Member lintas 4 brand ritel/F&B; target ratusan ribu member (asumsi, belum ada angka klien) | P1–P4, P8 |
| d | Evaluasi, penskoran, pemantauan sistematis | **Ya.** Pemantauan anomali poin per member/kasir/PT, batas anti-fraud, riwayat belanja untuk poin & promo | P2, P7 |
| e | Pencocokan/penggabungan data | **Ya.** Satu identitas member lintas 6 entitas; DWH menggabungkan transaksi semua PT (pseudonim) | P1, P2, P8 |
| f | Teknologi baru | **Ya.** OCR + LLM lokal untuk foto label harga; Text-to-SQL atas DWH; enkripsi ORM + token pencarian (ADR-036) | P5, P8 |
| g | Membatasi hak subjek | **Ya, terbatas.** Penundaan hapus akun ≤ 14 hari selama pesanan berjalan (Q-P18); data faktur pajak disimpan 10 th setelah erasure; poin hangus saat anonimisasi; **(19 Okt)** setelah anonimisasi subjek tidak lagi dapat meminta akses/ekspor riwayat (data sudah tidak tertaut) | P1, P4, **P18** |

**Kesimpulan architect:** DPIA **wajib** (huruf c, d, e, f terpenuhi). DPO menilai ulang.

## 3. Deskripsi pemrosesan

Rincian kolom, lokasi, retensi, dan enkripsi ada di `PDP_DATA_INVENTORY.md` v7 (RoPA). Tabel ini merangkum per kegiatan. Kolom dasar = Pasal 20(2) huruf (usulan, ⚠ Legal).

| ID | Kegiatan | Data pribadi | Subjek | Tujuan | Dasar (usulan) | Retensi (diputuskan 10/18 Okt) | Penerima |
|---|---|---|---|---|---|---|---|
| P1 | Akun member Super-App & OTP | HP (E.164), consent, pernyataan usia 17+, IP (hash/terenkripsi), perangkat | Pelanggan | Login, identitas member, keamanan | b (akun), f (keamanan IP), a (marketing/lokasi) | akun selama aktif; anonimisasi **12 bln** tidak aktif (18-14) — **OFF di production sampai D-12 (§13)**; suppression 365 h | BFF; Meta (OTP WA) |
| P2 | Program poin lintas brand | `member_hash`, ledger earn/redeem/clawback, PT penerbit/penukar, reservasi | Pelanggan | Poin = alat bayar, settlement antar-PT, akuntansi liabilitas | b, c (akuntansi) | ledger **10 th** | BFF, Odoo (jurnal agregat), ETL (pseudonim) |
| P3 | POS: pelanggan di kasir | nama, HP, email, alamat, NPWP (opsional), riwayat order; email struk | Pelanggan | Transaksi, struk, faktur | b, c (pajak) | transaksi **10 th** | Odoo; PC kasir (tanpa preload pelanggan terlindungi, 18-30) |
| P4 | Belanja online + faktur atas nama | riwayat order (member UUID), kode ambil, profil faktur opsional (nama, NPWP/NIK hanya bila diminta, alamat, HP, email) | Pelanggan | Jual-beli, ambil di toko, faktur | b, c | tautan akun 730 h, event 180 h, keuangan 10 th, kode ambil ≤ 7 h | BFF, Odoo, payment gateway (belum) |
| P5 | Price Hunter (foto label + OCR/AI) | foto (bisa memuat orang), lokasi dibulatkan (consent), `user_hash` | Pelanggan, pihak ketiga tak sengaja | Indeks harga pasar, reward poin | b (program), a (lokasi) | lokasi NULL **90 h**; foto per ILM; mart pseudonim 5 th | Worker OCR/Ollama (privat), DWH |
| P6 | MGM / referral (rencana ADR-009) | relasi pengajak–diajak (pseudonim), reward | Pelanggan | Insentif akuisisi | b | mengikuti ledger | BFF |
| P7 | Deteksi anomali & anti-fraud | pola earn/redeem per member/kasir/PT, `order_ref` opak, uid kasir | Pelanggan, karyawan | Pencegahan kecurangan | f | 400 h (recon), audit 730 h / 10 th | Supervisor, Finance |
| P8 | Analitik DWH, Metabase, Text-to-SQL | fakta transaksi dengan pseudonim (`customer_id`, `member_hash`), **kode wilayah kab/kota** (ADR-036 §0.7) | Pelanggan (pseudonim) | Laporan grup & konsolidasi | f, c | baris 10 th; pseudonim dianonimkan **5 th** | Manajemen, Finance (view whitelist) |
| P9 | Karyawan & akun Odoo | nama kerja, email kerja, PIN POS, 2FA, log login/audit; **data pribadi HR** (alamat rumah, HP/email pribadi, NIK, paspor, kontak darurat — terenkripsi E-38b) | Karyawan | Hubungan kerja, keamanan, pengendalian internal | b, c, f | audit 730 h (min 365); HR menurut kebijakan HR (⚠ belum ditetapkan) | HR, atasan, Superadmin |
| P10 | Vendor & portal vendor | nama/kontak vendor perorangan, NPWP/NIK, login opak, 2FA, log akses | Vendor perorangan, kontak vendor | Pengadaan, tagihan | b, c, f (keamanan) | dokumen 10 th | Procurement, Finance; portal-gateway (ADR-035) |
| P11 | Pesan-antar / kurir internal (rencana W-05) | alamat, kode wilayah, nama & HP penerima, catatan kurir | Pelanggan, penerima | Pengiriman | b | ⚠ belum ditetapkan (usulan: alamat di order 10 th sebagai dokumen; titik lokasi ≤ 30 h) | Kurir internal; 3PL (belum) |
| P12 | Notifikasi WhatsApp & edge Cloudflare | HP, isi template (OTP, status pesanan, pemberitahuan anonimisasi), IP | Pelanggan | Komunikasi transaksional | b | sesuai Meta/Cloudflare + log 30 h | Meta, Cloudflare (lintas negara) |
| P13 | F&B: KDS & QR order | meja/antrean, menu, catatan dapur teks bebas; QR tamu tanpa PII; login OTP opsional | Pelanggan (tidak langsung), staf dapur | Melayani pesanan | b, f (KPI) | tiket 90 h | Staf dapur |
| P14 | Log, audit, observability, backup | uid, aksi, nilai ter-mask, IP ter-hash; backup terenkripsi berisi semua data di atas | Semua | Keamanan, forensik, pemulihan | f, c | Loki 30 h / 400 h; audit 730 h / tanpa batas (log kontrol); WAL-G lokal 14 base; off-site **35 h + bulanan 12 bln** (18-35) | Superadmin, on-call |
| P15 *(19 Okt)* | QR meja F&B (W-07) | sesi meja (outlet, label meja, waktu), kiriman tanpa teks catatan di BFF, catatan tamu ≤ 140 di Odoo, IP ter-HMAC; **`member_id` bila tamu klaim poin** | Tamu (anonim), member (opsional), pelayan | Melayani pesanan meja, poin | b, f | BFF 180 h; tautan member 30 h setelah tutup; catatan ikut `pos.order` (usulan 90 h, Q-P29) | Pelayan, dapur (KDS), BFF |
| P16 *(19 Okt)* | Poin di belanja online (W-12) | reservasi/penukaran/perolehan poin per bagian pesanan (pseudonim) | Pelanggan | Poin = alat bayar online | b, c | ledger 10 th; reservasi 30 h | BFF, Odoo (jurnal Poin EFN, partner generik) |
| P17 *(19 Okt)* | Hitung tidak aktif + pemberitahuan WA + hangus poin (W-17, D-22-12/16) | `member_hash`, aktivitas terakhir, jadwal notice, HP + jumlah poin + tanggal hangus (ke Meta), entri hangus | Pelanggan | Menjalankan syarat program; breakage akuntansi | b (atau a bila Legal/Meta mensyaratkan) | aktivitas selama subjek ada; notice 400 h; ledger 10 th | BFF, Meta (lintas negara), Odoo (jurnal agregat) |
| P18 *(19 Okt)* | Anonimisasi karena retensi berakhir (D-22-14) | seluruh data akun member (alur erasure yang ada) | Pelanggan | Memusnahkan data yang tidak lagi diperlukan | kewajiban Pasal 16(2) g / 43(1) a / 44(1) a (⚠ DPO) | bukti `dsar_request` 5 th; suppression 365 h | BFF, Odoo `efn_privacy`, ClickHouse |

```mermaid
flowchart LR
    C[Pelanggan] -->|HTTPS via Cloudflare| SA[Super-App] --> BFF
    C -->|di kasir| POS[POS Odoo via WireGuard]
    BFF -->|OTP/notif| WA[Meta WhatsApp]
    BFF -->|json2 terfilter mTLS| ODOO[(Odoo 6 company)]
    POS --> ODOO
    BFF --> PG[(PG platform: HP terenkripsi, ledger pseudonim)]
    ODOO -->|ETL pseudonim + kode wilayah| DWH[(ClickHouse)]
    PG -->|ETL pseudonim| DWH
    DWH --> MB[Metabase / Text-to-SQL]
    BFF -->|foto| S3[(SILO via s3-gateway)] --> OCR[OCR/Ollama privat]
    BFF -.->|belum dipilih| PAY[Payment gateway]
    BFF -.->|belum dipilih| TPL[3PL]
```

## 4. Kebutuhan & proporsionalitas (minimisasi)
| Prinsip | Penerapan di EFN | Rujukan |
|---|---|---|
| Identitas minimum | Member = HP saja, tanpa nama wajib; JWT `sub` = UUID opak; tanggal lahir tidak dikumpulkan (hanya pernyataan 17+) | ADR-029, docs/04 §3.2 |
| Pseudonim untuk analitik | `member_hash` berpepper, `customer_id`; nama/kontak tidak pernah ke DWH; kota hanya kode kab/kota | ADR-036 §0.7, inventaris §2.4 |
| Opsional bila bisa | NPWP/NIK hanya bila pelanggan meminta faktur/B2B (18-7); QR order sebagai tamu; lokasi hanya dengan consent | 18-7, ADR-032, PDP_CONSENT |
| Tanpa data kartu | PAN tidak pernah disimpan | CLAUDE.md, ADR-026 |
| Retensi terbatas | Angka diputuskan 10 & 18 Okt, configurable, job purge | inventaris §0a, `runbooks/retention.md` |
| Tidak memuat ke perangkat | Pelanggan terlindungi tidak di-preload ke PC kasir | 18-30, ADR-036 |

⚠ **DPO menilai:** apakah profil faktur pembeli (P4) dan alamat pengiriman (P11) proporsional terhadap tujuannya, dan apakah retensi HR (P9) sudah ditetapkan.

## 5. Penilaian risiko terhadap subjek data
Skala 1–5 sama dengan `docs/01`: **K** = kemungkinan, **D** = dampak terhadap subjek, skor = K×D. "Inheren" = tanpa kontrol EFN; "Residu" = setelah kontrol yang **sudah ada + yang dijadwalkan**. Semua angka = **usulan architect; DPO menilai ulang.**

| ID | Risiko terhadap subjek | Keg. | Inheren K×D | Kontrol (ada ✓ / dijadwalkan ◷) | Residu K×D | Pemilik |
|---|---|---|---|---|---|---|
| DR-01 | Kebocoran salinan DB/backup Odoo membuka kontak, alamat, NPWP/NIK pelanggan, vendor, karyawan | P3, P4, P9, P10 | 3×5=15 | ✓ LUKS, TLS verify-full, WAL-G libsodium, Odoo tidak publik (ADR-022/023); ◷ **enkripsi ORM E-38a/b** (ADR-036) | 2×3=6 | IT + Security |
| DR-02 | Staf berhak menyalahgunakan akses (lihat/ekspor CSV pelanggan) | P3, P4, P9, P10 | 3×4=12 | ✓ grup & record rule per company, 2FA, WireGuard, `efn_audit`; ◷ peran & SoD (W-31), review akses bulanan; tahap 2: audit ekspor | 3×3=9 | Superadmin + Internal Audit |
| DR-03 | Pengambilalihan akun member (SIM swap, tebak OTP) → poin dicuri | P1, P2 | 3×3=9 | ✓ OTP ber-rate limit, ALTCHA (ADR-028), HP terkunci (SEC-115), step-up, anomali | 2×3=6 | BFF + Ops |
| DR-04 | Nomor HP daur ulang → pemilik baru melihat/memakai akun lama | P1 | 3×3=9 | ◷ anonimisasi 12 bln tidak aktif + notifikasi 30 h (W-17); ✓ HP lock & alur ganti nomor | 2×2=4 | BFF |
| DR-05 | Keputusan otomatis (hold poin, pembatalan pesanan) merugikan subjek tanpa penjelasan | P2, P4, P7 | 3×3=9 | ✓ `hold` → tinjauan supervisor manusia; pembatalan tanpa bayar tanpa kerugian; ◷ jalur keberatan via CS dicatat di syarat program (Legal) | 2×2=4 | Ops + Legal |
| DR-06 | Lokasi Price Hunter membuka kebiasaan/tempat tinggal | P5 | 3×4=12 | ✓ consent `location`, pembulatan 4 desimal, NULL 90 h, TTL ClickHouse | 1×3=3 | BFF + data |
| DR-07 | Foto memuat wajah/data pihak ketiga; teks OCR dibaca model | P5 | 3×3=9 | ✓ strip EXIF, OCR/LLM di jaringan privat (ADR-014), ILM foto, AI di luar jalur transaksi | 2×2=4 | data-AI |
| DR-08 | Text-to-SQL/Metabase membuka data pribadi lewat kueri bebas | P8 | 3×4=12 | ✓ user ClickHouse `readonly`, view whitelist, hanya pseudonim, tanpa nama/kontak; riwayat kueri 10 th | 2×2=4 | data-AI |
| DR-09 | Transfer lintas negara (Meta, Cloudflare) tanpa perlindungan setara | P12 | 3×3=9 | ✓ payload minimum, WA Local Storage bila tersedia, Cloudflare proxy (keputusan Q-T) ; ◷ DPA/klausul | 2×3=6 | Legal |
| DR-10 | Vendor payment/3PL memproses data tanpa DPA/di luar Indonesia | P4, P11 | 3×4=12 | ◷ kriteria wajib DPA + pemrosesan di Indonesia (Q-P20), data pembayar minimum, tanpa PAN | 2×3=6 | Finance + Legal |
| DR-11 | Penggabungan data lintas 6 entitas melampaui tujuan yang diberitahukan | P1, P2, P8 | 3×3=9 | ✓ ledger & DWH pseudonim; ◷ penetapan pengendali & pemberitahuan privasi grup (Q-DPIA-1/2) | 2×3=6 | Legal |
| DR-12 | Data pribadi HR (NIK, alamat rumah, kontak darurat) bocor/terlihat luas | P9 | 3×4=12 | ✓ grup `hr.group_hr_user`; ◷ enkripsi E-38b + redaksi tracking (ADR-036 §0.2) | 2×3=6 | HR + IT |
| DR-13 | Portal vendor terekspos internet → akses tidak sah ke data vendor/PO | P10 | 3×4=12 | ◷ ADR-035: portal-gateway terpisah, allowlist route, 2FA, WAF, rate limit, mTLS, login opak, pentest pihak ketiga | 2×3=6 | IT + Security |
| DR-14 | Alamat & HP penerima di kurir internal/3PL disalahgunakan | P11 | 3×3=9 | ◷ W-05: akses kurir per penugasan, alamat terenkripsi (E-38b), retensi titik lokasi pendek, DPA 3PL | 2×2=4 | Ops |
| DR-15 | Retensi 10 th dokumen pajak mempertahankan nama/NPWP setelah hapus akun | P3, P4 | 2×3=6 | ✓ kewajiban hukum (c); erasure menganonimkan selain data wajib faktur; enkripsi ORM + XML Coretax terenkripsi | 2×2=4 | Legal + Finance |
| DR-16 | Kehilangan kunci (bus factor 1 Superadmin) → data subjek tidak dapat dipulihkan / layanan terhenti | semua | 2×4=8 | ✓ salinan tersegel brankas Direksi, uji pemulihan disaksikan (rencana), break-glass; risiko **diterima** pemilik proyek (R-B11) | 2×4=8 | Direksi + Superadmin |
| DR-17 | Data pribadi masuk log (HP, email, kode promo) | P14 | 3×3=9 | ✓ masking HP, redaksi Promtail + uji CI, larangan hash kode promo (L-42), near-miss register | 1×3=3 | devsecops |
| DR-18 | Hak hapus tertunda/terbatas | P1, P4 | 2×3=6 | ✓ tunda maks 14 h + eskalasi CS + erasure paksa; teks pelanggan (18-36) | 1×2=2 | CS + Legal |
| DR-19 | Anak di bawah 17 th mendaftar | P1 | 2×3=6 | ✓ pernyataan usia (app & kasir); tanpa verifikasi dokumen | 2×2=4 | Legal |
| DR-20 | Catatan dapur memuat data kesehatan (alergi) yang dikaitkan dengan orang | P13 | 3×3=9 | ✓ SOP "tulis menu/bahan, bukan kondisi orang", tiket tanpa nama, retensi 90 h | 2×2=4 | Ops F&B |
| DR-21 | Cache data pelanggan di PC kasir/tablet hilang/dicuri | P3 | 3×3=9 | ✓ pelanggan terlindungi tidak di-preload (18-30), perangkat via WireGuard; ⚠ enkripsi disk perangkat outlet **belum ditetapkan** (Q-DPIA-4) | 2×3=6 | IT outlet |
| DR-22 | Analisis frekuensi token nama/blind index pada dump DB menebak sebagian nama/kesamaan HP | P3, P10 | 2×2=4 | ✓ kunci terpisah, padding token, dump terenkripsi (ADR-036 §0.5, SEC-140); risiko **diterima** (R-T19) | 2×2=4 | IT + Security |
| DR-23 *(19 Okt)* | Poin member hangus tanpa pemberitahuan yang sah/terbaca (WA gagal, nomor tidak aktif, tanpa consent bila MARKETING) → kerugian ekonomi + keluhan | P17 | 3×4=12 | ◷ notice ≥ 30 h dijamin (`due_at = GREATEST(...)`), info di aplikasi (`points_expiry`), pengaman massal (`MAX_PER_RUN`, dry-run pertama), job **OFF prod** sampai teks Legal & template Meta; ◷ jalur keberatan (Q-P27) | 2×3=6 | Legal + Finance (R-L11) |
| DR-24 *(19 Okt)* | Definisi "aktif" keliru/bug jangkar → member aktif terhanguskan | P17 | 2×4=8 | ◷ aktivitas diperbarui di transaksi peristiwa, cek ulang jangkar saat expire, alert `EfnLoyaltyExpiryAborted` + deadman, dry-run per lingkungan; test BFF | 1×4=4 | BFF + QA |
| DR-25 *(19 Okt)* | Anonimisasi tidak dapat dibatalkan; subjek kehilangan akses ke riwayat & hak akses/ekspor | P18 | 3×3=9 | ◷ pemberitahuan menyebut anonimisasi, masa tenggang (`GRACE_DAYS`, usulan 30 h), aktivitas selama tenggang membatalkan request; flag **OFF prod** | 2×2=4 | DPO |
| DR-26 *(19 Okt)* | Isi notifikasi (HP + jumlah poin + tanggal) ke Meta lintas negara | P17 | 3×2=6 | ✓ tanpa nama, nominal Rupiah, tautan berparameter; ◷ WA Local Storage (Q-T) | 2×2=4 | Legal |
| DR-27 *(19 Okt)* | QR meja: token difoto/dibagikan → pesanan iseng atas nama meja lain; catatan tamu memuat nama/kondisi kesehatan | P15 | 3×3=9 | ✓ token di fragment, HMAC + TTL ≤ 4 jam + dicabut saat bayar/cetak ulang; batas per sesi; default **konfirmasi pelayan** (L-71); filter catatan (deret digit, `@`); tamu tanpa identitas | 2×2=4 | Ops F&B (R-B12) |
| DR-28 *(19 Okt)* | Tautan member ↔ kunjungan restoran membentuk profil kebiasaan | P15, P16 | 2×3=6 | ✓ tautan dilepas 30 h setelah sesi ditutup; ledger pseudonim; DSAR export & erasure | 1×3=3 | BFF |

## 6. Rencana mitigasi yang belum selesai (mengikat go-live publik)
| # | Mitigasi | Risiko | Paket / gelombang | Syarat go-live? |
|---|---|---|---|---|
| M-1 | Enkripsi ORM pelanggan (E-38a) — DB produksi dibuat setelahnya | DR-01, DR-15 | W-08, G20 | **Ya** |
| M-2 | Enkripsi vendor/portal/karyawan (E-38b) sebelum impor master | DR-01, DR-12 | W-08, G21 | **Ya** (sebelum impor) |
| M-3 | Peran & SoD + review akses bulanan | DR-02 | W-31, G21 | **Ya** |
| M-4 | Anonimisasi 12 bulan + notifikasi (**dibangun G22, OFF prod**; aktivasi butuh D-12..D-15) | DR-04, DR-23, DR-25 | W-17, G22 | Ya, sebelum bulan ke-12 setelah go-live (`LOYALTY_ACTIVITY_EPOCH` = tanggal go-live, sehingga hangus pertama paling cepat 12 bulan setelahnya) |
| M-11 *(19 Okt)* | Jalur keberatan atas hangus/anonimisasi lewat CS + SOP pemulihan Finance | DR-23, DR-25 | Legal + Finance (Q-P27) | Ya, sebelum `LOYALTY_EXPIRY_ENABLED=true` |
| M-12 *(19 Okt)* | Teks syarat program & pemberitahuan privasi (12 bulan, 30 h, hangus, anonimisasi) | DR-23, DR-25 | Legal (Q-F22-4) | Ya, sebelum job hangus aktif |
| M-13 *(19 Okt)* | SOP QR meja (cetak ulang, cabut sesi, catatan tanpa nama/kondisi) | DR-27 | `docs/runbooks/fnb-qr-orders.md` | Ya, sebelum `FNB_QR_ENABLED=true` |
| M-5 | DPA + pemrosesan di Indonesia untuk payment & 3PL | DR-10, DR-14 | A-4, W-05 | **Ya** untuk fitur terkait |
| M-6 | Perjanjian pengendali antar-entitas + pemberitahuan privasi grup | DR-11 | Legal (Q-DPIA-1/2) | **Ya** |
| M-7 | Backup off-site terenkripsi 35 h + bulanan 12 bln; uji pemulihan disaksikan | DR-16 | W-28, W-19 (staging) | **Ya** |
| M-8 | Portal vendor: pentest pihak ketiga | DR-13 | W-13, G23+ | Ya, sebelum portal dibuka |
| M-9 | Enkripsi disk perangkat outlet (PC kasir/tablet) | DR-21 | W-23 / ADR-023 | Disarankan |
| M-10 | Audit ekspor CSV `res.partner` | DR-02 | ADR-036 tahap 2 | Disarankan |

## 7. Risiko residu
Setelah M-1..M-8, residu tertinggi (usulan architect):
1. **DR-02** penyalahgunaan oleh staf berhak (9). Enkripsi tidak dapat menutupnya; kontrolnya bersifat organisasi.
2. **DR-16** bus factor kunci (8). Risiko ini sudah diterima pemilik proyek.
3. **DR-01, DR-09, DR-10, DR-11, DR-12, DR-13, DR-21** (6).

Tidak ada residu ≥ 15. **DPO memutuskan** apakah residu diterima, perlu mitigasi tambahan, atau perlu konsultasi dengan lembaga pengawas (bila peraturan pelaksana mewajibkannya untuk residu tinggi; **belum terverifikasi**).

## 8. Hak subjek, insiden, transfer
- Hak akses/koreksi/hapus/tarik consent: `PDP_DSAR_DESIGN.md` (sudah dibangun sebagian: DSAR export, hapus akun dengan penundaan ≤ 14 h, consent).
- Insiden & notifikasi: `PDP_BREACH_RESPONSE.md`, `runbooks/security-incident.md`; register near-miss dipegang Legal (`PDP_DATA_INVENTORY.md` §6).
- Transfer lintas negara: `PDP_TRANSFER_LINTAS_NEGARA.md`.

## 9. Konsultasi
| Pihak | Peran dalam DPIA | Status |
|---|---|---|
| DPO (Legal) | Pemilik dokumen, penilai residu, penandatangan | Belum |
| IT/Security (Superadmin) | Verifikasi kontrol teknis | Belum |
| Finance + konsultan pajak | Retensi pajak, P4/P2 | Belum |
| HR | P9, kebijakan retensi HR | Belum |
| Ops Outlet / F&B | P3, P13, perangkat outlet | Belum |
| Procurement | P10 portal vendor | Belum |
| Perwakilan subjek (opsional) | Uji keterbacaan pemberitahuan privasi | Opsional |

## 10. Yang harus diisi/diputuskan DPO
| # | Isian | Catatan architect |
|---|---|---|
| D-1 | **Pengendali per kegiatan** & perjanjian antar-entitas (Q-DPIA-1) | Usulan §1 |
| D-2 | Pemberitahuan privasi grup (6 entitas) + teks consent (Q-DPIA-2) | Teks 17+ & hapus akun 14 h sudah final (18-36) |
| D-3 | **Nomor pasal** untuk setiap dasar pemrosesan §3 | Pasal 20(2) huruf sudah ditandai |
| D-4 | Penilaian kebutuhan & proporsionalitas P4, P9, P11 (§4) | — |
| D-5 | Nilai K×D residu final (§5) dan **keputusan penerimaan residu** (§7) | — |
| D-6 | Retensi data HR (P9) dan pesan-antar (P11) | Belum ada keputusan |
| D-7 | Enkripsi disk perangkat outlet (Q-DPIA-4) | Biaya/perangkat dari IT |
| D-8 | Apakah PP/pedoman lembaga PDP sudah terbit dan mengubah format (Q-DPIA-3) | Belum terverifikasi |
| D-9 | Daftar prosesor + status DPA (Meta, Cloudflare, VPS, payment, 3PL) | Payment & 3PL belum dipilih |
| D-10 | Konfirmasi interpretasi ADR-036 Q-PII7 (nama karyawan plaintext) dan Q-PII10 (kode wilayah plaintext) | — |
| D-11 | Tanda tangan, tanggal, jadwal tinjau ulang | — |
| D-12 *(19 Okt)* | Dasar hukum & retensi anonimisasi karena retensi (RoPA §13.1), masa tenggang | Q-P22 |
| D-13 *(19 Okt)* | Dasar & kategori notifikasi hangus; perlakuan member tanpa consent | Q-P25 |
| D-14 *(19 Okt)* | Isi template (jumlah poin) & bukti pemberitahuan setelah anonimisasi | Q-P26 |
| D-15 *(19 Okt)* | Jalur keberatan Pasal 10 ayat (1) atas hangus/anonimisasi | Q-P27 |
| D-16 *(19 Okt)* | Definisi "aktif" (termasuk login OTP?) — bersama Finance | D-22-12, Q-F22-3 |
| D-17 *(19 Okt)* | Paket retensi QR meja | Q-P29 |

## 11. Kapan ditinjau ulang
Sebelum go-live publik (wajib), lalu tahunan. Ditinjau juga saat ada kegiatan baru: pesan-antar (W-05), portal vendor (W-13), MGM (ADR-009), vendor payment/3PL terpilih, model AI baru, perubahan pengendali/entitas, insiden data pribadi, atau terbitnya PP pelaksana Pasal 34(3). *(19 Okt)* Juga sebelum aktivasi produksi QR meja (`FNB_QR_ENABLED`), poin online, job hangus, atau anonimisasi karena retensi (§13.6).

## 12. Belum terverifikasi
- Status dan isi PP pelaksana UU PDP (format DPIA, kewajiban konsultasi lembaga).
- Nomor pasal selain Pasal 20 dan 34.
- Jumlah member/transaksi (asumsi "skala besar" belum didukung angka klien).
- Kontrol berlabel ◷ (belum dibangun) dan semua kontrol yang butuh staging (LUKS, off-site, timer, perangkat outlet).
- Kebijakan retensi HR dan pesan-antar.
- Lokasi pemrosesan vendor payment/3PL (belum dipilih).
- Sintaks Mermaid tidak dirender.
- *(19 Okt)* Kode G22 (job hangus, anonimisasi, QR meja) masih dibangun; §13 mengikuti kontrak `docs/04` §14, bukan kode yang sudah diuji.
- *(19 Okt)* Kategori template WA dan aturan opt-in Meta.

## 13. Tambahan 19 Okt 2026 — RoPA & catatan DPIA: anonimisasi karena retensi (D-22-14) dan notifikasi hangus (D-22-16)

> **Ringkasan manajemen.** Mulai G22 sistem dapat menghanguskan poin member yang **12 bulan tidak aktif**. Member diberi tahu lewat WhatsApp **paling lambat 30 hari sebelumnya**, lalu (bila diaktifkan) akunnya **dianonimkan**: nomor HP dan tautan ke riwayat belanja dihapus, sedangkan catatan keuangan tetap disimpan 10 tahun tanpa identitas. Keduanya adalah keputusan otomatis yang merugikan secara ekonomi. Karena itu keduanya **mati di produksi** sampai DPO dan Legal menetapkan dasar hukum, teks syarat program, dan cara member mengajukan keberatan. Biaya teknis pengaktifan Rp0 (sakelar). Biaya keputusan "tunda hangus bila notifikasi gagal" ±1–2 hari engineer.

Sumber teknis: `docs/04` §14.D (W-17) + L-73; inventaris v8 §2.8 (N10–N12). Teks pasal diverifikasi pasal.id 19 Okt 2026: Pasal 16 ayat (2) huruf g ("Data Pribadi dimusnahkan dan/atau dihapus setelah masa retensi berakhir atau berdasarkan permintaan Subjek Data Pribadi, kecuali ditentukan lain oleh peraturan perundang-undangan"), Pasal 43 ayat (1) huruf a ("Data Pribadi tidak lagi diperlukan untuk pencapaian tujuan pemrosesan Data Pribadi"), Pasal 44 ayat (1) huruf a ("telah habis masa retensinya dan berketerangan dimusnahkan berdasarkan jadwal retensi arsip"; Penjelasan: memusnahkan = data "tidak lagi dapat digunakan untuk mengidentifikasi Subjek Data Pribadi"), Pasal 10 ayat (1) (hak keberatan atas keputusan yang hanya didasarkan pada pemrosesan otomatis yang berakibat hukum/berdampak signifikan). **Penerapan pasal ke kasus EFN = penafsiran architect, bukan nasihat hukum.**

### 13.1 RoPA — kegiatan R-22-A: anonimisasi akun member karena retensi berakhir (P18)
| Unsur RoPA | Isi |
|---|---|
| Nama kegiatan | Anonimisasi akun member setelah poin hangus karena tidak aktif (`dsar_request.type='retention'`, `reason='inactivity'`) |
| Pengendali | Holding EFN sebagai pengendali data member (usulan §1, ⚠ Q-DPIA-1); PT penerbit poin sebagai pengendali catatan keuangan poinnya |
| Pemroses | Internal (BFF, Odoo `efn_privacy`, ETL ClickHouse). Tidak ada pihak ketiga |
| Tujuan | Memusnahkan data akun yang tidak lagi diperlukan setelah hubungan program berakhir karena tidak aktif; mengurangi risiko nomor HP daur ulang (DR-04) |
| Dasar | Bukan "dasar pemrosesan" baru melainkan **pelaksanaan kewajiban** penghapusan/pemusnahan (Pasal 16(2) g, 43(1) a, 44(1) a) — ⚠ DPO memilih pasal (D-12) |
| Kategori subjek | Member Super-App/POS yang 12 bulan tidak aktif dan poinnya sudah hangus (termasuk subjek yang hanya ada di ledger POS, `member_id` NULL) |
| Kategori data yang dimusnahkan | HP terenkripsi (`member.member_ref_enc`), `member` (baris), consent (`ip_enc` NULL), tautan pesanan online & sesi QR, reservasi/earn POS, `loyalty_member_activity`, `loyalty_expiry_notice`, partner member Odoo (anonimisasi `efn_privacy`), pseudonim ClickHouse |
| Data yang **tetap** (tanpa identitas) | Ledger 10 th (tombstone acak SEC-31), baris pesanan/keuangan 10 th, faktur atas nama pembeli (⚠ Q-P24), `dsar_request` (bukti, `member_hash` NULL saat selesai), `erasure_suppression` 365 h (tag HMAC, mencegah migrasi menghidupkan kembali) |
| Pemicu & jadwal | Job BFF harian 01:00 WIB setelah fase hangus; `scheduled_at = now + LOYALTY_INACTIVE_ANONYMIZE_GRACE_DAYS` (default 0, usulan 30) |
| Penerima | Tidak ada (pemusnahan) |
| Transfer lintas negara | Tidak ada. Pemberitahuan sebelumnya lewat Meta tercakup R-22-B |
| Keamanan | Alur erasure yang sudah ada (DSAR delete): transaksi per subjek, tombstone acak, Odoo pull mTLS, ETL DELETE; pengaman massal `LOYALTY_EXPIRY_MAX_PER_RUN`; heartbeat + alert |
| Keputusan otomatis | **Ya** (Pasal 10(1)) — tanpa manusia per kasus. Mitigasi: pemberitahuan ≥ 30 h, masa tenggang, jalur keberatan (Q-P27) |
| Hak subjek | Sebelum anonimisasi: akses/ekspor (`GET /v1/me/data`), hapus sendiri, keberatan via CS. Sesudahnya: tidak ada data tertaut → hak akses tidak dapat dipenuhi (diberitahukan di teks syarat) |
| Retensi catatan kegiatan | `dsar_request` 5 th (usulan lama, job belum ada) |
| Status | Kode G22 sedang dibangun; `LOYALTY_INACTIVE_ANONYMIZE=false` di production (L-73) |

### 13.2 RoPA — kegiatan R-22-B: hitung tidak aktif, pemberitahuan WA T−30, dan hangus poin (P17)
| Unsur RoPA | Isi |
|---|---|
| Nama kegiatan | Pemantauan aktivitas program poin, pemberitahuan hangus, dan hangus otomatis |
| Pengendali | Holding EFN (program member); PT penerbit (jurnal breakage per PT, L-67) |
| Pemroses | **Meta Platforms (WhatsApp Cloud API)** — lintas negara; internal BFF/Odoo |
| Tujuan | Menjalankan syarat program (poin berlaku selama member aktif); memberi tahu sebelum hak hilang; akuntansi liabilitas & breakage |
| Dasar (usulan) | Pasal 20(2) **b** pelaksanaan perjanjian (syarat program yang diterima saat daftar/login). Alternatif **a** (consent `marketing`) bila Legal mensyaratkan atau Meta menggolongkan template MARKETING (D-13). Jurnal breakage: **c** (akuntansi) |
| Kategori subjek | Member dengan saldo poin > 0 |
| Kategori data | `member_hash`, `last_activity_at` + `last_kind` (satu nilai, bukan riwayat), jadwal notice (`anchor_at`, `due_at`, `notified_at`, status), HP (didekripsi saat kirim), jumlah poin, tanggal hangus, entri ledger `inactivity_expire` |
| Sumber data | Peristiwa yang sudah ada (earn/redeem POS & online, login OTP sukses, Price Hunter valid, klaim poin QR, impor migrasi) — **login OTP dipakai untuk tujuan baru** (penanda aktif): kompatibilitas tujuan dinilai DPO (D-16) |
| Penerima | Meta (HP + jumlah poin + tanggal), Odoo (agregat Rupiah per PT/tanggal, tanpa member) |
| Transfer lintas negara | Ya (Meta) — `PDP_TRANSFER_LINTAS_NEGARA.md`; WA Local Storage bila tersedia (Q-T) |
| Retensi | Aktivitas: selama subjek ada; notice 400 h (usulan); ledger 10 th; pesan di sisi Meta: kebijakan Meta |
| Keamanan | `notify.push` hanya hash; HP di-resolve BFF dari kolom terenkripsi saat kirim; log tanpa member (`EFN_LOYALTY_EXPIRY_RUN` hanya hitungan); idempotensi `notify:loyalty_inactivity:<notice_id>` |
| Keputusan otomatis | **Ya** (hangus). Hangus tetap jalan walau notifikasi gagal (desain §14.D.4) → D-13 |
| Hak subjek | Akses: `GET /v1/me` `points_expiry` (`expires_on` hanya bila ≤ 60 h); keberatan: CS (Q-P27); tarik consent marketing tidak menghentikan pemberitahuan bila dasarnya b |
| Status | Kode G22 sedang dibangun; `LOYALTY_EXPIRY_ENABLED=false` di production; template belum didaftarkan di Meta |

### 13.3 Alur
```mermaid
flowchart TD
    A[Peristiwa aktif: earn/redeem/login OTP/klaim QR] -->|tx yang sama| ACT[(loyalty_member_activity: last_activity_at)]
    ACT --> J{Job 10:00 WIB: last_activity + 12 bln - 30 h <= hari ini?}
    J -->|ya, belum ada notice| N[(loyalty_expiry_notice queued)] --> Q[notify.push v2 hash saja] --> BFFR[BFF dekripsi HP] --> META[Meta WA: jumlah poin + tanggal]
    J -->|tidak| X[tidak ada tindakan]
    A2[Aktivitas baru sebelum due_at] --> SUP[notice superseded, tanpa pesan]
    N --> E{Job 01:00 WIB: due_at <= now, jangkar tidak bergeser, tidak ada pesanan/reservasi/DSAR terbuka?}
    E -->|ya| EXP[entri inactivity_expire per lot -> breakage per PT penerbit]
    E -->|tidak| L[coba besok]
    EXP --> F{LOYALTY_INACTIVE_ANONYMIZE?}
    F -->|false, default prod| END[akun tetap, saldo 0]
    F -->|true| R[dsar_request type=retention, grace] --> ER[ErasureService: platform, Odoo efn_privacy, ClickHouse]
    R -.->|aktivitas selama grace| C[request cancelled]
```

### 13.4 Kebutuhan & proporsionalitas
| Pertanyaan | Penilaian architect |
|---|---|
| Apakah data aktivitas minimum? | Ya — satu cap waktu per member, bukan riwayat; tidak ada data lokasi/perangkat |
| Apakah notifikasi minimum? | Tanpa nama, Rupiah, tautan berparameter. Jumlah poin masih dikirim (D-14: bisa dihapus, Rp0) |
| Apakah anonimisasi lebih baik daripada menyimpan akun? | Ya untuk minimisasi & risiko HP daur ulang. Kerugian: subjek kehilangan riwayat. Alternatif "hanya hangus poin, akun tetap" (default prod saat ini) menyimpan HP tanpa batas waktu sehingga bertentangan dengan Pasal 16(2) g bila tidak ada retensi lain |
| Apakah 12 bulan proporsional? | Keputusan pemilik proyek (18-14), configurable 6..36 bulan. DPO memastikan sejalan dengan teks syarat |
| Apakah hangus tanpa notifikasi terkirim adil? | Risiko utama (DR-23). Opsi: tetap hangus (desain sekarang) vs tunda bila notifikasi gagal (±1–2 hari BFF) — D-13 |

### 13.5 Risiko & residu
Lihat DR-23..DR-26 di §5. Residu tertinggi kegiatan ini: **DR-23 (6)**, dengan syarat M-11/M-12 terpenuhi sebelum aktivasi. Terkait register `docs/01`: R-L01, **R-L11**, R-B05.

### 13.6 Kapan bagian ini ditinjau ulang
Sebelum `LOYALTY_EXPIRY_ENABLED=true` atau `LOYALTY_INACTIVE_ANONYMIZE=true` di production; saat Meta mengubah kategori template; saat N bulan diubah; setelah run nyata pertama (dry-run) di staging; bila terbit PP pelaksana UU PDP tentang retensi/keputusan otomatis.
