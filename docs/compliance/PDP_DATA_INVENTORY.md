# Inventaris Data Pribadi / RoPA (UU PDP No. 27/2022) — EFN Platform

- **Status:** Draf teknis untuk ditinjau **DPO + Legal** (belum ditetapkan). **v3 (2026-10-06, Gelombang 12)**: diselaraskan dengan kondisi kode setelah Gelombang 8–11 (`docs/04` v3.8): `platform.member` dihapus saat erasure, tombstone acak (SEC-31), Redis key ber-HMAC tanpa PII (SEC-24), challenge login terenkripsi, log audit Odoo `efn_audit` + sink Loki 400 hari, SILO + `s3-gateway`, access key acak, retensi aktual di kode. Menutup butir dokumentasi SEC-11 / SEC-24 / SEC-40 / SEC-45 (review r4).
- **Tanggal:** 2026-10-05 · revisi v2 & v3 2026-10-06 · Penyusun: efn-architect
- **Keputusan klien 6 Okt 2026:** kebijakan UU PDP (penetapan DPO, retensi, transfer ke Meta/Cloudflare) **belum diputuskan**. Semua angka retensi di bawah adalah **default usulan yang dipakai sementara** dan configurable.
- **Terkait:** ADR-022 (enkripsi), ADR-024 (kredensial), ADR-026 (payment), ADR-028 (ALTCHA), ADR-029 (member opak), ADR-030 (object storage), ADR-031 (`efn_audit`), `docs/04 §1, §7, §8`, `PDP_DSAR_DESIGN.md`, `PDP_CONSENT.md`, `PDP_BREACH_RESPONSE.md`, `PDP_TRANSFER_LINTAS_NEGARA.md`, `docs/runbooks/security-incident.md`

> **Ringkasan manajemen.** Dokumen ini adalah daftar resmi (draf) semua data pribadi yang diproses platform: apa, di mana, untuk apa, dasar hukumnya, berapa lama disimpan, apakah terenkripsi, dan siapa yang boleh membaca. Sejak Gelombang 8–11: nomor HP tidak lagi tersimpan polos di cache, log aplikasi terbukti bebas nomor HP mentah (uji otomatis), akun yang dihapus tidak bisa lagi ditautkan ke nomor HP-nya, dan ada **log audit** perubahan data di Odoo yang juga disalin ke penyimpanan log terpisah selama 400 hari. Semua masa simpan masih **usulan teknis yang menunggu DPO**. Tiga keputusan baru untuk DPO: masa simpan log audit (Q-P9), siapa "Auditor Data Pribadi Lintas PT" (Q-P10), dan kebijakan akun tidak aktif / nomor HP daur ulang (Q-P11).

## 0. Cara membaca
- **Dasar pemrosesan** = usulan arsitek per Pasal 20 UU PDP (persetujuan, perjanjian, kewajiban hukum, kepentingan vital, tugas publik, kepentingan sah). **Wajib diverifikasi Legal** (nomor pasal belum diverifikasi).
- **Retensi** = default di kode/konfigurasi saat ini (sumber disebut), **menunggu DPO**.
- Legenda enkripsi: **L** = LUKS2 disk host · **K** = kolom AES-256-GCM aplikasi (kunci hanya BFF) · **S** = SSE-S3 object storage · **W** = backup WAL-G libsodium · **C** = ClickHouse encrypted disk · **T** = TLS verify-full in transit · **H** = hash berpepper (pseudonim, **tetap data pribadi**) · **M** = masking sebelum disimpan · **R** = tombstone acak (tidak dapat ditautkan).
- Catatan status: "LUKS" adalah keputusan ADR-022 untuk staging/produksi; di dev (Docker Desktop) tidak ada LUKS.

