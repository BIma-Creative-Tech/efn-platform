# Inventaris Data Pribadi (UU PDP No. 27/2022) — EFN Platform

- **Status:** Draf teknis untuk ditinjau **DPO + Legal** (belum ditetapkan). **v2 (2026-10-06): diselaraskan dengan `docs/04 §8`** (tabel `member`, `consent`, `dsar_request`, `payment_intent`; penghapusan Odoo via pull; ClickHouse via ETL; retensi default). Menutup sebagian SEC-11 / SEC-24 / R-L01.
- **Tanggal:** 2026-10-05 · revisi 2026-10-06 · Penyusun: efn-architect
- **Keputusan klien 6 Okt 2026:** kebijakan UU PDP (penetapan DPO, retensi, transfer ke Meta/Cloudflare) **belum diputuskan** — angka retensi di bawah adalah **default usulan yang dipakai sementara**, semuanya configurable.
- **Terkait:** ADR-022 (enkripsi), ADR-026 (payment), ADR-028 (ALTCHA), ADR-029 (member opak), `docs/04 §1, §7, §8`, `PDP_DSAR_DESIGN.md`, `PDP_CONSENT.md`, `PDP_BREACH_RESPONSE.md`, `PDP_TRANSFER_LINTAS_NEGARA.md`

> **Ringkasan manajemen.** Tabel di bawah mencatat setiap data pribadi yang diproses platform: apa, di mana disimpan, untuk apa, berapa lama, dan apakah terenkripsi. Data paling sensitif (nomor HP, lokasi, foto, IP) dibatasi dan dienkripsi; token login tidak lagi memuat nomor HP. Masa simpan di sini **usulan teknis, menunggu DPO**; dokumen transaksi pajak 10 tahun menunggu konfirmasi Finance/Legal. `member_hash`/`user_hash` tetap **data pribadi (pseudonim)**, bukan anonim, karena dapat dicocokkan oleh pemegang pepper.

## 1. Kategori data & dasar pemrosesan (usulan — verifikasi Legal)
| Kategori | Contoh | Dasar pemrosesan usulan | Spesifik? |
|---|---|---|---|
| Identitas kontak member | Nomor HP (E.164) | Pelaksanaan perjanjian (akun, poin, OTP) | Tidak |
| Identitas pelanggan Odoo | Nama, HP, email, alamat kirim | Perjanjian (transaksi, pengiriman) + kewajiban hukum (faktur pajak) | Tidak |
| Lokasi | Koordinat laporan Price Hunter (dibulatkan di server) | **Persetujuan** (`consent` purpose `location`) | Tidak |
| Foto | Foto label harga (dapat memuat orang lain tak sengaja) | Perjanjian (program Price Hunter) + minimisasi (strip EXIF) | Tidak |
| Data teknis | Alamat IP, user-agent | Kepentingan sah (keamanan, anti-fraud) | Tidak |
| Pseudonim | `member_hash`, `user_hash`, `efn_member_hash` (Odoo) | Mengikuti data asalnya | Tidak |
| Identitas opak | `member.id` (UUID, JWT `sub`) | Mengikuti akun | Tidak |
| Transaksi, pembayaran & poin | Order, payment intent, saldo/riwayat poin | Perjanjian + kewajiban hukum (akuntansi) | **Cek "data keuangan pribadi"** (Q-P5) |
| Karyawan (Odoo) | Nama, email kerja, PIN POS, log login | Perjanjian kerja | Tidak |
| Marketing | Consent marketing | **Persetujuan** (`consent` purpose `marketing`) | Tidak |

Data spesifik (kesehatan, biometrik, anak) tidak dikumpulkan dengan sengaja. Batas usia: Q-P3.

## 2. Inventaris per lokasi penyimpanan
Legenda: **L** = LUKS, **K** = kolom AES-256-GCM aplikasi, **S** = MinIO SSE-S3, **W** = WAL-G libsodium, **C** = ClickHouse encrypted disk, **T** = TLS in transit, **H** = hash/pseudonim, **M** = mask. Retensi = **default usulan, menunggu DPO** (configurable).