## 1. Kategori data & dasar pemrosesan (usulan — verifikasi Legal)
| Kategori | Contoh | Subjek | Dasar pemrosesan usulan | Spesifik? |
|---|---|---|---|---|
| Identitas kontak member | Nomor HP (E.164) | Pelanggan | Pelaksanaan perjanjian (akun, poin, OTP) | Tidak |
| Identitas pelanggan Odoo | Nama, HP, email, alamat kirim, NPWP/NIK | Pelanggan, supplier perorangan | Perjanjian + kewajiban hukum (faktur pajak) | Tidak |
| Lokasi | Koordinat laporan Price Hunter (dibulatkan) | Pelanggan | **Persetujuan** (`consent` purpose `location`) | Tidak |
| Foto | Foto label harga (bisa memuat orang lain) | Pelanggan, pihak ketiga tak sengaja | Perjanjian (program Price Hunter) + minimisasi (strip EXIF) | Tidak |
| Data teknis | IP, user-agent | Pelanggan, staf | Kepentingan sah (keamanan, anti-fraud) | Tidak |
| Pseudonim | `member_hash`, `user_hash`, `efn_member_hash` | Pelanggan | Mengikuti data asal | Tidak |
| Identitas opak | `member.id` (UUID, JWT `sub`) | Pelanggan | Mengikuti akun | Tidak |
| Transaksi, pembayaran & poin | Order, payment intent, ledger poin | Pelanggan | Perjanjian + kewajiban hukum (akuntansi) | **Cek "data keuangan pribadi"** (Q-P5) |
| Jejak audit | Siapa mengubah apa & kapan (uid), nilai lama/baru ter-mask | **Karyawan** (pelaku) + pelanggan/supplier (objek) | Kepentingan sah (pencegahan fraud internal, R-S10) + kewajiban hukum (pengendalian internal akuntansi) | Tidak |
| Karyawan (Odoo) | Nama, email kerja, PIN POS, log login, 2FA | Karyawan | Perjanjian kerja | Tidak |
| Marketing | Consent marketing | Pelanggan | **Persetujuan** | Tidak |

Data spesifik (kesehatan, biometrik, anak) tidak dikumpulkan dengan sengaja. Batas usia: Q-P3.

## 2. Inventaris per lokasi penyimpanan

### 2.1 PostgreSQL `efn_platform` skema `platform` (pemilik objek: role NOLOGIN `efn_platform_owner`; aplikasi `efn_bff` hanya DML minimum — SEC-39)
| Tabel.kolom | Data pribadi | Tujuan / dasar | Retensi default (sumber) | Enkripsi | Akses |
|---|---|---|---|---|---|
| `member.id` | UUID opak | JWT `sub`, relasi consent/DSAR · perjanjian | Selama akun; **baris dihapus** saat erasure (`erasure.service.ts`) | L, W, T | `efn_bff` |
| `member.member_hash` | Pseudonim HP | Lookup login · perjanjian | Selama akun; hilang bersama baris | H, L, W | `efn_bff` |
| `member.member_ref_enc` | HP | OTP/notifikasi, DSAR · perjanjian | Selama akun; hilang bersama baris | K, L, W | `efn_bff` (dekripsi hanya BFF) |
| `consent` (append-only, trigger) | `member_id`, purpose, versi, `text_sha256`, `ip_enc` | Bukti persetujuan · kewajiban hukum (pembuktian) | Akun + 5 th (usulan; **belum ada job purge**) — saat erasure `ip_enc` di-NULL-kan, baris dipertahankan tanpa PII (`member_id` yatim) | K (ip), L, W | `efn_bff` INSERT/SELECT + `UPDATE(ip_enc)` |
| `dsar_request` | `member_id`, `member_hash`, jenis, status, `reason` (tanpa PII) | Bukti pemenuhan hak · kewajiban hukum | 5 th (usulan; belum ada job). `member_hash` **hanya hidup selama aktif**: CHECK `ck_dsar_member_hash_active`, di-NULL-kan saat `completed` dan untuk request lain milik subjek yang dihapus (SEC-31) | H, L, W | `efn_bff` (tanpa DELETE); `efn_etl` kolom `id, member_hash, status, stores_pending` |
| `price_hunter_report.user_hash` | Pseudonim HP | Cap harian, dedup, poin | Ikut baris 730 h (`PRICE_HUNTER_REPORT_RETENTION_DAYS`); saat erasure → **tombstone acak** | H→R, L, W | `efn_bff`, `efn_ocr` (kolom terbatas), `efn_etl` |
| `price_hunter_report.user_enc` | HP | Notifikasi hasil | **NULL setelah 90 h** (`PRICE_HUNTER_USER_ENC_RETENTION_DAYS`) atau saat erasure | K, L, W | `efn_bff` |
| `price_hunter_report.geo_*` | Lokasi (dibulatkan) | Validasi & peta harga · **persetujuan** | Ikut baris 730 h (Q-P8: usulan NULL 90 h) | L, W | `efn_bff`, `efn_ocr`, `efn_etl` |
| `price_hunter_report.image_object/image_phash` | Referensi foto | Validasi, dedup | Objek 90 h (§2.3); referensi ikut baris | L, W | `efn_bff`, `efn_ocr` |
| `loyalty_ledger.member_ref_hash` | Pseudonim | Saldo poin, liabilitas per PT · kewajiban hukum (akuntansi) | 10 th (Q-P2); saat erasure → **tombstone acak**; entri forfeit `source_ref` NULL + idempotency UUID acak | H→R, L, W | `efn_bff` (tanpa DELETE / UPDATE `delta_points`), `efn_etl` |
| `loyalty_ledger.member_ref_enc` | HP | Notifikasi, DSAR | Sampai erasure → NULL | K, L, W | `efn_bff` |
| `otp_audit.subject` / `ip_enc` | Pseudonim / IP | Audit keamanan · kepentingan sah | **90 h** (`OTP_AUDIT_RETENTION_DAYS`); subject → tombstone acak saat erasure | H→R / K, L, W | `efn_bff` |
| `payment_intent` | `order_ref`, `company_code`, nominal, `provider_ref`, `qr_string` — **tanpa** nama/HP/kartu | Pembayaran · perjanjian + akuntansi | 10 th (Q-P2); belum ada job | L, W | `efn_bff` (tanpa DELETE). Data pribadi **tidak langsung**: `order_ref` dapat ditautkan ke `pos.order`/partner di Odoo |
| `outbox.payload` | Hanya hash (event v2) | Pengiriman event | Published **14 h** (`OUTBOX_RETENTION_DAYS`) | L, W | `efn_bff` |
| `processed_message` | Tidak | Idempotensi | **14 h** (`PROCESSED_MESSAGE_RETENTION_DAYS`) | L | `efn_bff` |

Tombstone (SEC-31, `docs/04 §8.13`): 32 byte acak per erasure, sama untuk semua baris subjek, **tidak** diturunkan dari `request_id`/`member_hash` dan tidak disimpan di tempat lain → baris pasca-hapus tidak dapat ditautkan kembali ke nomor HP, bahkan oleh pemegang pepper. Residu yang diakui: korelasi waktu `erased_at` ↔ `scheduled_at`.