### 2.1 PostgreSQL `efn_platform` (pemilik: BFF)
| Tabel.kolom | Data pribadi | Tujuan | Retensi default | Enkripsi | Sumber kontrak / catatan |
|---|---|---|---|---|---|
| `member.id` | Identitas opak (UUID) | JWT `sub`, relasi consent/DSAR | Selama akun; baris dianonimkan saat hapus (`deleted_at`) | L, W | §8.2, ADR-029 |
| `member.member_hash` | Pseudonim HP | Lookup login, relasi ledger | Selama akun | H, L, W | §8.2 |
| `member.member_ref_enc` | HP | OTP/notifikasi, DSAR | Sampai akun dihapus → dikosongkan | K, L, W | §8.2 (perlu NULLABLE untuk akun terhapus — catatan lead) |
| `price_hunter_report.user_hash` | Pseudonim HP | Cap harian, dedup, poin | Ikut baris (**2 tahun**) | H, L, W | §7.2, §8.4 |
| `price_hunter_report.user_enc` | HP | Notifikasi hasil | **NULL setelah 90 hari** atau saat hapus akun | K, L, W | §8.4 |
| `price_hunter_report.geo_*` | Lokasi (dibulatkan 3 desimal di server) | Validasi & peta harga | Ikut baris (2 tahun) — **usulan: NULL setelah 90 hari** bersama `user_enc` (keputusan DPO) | L, W | Dibuang bila consent `location` tidak aktif (§8.4) |
| `price_hunter_report.image_object/image_phash` | Referensi foto | Validasi, dedup | Objek 90 hari; referensi ikut baris | L, W | |
| Baris `price_hunter_report` (non-pribadi) | — | Analitik harga | **2 tahun** | L | §8.4 |
| `loyalty_ledger.member_ref_hash` | Pseudonim | Saldo poin, liabilitas per PT | **10 tahun** (akuntansi, Q-P2) | H, L, W | Usulan tombstone setelah hapus akun — Q-P6 |
| `loyalty_ledger.member_ref_enc` | HP | Notifikasi, DSAR | Sampai akun dihapus → NULL | K, L, W | §8.4 (NULLABLE hanya untuk baris terhapus) |
| `otp_audit.subject` / `ip_enc` | Pseudonim / IP | Audit keamanan, forensik | **90 hari** | H/K, L | §8.4 |
| `consent` (append-only) | `member_id`, purpose, versi, `ip_enc` | Bukti dasar pemrosesan | **Selama akun + 5 tahun**; saat hapus akun dipertahankan tanpa PII | K (ip), L, W | §8.4; grant INSERT/SELECT saja |
| `dsar_request` | `member_id`, `member_hash`, jenis & status | Bukti pemenuhan hak | 5 tahun (usulan, belum di kontrak) | H, L, W | §8.4 |
| `payment_intent` | `order_ref`, `company_code`, nominal, `provider_ref`, `qr_string` — **tanpa** nama/HP/data kartu | Pembayaran QRIS POS & online | Ikut kewajiban akuntansi (10 tahun, Q-P2) | L, W | §8.3, ADR-026; tidak ada PAN (SAQ A) |
| `outbox.payload` | Hanya hash (event v2) | Pengiriman event | Published 7 hari | L | |
| `processed_message` | Tidak | Idempotensi | 30 hari | L | |