### 2.2 PostgreSQL `efn_odoo` (pemilik: role `efn_odoo`)
| Model.field | Data pribadi | Tujuan / dasar | Retensi default | Enkripsi | Akses |
|---|---|---|---|---|---|
| `res.partner` name, phone, email, street, vat (NPWP/NIK) | Identitas pelanggan/supplier | Transaksi, faktur, pengiriman · perjanjian + kewajiban pajak | Selama hubungan; **anonimisasi** saat erasure (pull `GET /v1/pos/privacy/erasures`, termasuk arsip & lintas company; penanda `efn_erased`) kecuali faktur NPWP yang wajib utuh (menunggu DPO) | L, W, T (bukan per kolom — ADR-022 Keputusan 5, Q-E3) | User Odoo sesuai grup + record rule multi-company; ETL `efn_etl` grant baca terbatas |
| `res.partner.efn_member_hash` (stored, indexed) | Pseudonim HP | Mencocokkan permintaan hapus tanpa HP mentah | Dikosongkan saat anonimisasi (gagal → rollback) | H, L, W | Odoo (pemegang `member_hash_pepper`) |
| Kunci HP member (`efn_pos_otp_gate`, SEC-07/44) | — (kontrol) | HP partner ber-riwayat OTP hanya bisa diubah POS Manager/System; perubahan tercatat di chatter (ter-mask) & `efn.audit.log` | — | — | Keputusan terbuka: **kasir boleh membuat pelanggan?** (Q-K1, §4) |
| `pos.order` partner, `email`, `mobile` (salinan kontak) | Relasi/kontak pelanggan | Transaksi · perjanjian + akuntansi | 10 th (Q-P2); salinan kontak dibersihkan saat erasure | L, W | Kasir/manajer per company |
| `account.move` partner, NPWP/NIK | Identitas pajak | Kewajiban pajak (Coretax) | 10 th | L, W | Finance per company |
| Challenge OTP POS (`efn_pos_otp_gate`) | HP termask / pseudonim | Gate OTP promo | 30 h | L, W | POS |
| `res.users`, `hr.employee` (PIN POS), `res.users.log`, `auth_totp.device` | Karyawan | Akses ERP, keamanan · perjanjian kerja | Selama kerja / log 1 th (usulan) | L, W | Admin |
| `mail.message`, `ir.attachment`, filestore | Data bebas | Kolaborasi | Pembersihan tahunan (usulan) | L, W (filestore volume lokal di atas LUKS — Q-OS4) | Per dokumen |
| **`efn.audit.log` / `efn.audit.log.line`** (modul `efn_audit`, ADR-031) | Pelaku (`user_id` = uid asli + flag `is_sudo`), model/record, `company_id` + `prev_company_id`, nilai lama/baru **ter-mask sebelum masuk DB**: HP/email/NPWP-NIK (`****` + 3 digit akhir)/nama orang (inisial); `res_name` model orang = `model,id`; denylist password/token/TOTP/secret/binary/`efn_member_hash` tidak pernah dicatat. Flag `is_personal` | Pencegahan & pembuktian fraud internal (R-S10, R-B06), kontrol harga/promo · kepentingan sah + pengendalian internal | **730 h** default (`efn.audit.retention_days`), **minimum 365 h di-hard-code** (`MIN_RETENTION_DAYS`, nilai 1..364 ditolak). Log `purge` dan log model kontrol (`ir.config_parameter efn.audit.*`, `efn.audit.rule`, `ir.actions.server`, `ir.cron`, `ir.module.module`, `base.automation`) **tidak pernah di-GC** → retensi tak terbatas, **perlu keputusan DPO (Q-P9)**. Log milik partner **dihapus saat erasure** (`_efn_audit_purge_records`) | M, L, W, T | Read-only untuk semua (tidak ada grup dengan create/write/unlink; `sudo()` juga ditolak). **Auditor** per PT: log company/prev_company sendiri + log tanpa company yang non-pribadi. **Auditor Data Pribadi Lintas PT** (`group_auditor_personal`): + semua log pribadi tanpa company — **tidak diberikan ke siapa pun secara default, termasuk admin** (penunjukan = Q-P10). `base.group_system` → Manager audit (rekomendasi: hanya Superadmin) |

### 2.3 Object storage — SILO (fork MinIO, ADR-030) di belakang `s3-gateway`
Storage hanya di network internal `objstore`; semua klien lewat `s3-gateway` (HAProxy, TLS masuk + re-encrypt `verify required`, filter L7 SEC-41). Access key per klien **acak** dari secret; root user acak (`minio_root_user`). Log akses gateway **tanpa query string**.