### 2.2 PostgreSQL `efn_odoo` (pemilik: Odoo)
| Model.field | Data pribadi | Tujuan | Retensi default | Enkripsi | Catatan |
|---|---|---|---|---|---|
| `res.partner` name, phone, mobile, email, street, vat | Identitas pelanggan/supplier | Transaksi, faktur, pengiriman | Selama hubungan; **anonimisasi** saat hapus akun (pull `GET /v1/pos/privacy/erasures`) kecuali faktur NPWP wajib utuh | L, W, T | Tidak dienkripsi per kolom (ADR-022 K5) |
| `res.partner.efn_member_hash` (baru, stored, indexed) | Pseudonim HP | Mencocokkan permintaan hapus tanpa HP mentah | Dikosongkan saat anonimisasi | H, L, W | §8.4; Odoo memegang `member_hash_pepper` |
| `pos.order.partner_id`, `sale.order.partner_*` | Relasi ke pelanggan | Transaksi | 10 tahun (Q-P2) | L, W | Partner dianonimkan |
| `account.move` partner, NPWP/NIK | Identitas pajak | Kewajiban pajak (Coretax) | 10 tahun | L, W | |
| `efn_otp_request` / challenge POS | Pseudonim/HP termask | Gate OTP promo | 30 hari | L | |
| `res.users`, `hr.employee` (PIN POS), `res.users.log`, `auth_totp.device` | Karyawan | Akses ERP, keamanan | Selama kerja / log 1 tahun | L, W | 2FA (ADR-023) |
| `mail.message`, `ir.attachment` | Data bebas | Kolaborasi | Pembersihan tahunan (usulan) | L, W | Edukasi: jangan unggah KTP ke chatter |

### 2.3 Object storage MinIO
| Bucket/prefix | Data pribadi | Retensi default | Enkripsi | Catatan |
|---|---|---|---|---|
| `market-scout/reports/...` | Foto (EXIF dibuang) | **90 hari** (§8.4) | S, L, T | Lifecycle **sudah ada tetapi 365 hari** (`infra/minio/minio-init.sh`) → devsecops ubah ke 90 hari / configurable. Dihapus segera saat hapus akun |
| `pg-backup/efn/*` | Semua isi PostgreSQL | 14 full + WAL; object lock governance | W, S, L | Data terhapus via DSAR tetap ada di backup sampai kedaluwarsa (dijelaskan di kebijakan privasi) |

### 2.4 ClickHouse `efn_dwh`
| Tabel.kolom | Data pribadi | Retensi default | Enkripsi | Penghapusan |
|---|---|---|---|---|
| `competitor_prices.user_hash`, geo (dibulatkan) | Pseudonim, lokasi | Mart TTL **5 tahun** (§8.4) | C, L | ETL `DELETE` per `member_hash` dari `dsar_request` |
| `loyalty_events.member_hash` | Pseudonim | TTL 5 tahun | C, L | Sama |
| `fact_sales_line.customer_id` | Pseudonim (id partner) | TTL 5 tahun | C, L | Partner Odoo dianonimkan di sumber |

Catatan: inventaris v1 mengusulkan TTL 24 bulan untuk kolom pseudonim; kontrak v3 memakai 5 tahun untuk mart. Usulan ke DPO: pseudonim 24 bulan (kolom TTL), agregat 5 tahun (Q-P7).

### 2.5 Cache, antrean, log
| Komponen | Data pribadi | Retensi | Enkripsi | Catatan |
|---|---|---|---|---|
| Redis `otp:<challenge_id>` | `member_hash`, `order_ref`, HMAC kode; **challenge login masih memuat HP plaintext (SEC-24)** | TTL 180 s; AOF persisten | T, L | Rekomendasi SEC-24: enkripsi `m` atau AOF off |
| Redis `rl:*` / `otp:login:ip:<ip>` | **IP di nama key** (SEC-24) | ≤ 15 menit | T, L | Usulan HMAC IP |
| Redis `altcha:used:<sig>` | Tidak | 5 menit | T, L | ADR-028 |
| Redis `dtok:used:<jti>` | Tidak | TTL token | T, L | |
| RabbitMQ (`ocr.requested`, `loyalty.earn.requested`, `notify.push`, `privacy.erasure.completed` + DLQ) | Hash saja (+ geo di `ocr.requested`) | Sampai dikonsumsi; DLQ ≤ 7 hari | T, L | |
| Loki | HP **di-mask**, IP klien di access log Traefik | 30 hari | T, L | |
| Prometheus | Tidak | 15–30 hari | L | |
| Metabase | Email user internal; hasil query | Selama dipakai | L, W | Hanya via WireGuard |