| Bucket/prefix | Data pribadi | Retensi default | Enkripsi | Akses |
|---|---|---|---|---|
| `market-scout/reports/...` | Foto (EXIF dibuang) | **90 h** — lifecycle `efn-photo-retention` (`MINIO_MARKET_SCOUT_EXPIRE_DAYS`, terverifikasi runtime G9) + versi noncurrent 35 h; dihapus segera saat erasure (setelah commit DB, SEC-40) | S, L, T | BFF Put/Delete prefix `reports/*`; ocr-worker Get |
| `pg-backup/efn/*` | Seluruh isi PostgreSQL (platform + Odoo) | Lihat §2.5 | W (libsodium sebelum keluar host) + S + L + T | User backup (WAL-G) saja; object lock GOVERNANCE 15 h; PUT `?retention`/bypass-governance diblok gateway |

### 2.4 ClickHouse `efn_dwh` (turunan via ETL — PostgreSQL tetap source of truth)
| Tabel.kolom | Data pribadi | Retensi default | Enkripsi | Penghapusan / akses |
|---|---|---|---|---|
| `competitor_prices.user_hash`, geo (dibulatkan) | Pseudonim, lokasi | TTL 5 th (Q-P7: usulan kolom pseudonim 24 bln) | C, L, T | ETL `DELETE` per `member_hash` dari `dsar_request` lalu `privacy.erasure.completed`; Metabase/Text-to-SQL lewat view whitelist |
| `loyalty_events.member_hash` | Pseudonim | TTL 5 th | C, L, T | Sama. Baris lama yang memuat tombstone deterministik pra-SEC-31 tidak ikut diganti (opsional job satu kali) |
| `fact_sales_line.customer_id` | Pseudonim (id partner) | TTL 5 th | C, L, T | Partner dianonimkan di sumber |
| **Salinan audit `efn_audit_log` / `efn_audit_log_line`** (**sedang dibangun data-AI, belum terverifikasi**) | Sama dengan §2.2 (sudah ter-mask di sumber) + `row_hash = sha256(prev_hash ‖ payload)` | Usulan: **≥ retensi Odoo, default 730 h**, menunggu DPO (Q-P9) | C, L, T | Append-only: user ETL tanpa `ALTER/DELETE`; rekonsiliasi harian "id hilang PG vs CH" (indikasi penghapusan di luar GC). Akses baca hanya auditor (bukan Metabase umum). **Konflik yang perlu diputuskan:** erasure partner di Odoo menghapus log, salinan append-only tidak → usulan: salinan hanya menyimpan baris yang **tidak** `is_personal`, atau DELETE terkontrol oleh job erasure yang dicatat (Q-P9) |