### 2.6 Klien & pihak ketiga
| Pihak | Data | Peran | Lokasi | Catatan |
|---|---|---|---|---|
| Browser/app pelanggan | Cookie `efn_session` (JWT, `sub` = UUID member) | — | Perangkat | HttpOnly, Secure; tidak memuat HP (ADR-029) |
| Meta (WhatsApp Cloud API) | HP, isi template | Pemroses | **Lintas negara** | `PDP_TRANSFER_LINTAS_NEGARA.md` |
| Cloudflare | IP, header, URL, isi request (TLS diterminasi di CF) | Pemroses | **Lintas negara** | `PDP_TRANSFER_LINTAS_NEGARA.md` |
| Payment gateway (vendor **belum ditentukan**, ADR-026) | `order_ref`, nominal; nama/HP/email hanya bila vendor mewajibkan | Pemroses/pengendali (kontrak) | Verifikasi saat pemilihan | Tidak ada PAN |
| 3PL (fase 3) | Nama, HP, alamat | Pemroses / pengendali terpisah | Indonesia | |
| Vendor VPS (ADR-004) | Semua data (terenkripsi LUKS) | Pemroses infrastruktur | Indonesia (syarat) | DPA wajib |
| GitHub | **Kode & data sintetis saja** — bukan data pelanggan | — | Lintas negara | Larang data produksi di fixture/log CI |
| ALTCHA | Tidak ada pihak ketiga (self-hosted) | — | — | ADR-028 |

## 3. Matriks retensi ringkas (default usulan → ditetapkan DPO)
| Data | Default | Env/mekanisme (pemilik) |
|---|---|---|
| `otp_audit` | 90 hari | Purge BFF |
| Foto MinIO | 90 hari | ILM MinIO (devsecops — ubah dari 365) |
| `price_hunter_report.user_enc` | 90 hari → NULL | Job BFF |
| Baris `price_hunter_report` | 2 tahun | Job BFF |
| `outbox` / `processed_message` | 7 / 30 hari | Purge BFF |
| ClickHouse mart | 5 tahun | `TTL` (data-ai) |
| Ledger poin & `payment_intent` | 10 tahun | Tidak dipurge; PII dikosongkan saat hapus akun |
| Dokumen pajak Odoo | 10 tahun | Partner dianonimkan |
| Consent | Akun + 5 tahun | — |
| `dsar_request` | 5 tahun | — |
| Log Loki | 30 hari | Loki |
| Backup | 14 full + WAL | WAL-G |

## 4. Pertanyaan untuk Legal/DPO (belum diputuskan klien)
- **Q-P1** Penetapan DPO dan kontak publik.
- **Q-P2** Retensi 10 tahun dokumen transaksi; apakah ledger poin & `payment_intent` termasuk.
- **Q-P3** Batas usia pengguna.
- **Q-P4** Transfer lintas negara (Meta, Cloudflare, payment gateway) — lihat `PDP_TRANSFER_LINTAS_NEGARA.md`; DPIA Price Hunter & Text-to-SQL.
- **Q-P5** Poin/riwayat transaksi = data keuangan pribadi (spesifik)?
- **Q-P6** Tombstone pseudonim di ledger setelah hapus akun (`PDP_DSAR_DESIGN.md` §7).
- **Q-P7** Retensi pseudonim di ClickHouse: 24 bulan (kolom) vs 5 tahun (mart).
- **Q-P8** Lokasi Price Hunter: NULL setelah 90 hari atau ikut baris 2 tahun?

## 5. Yang belum terverifikasi
Nomor pasal UU PDP (wajib Legal). Field Odoo dari pengetahuan model CE + `docs/08`; `efn_member_hash` belum diimplementasi. Lokasi pemrosesan vendor payment (belum dipilih).