### 2.5 Backup
| Komponen | Data pribadi | Retensi default | Enkripsi | Catatan |
|---|---|---|---|---|
| WAL-G base + WAL (`pg-backup`) | Semua isi PostgreSQL | **14 full backup** (`RETAIN_FULL=14`, `efn-backup.service`) + WAL; object lock GOVERNANCE 15 h; versi noncurrent kedaluwarsa 35 h | W, S, L, T | Data yang dihapus lewat DSAR tetap ada di backup sampai kedaluwarsa (±15–35 h) — dijelaskan di kebijakan privasi. Restore lama dapat "menghidupkan" data terhapus → replay erasure setelah restore belum otomatis (SEC-31 rek. #2, butuh keputusan kustodi) |
| Backup off-site | Sama | Belum ada (wajib sebelum go-live, Q-OS5) | W, S, T | — |

### 2.6 Cache, antrean, log
| Komponen | Data pribadi | Retensi | Enkripsi | Catatan / bukti |
|---|---|---|---|---|
| Redis `otp:<challenge_id>` | `member_hash`, `order_ref`, HMAC kode; login: field `m` = HP **terenkripsi** AES-256-GCM (AAD `efn:redis_otp.m:v1`) | TTL 180 s + grace 600 s; AOF | K (m), T, L | SEC-24 **ditutup**: tidak ada HP plaintext di Redis/AOF (`otp.service.ts`) |
| Redis rate limit `rl:*`, `otp:login:ip:<ipk>`, `otp:login:member:<member_hash>`, `otp:pos:*`, `otp:dsar:*`, `altcha:ip:<ipk>`, `dsar:receipt-ip:<ipk>` | IP **tidak** di nama key: `ipk` = HMAC (`redisKeyHash`, kunci turunan HKDF info `efn:redis_key:v1`); member = `member_hash` | ≤ 15 menit / 1 jam | H, T, L | SEC-24 butir IP **ditutup** |
| Redis `altcha:used:<sig>`, `dtok:used:<jti>`, `otp:stepup-used:<jti>`, `rl:paychk:<id>` | Tidak | TTL token / 5 menit | T, L | — |
| RabbitMQ (`ocr.requested`, `loyalty.earn.requested`, `notify.push`, `privacy.erasure.completed` + DLQ) | Hash saja (+ geo dibulatkan di `ocr.requested`) | Sampai dikonsumsi; DLQ ≤ 7 h | T, L | Event v2 |
| Loki — log container (30 h, `retention_period 720h`) | HP **di-mask** (`62812****789`); IP klien di access log Traefik | 30 h | T (mTLS CN allowlist), L | **Bukti smoke G8–G11: 0 baris HP mentah** di log api-gateway, ocr-worker, etl, superapp-web, odoo (+ s3-gateway, Traefik; 7 service di G11). R2 (HP di query string) ditutup: `POST /v1/pos/loyalty/balance`. Log PostgreSQL **dev** (`log_statement=all`) memuat HP — dev saja; produksi `log_parameter_max_length=0` |
| Loki — stream `efn_audit_sink` | `log_id`, `operation`, `model`, `res_id`, `uid`, `sudo`, nama field, `note` — **tanpa nilai lama/baru** | **400 h** (`retention_stream`) | T (mTLS), L | Hanya model kontrol + purge. `uid` = data pribadi karyawan; `res_id` partner = pengenal tidak langsung. Di luar kendali admin Odoo, **bukan WORM** |
| Log `efn-deploy` (journald host) | Nama user ops | journald default | L | Belum dikirim ke Loki (G11.8) |
| Prometheus | Tidak | 15–30 h | L | — |
| Metabase | Email user internal; hasil query | Selama dipakai | L, W | Hanya via WireGuard |

### 2.7 Klien & pihak ketiga
| Pihak | Data | Peran | Lokasi | Catatan |
|---|---|---|---|---|
| Browser/app pelanggan | Cookie `efn_session` (JWT, `sub` = UUID), `efn_stepup`, `efn_dsar_receipt` (opak, HttpOnly, 30 h) | — | Perangkat | Tidak memuat HP (ADR-029) |
| Meta (WhatsApp Cloud API) | HP, isi template | Pemroses | **Lintas negara** | `PDP_TRANSFER_LINTAS_NEGARA.md` (Q-T1..T6) |
| Cloudflare | IP, header, URL, isi request | Pemroses | **Lintas negara** | idem |
| Payment gateway (vendor belum ditentukan, ADR-026) | `order_ref`, nominal; identitas hanya bila vendor mewajibkan | Pemroses/pengendali | Verifikasi saat pemilihan (Q-T4) | Tanpa PAN |
| 3PL (fase 3) | Nama, HP, alamat | Pemroses / pengendali terpisah | Indonesia | |
| Vendor VPS (ADR-004) | Semua (terenkripsi LUKS) | Pemroses infrastruktur | Indonesia (syarat) | DPA wajib |
| GitHub | Kode & data sintetis; artefak smoke nightly berisi data fiktif | — | Lintas negara | Larang data produksi di fixture/log CI |
| ALTCHA | Tidak ada pihak ketiga | — | — | ADR-028 |

## 3. Matriks retensi ringkas (default → ditetapkan DPO)
| Data | Default | Mekanisme (pemilik) | Status |
|---|---|---|---|
| `otp_audit` | 90 h | Purge BFF | Jalan (kode + test) |
| Foto object storage | 90 h | ILM SILO | Jalan (runtime G9) |
| `price_hunter_report.user_enc` | 90 h → NULL | Job BFF | Jalan |
| Baris `price_hunter_report` | 730 h | Job BFF | Jalan |
| `outbox` / `processed_message` | 14 / 14 h | Purge BFF | Jalan (v2 menulis 7/30 — dikoreksi) |
| ClickHouse mart | 5 th | `TTL` (data-ai) | Jalan |
| Ledger poin & `payment_intent` | 10 th | Tidak dipurge; PII dikosongkan / tombstone saat erasure | — |
| Dokumen pajak Odoo | 10 th | Partner dianonimkan | — |
| Consent | Akun + 5 th | — | **Belum ada job** |
| `dsar_request` | 5 th | — | **Belum ada job** |
| `efn.audit.log` | 730 h (min 365, kontrol/purge tanpa batas) | Autovacuum Odoo | Jalan; Q-P9 |
| Salinan audit ClickHouse | ≥ Odoo (usulan) | ETL append-only | Sedang dibangun |
| Loki umum / `efn_audit_sink` | 30 h / 400 h | Compactor Loki | Jalan (runtime G11) |
| Backup | 14 full + WAL; lock 15 h; noncurrent 35 h | WAL-G + ILM | Jalan |

## 4. Pertanyaan untuk Legal/DPO (belum diputuskan klien)
- **Q-P1** Penetapan DPO dan kontak publik.
- **Q-P2** Retensi 10 tahun dokumen transaksi; apakah ledger poin & `payment_intent` termasuk.
- **Q-P3** Batas usia pengguna.
- **Q-P4** Transfer lintas negara + DPIA Price Hunter & Text-to-SQL (`PDP_TRANSFER_LINTAS_NEGARA.md`).
- **Q-P5** Poin/riwayat transaksi = data keuangan pribadi (spesifik)?
- **Q-P6** Tombstone pseudonim di ledger setelah hapus akun — kini acak/tidak dapat ditautkan (SEC-31); cukup?
- **Q-P7** Retensi pseudonim di ClickHouse: 24 bulan (kolom) vs 5 tahun (mart).
- **Q-P8** Lokasi Price Hunter: NULL setelah 90 hari atau ikut baris 2 tahun?
- **Q-P9 (baru)** Retensi log audit: (a) `efn.audit.log` 730 h default, minimum 365 h (= Q-6/Q-49 docs/01); (b) log kontrol & purge disimpan tanpa batas atau dibatasi (usulan 5 th); (c) salinan ClickHouse append-only: simpan baris pribadi (bertentangan dengan erasure) atau hanya non-pribadi.
- **Q-P10 (baru)** Siapa anggota grup **Auditor Data Pribadi Lintas PT** (default: tidak ada). Usulan: DPO + 1 auditor internal holding, ditinjau tahunan.
- **Q-P11 (baru, SEC-38)** Akun tidak aktif / nomor HP daur ulang operator: anonimisasi otomatis setelah N bulan tanpa login/transaksi (usulan 24 bulan) atau batasi ekspor DSAR ke data setelah login pertama di perangkat baru? Tanpa keputusan: risiko residu tercatat.

## 5. Yang belum terverifikasi
Nomor pasal UU PDP (wajib Legal). Salinan audit ClickHouse (desain dari spesifikasi odoo-engineer G11, kode data-AI belum ditinjau). Sink audit dengan purge via server action nyata (hanya baris sintetis). LUKS & `log_parameter_max_length=0` produksi (belum ada staging). Field `res.partner` dari Odoo CE 19 (`mobile` tidak ada di 19.0 base, menurut odoo-engineer G9). Lokasi pemrosesan vendor payment.
