# Skenario UAT — Fase 0 (PoC "Kitting & OTP", Price Hunter, Login Super-App, Backup)

Status: **Draft** (efn-qa-engineer, 2026-10-05; bagian G ditambah 2026-10-06 untuk kontrak v3 §8). Acuan kontrak: `docs/04_SERVICE_CONTRACTS.md`. ADR terkait (semua *Proposed*): ADR-017 (OTP saat POS offline), ADR-018 (kitting Manufacture BoM), ADR-006/007/008 (Price Hunter), ADR-020 (ledger poin), ADR-001/004 (backup, RPO/RTO).

Dijalankan di **staging** dengan `WHATSAPP_ENABLED=true` + nomor uji Meta (atau `false` + log console BFF untuk melihat kode OTP, hanya di staging). Setiap skenario dicatat: tanggal, penguji, hasil (Lulus/Gagal), bukti (screenshot / ID order / `report_id` / potongan log yang sudah di-mask).

**Data uji umum**
- Company `PT F&B` dengan `efn_brand_code = FNB`, POS config "Outlet Uji" dengan sesi terbuka, kasir grup *Point of Sale / User*.
- Program loyalty "Promo Member 10%" (`efn_requires_otp = True`, POS), program "Promo Bebas" (`efn_requires_otp = False`).
- Parameter: `efn.pos.otp_required_for_promo = True`, `efn.pos.promo_disabled_when_offline = True`, `efn.pos.otp_timeout_seconds = 5`.
- Nomor HP uji `0812-3456-7890` (member_ref `6281234567890`) terhubung ke WhatsApp penguji. **Jangan** memakai nomor pelanggan nyata.

Konvensi: setiap log yang diperiksa **tidak boleh** memuat nomor HP utuh (harus `62812****890`), kode OTP, atau token.

---

## A. OTP promo POS (docs/04 §3.1, ADR-017)

### A1. OTP sukses — promo diterapkan dan token dipakai sekali
- **Given** sesi POS terbuka, keranjang Rp125.000, kasir memilih reward "Promo Member 10%"
- **When** kasir **memilih pelanggan** ber-HP `0812-3456-7890` di order (v2/SEC-07: nomor diambil server Odoo dari `res.partner`, kasir tidak mengetik nomor), pelanggan menerima WA berisi 6 digit, kasir memasukkan kode benar, lalu memvalidasi pembayaran
- **Then** popup menampilkan nomor ter-mask `62812****890` (bukan nomor utuh)
- **And** reward diskon muncul di order; pembayaran sesuai total setelah diskon
- **And** setelah sinkron, order berstatus *Paid* dengan "Status OTP Promo" = *Token promo dipakai*
- **And** BFF mencatat `platform.otp_audit` outcome `sent` lalu `verified` dengan `subject` berupa hash 64 hex (bukan nomor)
- **And** Redis berisi `dtok:used:<jti>` bernilai `order_ref` = `pos.order.uuid`

### A2. OTP salah 3x — terkunci
- **Given** OTP telah diminta untuk order baru
- **When** kasir memasukkan kode salah ke-1 dan ke-2
- **Then** POS menampilkan "Kode OTP salah." dan sisa percobaan 2 lalu 1 (`attempts_left`)
- **When** kasir memasukkan kode salah ke-3
- **Then** POS menampilkan "Terlalu banyak percobaan. Minta OTP baru." (`reason = locked`), reward tidak bisa diterapkan
- **And** memasukkan kode **benar** setelah terkunci tetap ditolak (`locked`)
- **And** meminta OTP baru untuk nomor yang sama berhasil selama belum melewati batas 3 permintaan / 15 menit

### A3. OTP kedaluwarsa
- **Given** OTP diminta
- **When** kasir memasukkan kode benar setelah > 180 detik
- **Then** POS menampilkan "Kode OTP kedaluwarsa. Minta OTP baru." (`reason = expired`)

### A4. Rate limit permintaan OTP
- **Given** nomor yang sama sudah meminta OTP 3 kali dalam 15 menit (dari POS mana pun)
- **When** kasir meminta OTP ke-4
- **Then** POS menampilkan "Terlalu banyak permintaan OTP. Coba lagi nanti." (BFF `429`), tidak ada WA terkirim
- **And** respons tidak membedakan nomor terdaftar/tidak terdaftar

### A5. POS offline — promo ber-OTP dinonaktifkan
- **Given** POS kehilangan koneksi ke server Odoo (cabut jaringan) sebelum memilih reward
- **When** kasir membuka daftar Reward
- **Then** "Promo Member 10%" tidak tersedia / tombol OTP menolak dengan pesan offline; "Promo Bebas" tetap bisa dipakai
- **And** transaksi tanpa promo ber-OTP tetap bisa dibayar dan tersimpan lokal
- **When** koneksi pulih
- **Then** order tersinkron tanpa reward ber-OTP dan tanpa error

### A6. POS offline setelah OTP terverifikasi (ADR-017 aturan #2)
- **Given** OTP terverifikasi untuk order X, lalu POS offline sebelum sinkron
- **When** pembayaran divalidasi offline (bila diizinkan konfigurasi) dan POS online kembali > 10 menit kemudian (token kedaluwarsa)
- **Then** order X **tetap tersimpan** (tidak dibatalkan), "Status OTP Promo" = *Perlu review supervisor*, catatan `expired`
- **And** order muncul di menu *Point of Sale → Orders → Review OTP Promo*

### A7. BFF timeout / mati saat request atau verify
- **Given** container `api-gateway` dihentikan (atau latensi > `efn.pos.otp_timeout_seconds`)
- **When** kasir meminta atau memverifikasi OTP
- **Then** dalam ≤ 5 detik POS menampilkan "Layanan OTP tidak tersedia. Lanjutkan tanpa promo."
- **And** transaksi tanpa promo tetap bisa diselesaikan (POS tidak hang)
- **And** log Odoo memuat `bff_timeout`/`bff_unreachable` dengan nomor ter-mask, tanpa API key

### A8. BFF mati saat sinkron (consume)
- **Given** OTP terverifikasi untuk order Y, lalu `api-gateway` dihentikan
- **When** pembayaran divalidasi dan order disinkron
- **Then** order Y tersimpan *Paid* dan ditandai *Perlu review supervisor* dengan catatan `bff_unreachable`/`bff_timeout`/`bff_server_error`

### A9. Token replay / dipakai dua kali
- **Given** order A sudah sukses consume token T
- **When** token T dikirim ulang ke `POST /v1/pos/discount-token/consume` dengan `order_ref` A (retry Odoo)
- **Then** BFF menjawab `200 { ok: true }` (idempoten untuk order yang sama)
- **When** token T dikirim dengan `order_ref` B
- **Then** BFF menjawab `409 { ok: false, reason: "already_used" }`
- **And** (lewat Odoo) order B yang membawa reward ber-OTP tanpa OTP terverifikasinya sendiri **ditolak** saat sinkron ("Promo pada order ini memerlukan verifikasi OTP pelanggan")

### A10. OTP untuk order berbeda
- **Given** OTP terverifikasi untuk order A (uuid A)
- **When** kasir membuat order B dan menerapkan reward ber-OTP tanpa verifikasi untuk B
- **Then** pembayaran B diblokir di frontend; bila dipaksa (klien dimodifikasi) sinkron B ditolak server
- **And** pemanggilan langsung `consume` dengan token A + `order_ref` B → `409 order_mismatch`

### A11. Akses langsung endpoint POS dari internet
- **When** penguji memanggil `https://api.<domain>/v1/pos/otp/request` dari luar
- **Then** Traefik menjawab 404 (router publik mengecualikan `/v1/pos`, `/internal`, `/metrics`)
- **And** pemanggilan internal tanpa header `X-EFN-Service-Key` → `401`

### A12. Pelanggan diganti setelah OTP — `member_mismatch` (SEC-07, docs/04 §3.1 v2)
- **Given** OTP terverifikasi untuk order Z dengan pelanggan A (`6281234567890`)
- **When** kasir mengganti pelanggan order Z menjadi B (`6281298765432`) lalu memvalidasi pembayaran
- **Then** Odoo mengirim consume dengan `member_ref` = nomor B; BFF menjawab `409 { ok:false, reason:"member_mismatch" }`
- **And** order Z tetap tersimpan *Paid*, "Status OTP Promo" = *Perlu review supervisor*, catatan `member_mismatch`, nomor di catatan ter-mask
- **And** token **tidak terbakar** (Redis tidak berisi `dtok:used:<jti>`); consume ulang dengan pelanggan A untuk order Z → `200`

### A13. Pelanggan tanpa HP Indonesia
- **Given** order dengan pelanggan tanpa nomor HP, atau nomor `+65 9123 4567`
- **When** kasir meminta OTP promo
- **Then** POS menampilkan pesan nomor pelanggan tidak valid/tidak ada (`no_phone` / `foreign_phone`); tidak ada panggilan ke BFF / WA
- **And** pemanggilan langsung BFF `POST /v1/pos/otp/request` dengan `member_ref` `659123…` → `400 { error: { code: "invalid_phone" } }`
- **And** order final yang pelanggannya dihapus setelah OTP → review `member_missing:no_phone`, tanpa consume

---

## B. Kitting Central Kitchen & penjualan combo (ADR-018)

### B1. MO paket mengurangi bahan mentah
- **Given** company CK, stok Ayam Utuh 20 kg, Tepung Bumbu 5 kg, BoM *Manufacture* "Paket Ayam Geprek" (0,2 kg ayam + 0,05 kg tepung per Porsi)
- **When** MO 100 Porsi dibuat → Confirm → Produce All → Done
- **Then** stok Ayam 0 kg, Tepung 0 kg, Paket Ayam Geprek 100 Porsi; jurnal stok (valuation) tercatat untuk bahan dan paket

### B2. Penjualan menu combo memotong paket, bukan bahan mentah
- **Given** stok Paket Ayam Geprek 100, Paket Nasi 100, Es Teh 100; menu "Menu Ayam Geprek Komplit" = BoM *Kit*, *Available in POS*
- **When** kasir menjual 3 menu di POS dan sesi ditutup
- **Then** stok Paket Ayam Geprek, Paket Nasi, Es Teh masing-masing berkurang 3
- **And** stok Ayam Utuh dan Tepung **tidak** berubah

### B3. Larangan Phantom untuk paket
- **When** pengguna mengubah BoM "Paket Ayam Geprek" menjadi tipe Kit
- **Then** sistem menolak dengan pesan validasi (paket CK wajib Manufacture)

### B4. Paket wajib storable
- **When** pengguna menandai produk consumable sebagai paket kitting
- **Then** sistem menolak

### B5. MO otomatis dari reorder rule
- **Given** reorder rule paket min 50 / max 200 di lokasi CK, `route_id` kosong (Odoo 19: route Manufacture adalah route gudang; warehouse CK *Manufacture to Resupply* aktif)
- **When** stok paket turun ke 40 dan scheduler dijalankan (*Order Once*)
- **Then** MO draft/confirmed 160 Porsi terbentuk dari BoM Manufacture

---

## C. Price Hunter / Market Scout (docs/04 §3.3, §4; ADR-006/007/008)

### C1. Laporan valid → poin & notifikasi
- **Given** pelanggan login di Super-App, cap harian belum tercapai
- **When** pelanggan mengunggah foto label harga jelas "Minyak Goreng 2L Rp34.500" dengan harga 34500
- **Then** layar menampilkan "sedang divalidasi" dan `report_id` (UUID); BFF menjawab `202 queued`
- **And** dalam ≤ 3 menit status menjadi `valid`, `points_awarded = 10` (placeholder Q-13)
- **And** pelanggan menerima WA template `efn_price_hunter_valid` berisi **nama produk** dan poin (bukan `-`)
- **And** `platform.loyalty_ledger` berisi satu entri `idempotency_key = price_hunter:<report_id>`, `company_code = EFN`
- **And** setelah ETL (≤ 15 menit) `efn_dwh.competitor_prices` memuat baris dengan `user_hash` = `loyalty_events.member_hash` member yang sama, tanpa nomor HP

### C2. Foto duplikat
- **Given** foto F sudah dilaporkan (status akhir apa pun) dalam 30 hari
- **When** pelanggan (sama atau lain) mengunggah foto F lagi (atau versi crop/kompres ringan)
- **Then** laporan kedua berstatus `flagged` (duplikat pHash), tanpa poin dan tanpa notifikasi; muncul untuk review manual

### C3. Cap harian
- **Given** pelanggan sudah mengirim 20 laporan hari ini (WIB)
- **When** mengirim laporan ke-21
- **Then** BFF menjawab `429` dan UI menampilkan pesan batas harian; tidak ada objek MinIO baru
- **And** setelah 00:00 WIB pelanggan bisa mengirim lagi

### C4. Ollama / VLM mati
- **Given** container `ollama` dihentikan, OCR tahap 1 confidence rendah
- **When** pelanggan mengirim laporan
- **Then** upload tetap `202` (AI di luar jalur transaksi, ADR-010)
- **And** laporan berakhir `flagged` dengan reason `vlm_unavailable` (bukan macet di `processing`), tanpa poin
- **And** POS, checkout, dan login tidak terpengaruh

### C5. File bukan gambar / terlalu besar
- **When** pelanggan mengunggah PDF yang di-rename `.jpg` → **Then** `415`
- **When** mengunggah foto > 8 MB → **Then** `413`, tidak ada objek MinIO dan baris DB

### C6. Status laporan milik orang lain
- **When** pelanggan B membuka `/pantau-harga/<report_id milik A>`
- **Then** halaman 404 (tidak membocorkan keberadaan laporan)

### C7. Pesan ganda / worker crash
- **Given** worker dihentikan paksa saat memproses laporan R
- **When** worker dijalankan lagi
- **Then** laporan R selesai sekali; ledger hanya punya satu entri `price_hunter:R`; WA hanya terkirim sekali

---

## D. Login OTP Super-App (docs/04 §3.2)

### D1. Login sukses
- **When** pelanggan memasukkan `08123456789x`, menerima OTP, memasukkan kode benar
- **Then** cookie `efn_session` HttpOnly + Secure + SameSite=Lax terpasang; token **tidak** tampak di respons browser/JS
- **And** `/akun` menampilkan nomor ter-mask dan saldo poin dari ledger

### D2. Nomor tidak terdaftar
- **When** pelanggan memasukkan nomor yang belum pernah bertransaksi
- **Then** respons dan waktu respons sama dengan nomor terdaftar (`202`), tidak ada indikasi status keanggotaan

### D3. Salah 3x / kedaluwarsa / rate limit
- Sama seperti A2–A4 tetapi lewat `/masuk`; tombol kirim ulang aktif setelah 60 detik

### D4. Sesi habis
- **Given** token berumur > 1 jam
- **When** pelanggan membuka `/akun` atau mengunggah laporan
- **Then** diarahkan ke `/masuk?expired=1`, cookie dihapus

### D5. CSRF dasar
- **When** form POST `/api/auth/otp/request` dikirim dari origin lain
- **Then** `403`

### D6. Nomor luar negeri ditolak (SEC-06)
- **When** pelanggan memasukkan `+65 9123 4567` di `/masuk`
- **Then** UI menampilkan "Nomor HP tidak valid" (validasi client); tidak ada WA terkirim
- **And** bila validasi client dilewati (POST langsung `/api/auth/otp/request` dengan nomor yang lolos Next.js tetapi ditolak BFF), Route Handler meneruskan `400 invalid_phone` (bukan `502`) dan UI menampilkan pesan nomor tidak valid

---

## E. Backup & restore test (R-T08, runbook `backup-restore.md`)

### E1. Backup harian
- **Given** timer `efn-backup.timer` aktif
- **When** jadwal 01:30 WIB lewat
- **Then** `efn-walg backup-list` menampilkan base backup baru; metrik `efn_backup_last_success_timestamp_seconds` < 26 jam; tidak ada alert `EfnBackup*`

### E2. WAL archiving (RPO 15 menit)
- **When** transaksi POS dibuat lalu ditunggu 15 menit
- **Then** segmen WAL terkait ada di `pg-backup/efn/wal_005/`; alert `EfnWalArchiveFailing` tidak aktif

### E3. Restore test bulanan
- **When** `sudo systemctl start efn-restore-test.service`
- **Then** skrip exit 0, durasi `efn_restore_test_duration_seconds` < 4 jam (RTO)
- **And** DB `efn_odoo`, `efn_platform`, `metabase` ada; `res_company` ≥ 1; row count `pos_order` dan `platform.loyalty_ledger` sama dengan produksi pada titik waktu restore (± transaksi setelah WAL terakhir)
- **And** container & volume sementara terhapus; repo backup produksi tidak ditulis (archive_mode=off)

### E4. Restore gagal terdeteksi
- **Given** secret `walg_libsodium_key` sengaja salah di lingkungan uji
- **When** restore test dijalankan
- **Then** skrip exit non-zero dan alert `EfnRestoreTestFailed` (atau setara) aktif

### E5. Kunci enkripsi
- **Then** salinan offline `walg_libsodium_key` tersedia di brankas dan dapat dipakai membuka backup (uji di E3 memakai salinan tersebut minimal sekali per kuartal)

---

## F. Enkripsi di setiap transaksi data & akses (docs/04 §7, ADR-022, ADR-023, ADR-024)

Dijalankan di **staging** (PKI staging, bukan dev). Alat: `openssl s_client`, `psql`, `redis-cli`, `curl`, dijalankan dari container uji sementara di network yang relevan (`docker run --rm --network efn_data …`), **bukan** dari container aplikasi. Hasil setiap langkah "ditolak" harus berupa kegagalan koneksi/handshake, bukan data.

### F1. Koneksi plaintext ditolak di setiap hop
- **When** penguji mencoba koneksi tanpa TLS:
  - `psql "host=postgres user=efn_bff dbname=efn_platform sslmode=disable"`
  - `redis-cli -h redis -p 6379 ping` (tanpa `--tls`)
  - AMQP `amqp://efn-bff@rabbitmq:5672` dan `:5671` tanpa TLS
  - `curl http://minio:9000/minio/health/live`, `curl http://clickhouse:8123/ping`, `clickhouse-client --port 9000`
  - `curl http://api-gateway:3000/health`, `curl http://odoo:8069/web/health` (dari container lain), `curl http://superapp-web:3001/`
- **Then** semua gagal: PostgreSQL `no pg_hba.conf entry … no encryption` (`hostnossl … reject`); Redis/AMQP/HTTP memutus koneksi atau port tertutup (8069/3001/11435 hanya loopback, 5672/8123/9000 tidak ada listener)
- **And** `scripts/check-encryption.sh` di CI hijau untuk compose dev & prod (tidak ada URL plaintext, `sslmode` lemah, `EFN_ALLOW_PLAINTEXT`)

### F2. Sertifikat salah / CA asing ditolak
- **Given** CA uji kedua (bukan `efn_ca_crt`) dan sertifikat `api-gateway` yang ditandatanganinya
- **When** sertifikat itu dipasang sementara pada BFF staging (atau penguji menjalankan server TLS palsu bernama `api-gateway` di network `internal`)
- **Then** Odoo menandai consume `bff_tls_error` → order *review*; Next.js mengembalikan `503`/`502` dengan log `cause=UNABLE_TO_VERIFY_LEAF_SIGNATURE` (tanpa PII); Traefik menjawab `502` untuk `api.<domain>`
- **When** klien memakai hostname yang tidak ada di SAN (mis. `psql "host=<IP postgres> sslmode=verify-full …"`)
- **Then** ditolak (`server certificate … does not match host name`)
- **And** `openssl s_client -connect <svc>:<port> -CAfile efn_ca_crt -verify_hostname <svc> -verify_return_error` sukses untuk setiap target di job Prometheus `probe-internal-tls`

### F3. Mode plaintext tidak bisa diaktifkan di produksi
- **When** service dijalankan dengan `APP_ENV=production` + `EFN_ALLOW_PLAINTEXT=true` (BFF, ocr-worker, etl, Odoo, superapp-web — satu per satu)
- **Then** setiap container exit ≠ 0 dengan pesan "EFN_ALLOW_PLAINTEXT=true ditolak …"; tidak ada port yang mendengar
- **When** `/run/secrets/efn_ca_crt` tidak di-mount atau bukan PEM
- **Then** container gagal start (bukan jalan tanpa verifikasi)

### F4. PII terenkripsi at rest
- **Given** satu laporan Price Hunter dan satu login OTP dibuat
- **When** DBA menjalankan `SELECT user_hash, user_enc FROM platform.price_hunter_report` dan `SELECT ip_enc FROM platform.otp_audit`
- **Then** `user_enc`/`ip_enc` berupa bytea diawali byte versi `\x01`, tidak memuat nomor HP/IP dalam bentuk teks; `user_hash` 64 hex
- **And** role `efn_etl`/`efn_ocr` mendapat `permission denied` untuk `SELECT user_enc …`
- **And** `mc stat efn/market-scout/reports/<…>.jpg` menampilkan `Encrypted: SSE-S3`; dump `pg_dump` tidak memuat nomor HP platform

### F5. Rotasi kunci PII tanpa downtime (runbook `pii-key-rotation.md`)
- **Given** data terenkripsi v1 (F4)
- **When** `pii_encryption_key_v2` ditambahkan (`PII_ENCRYPTION_KEY_V2_FILE`), deploy, lalu `PII_ACTIVE_KEY_VERSION=2`, deploy
- **Then** selama dan setelah deploy, login, upload laporan, notifikasi Price Hunter (dekripsi `user_enc` v1) tetap berhasil; baris baru diawali `\x02`
- **When** `PII_ACTIVE_KEY_VERSION=3` tanpa kunci v3
- **Then** BFF gagal start dengan pesan "PII_ACTIVE_KEY_VERSION=3 tidak punya kunci"
- **Catatan (G6):** re-encrypt & pensiun v1 kini diimplementasi (U2) — skenario lengkap di **G7**.

### F6. Odoo hanya via WireGuard + 2FA (ADR-023, SEC-01)
- **When** penguji membuka `https://erp.<domain>/web/login` dari internet publik (tanpa WireGuard)
- **Then** `403` (middleware `internal-only`); `/xmlrpc/2/common` dan `/jsonrpc` → `403` bahkan dari peer WireGuard
- **When** penguji terhubung lewat peer WireGuard outlet uji
- **Then** halaman login tampil; login user internal **meminta kode 2FA** (`auth_totp.policy = employee_required`; user tanpa aplikasi authenticator menerima kode via email)
- **And** percobaan login > batas `ratelimit-odoo-login` per menit dari satu peer → `429`
- **And** `curl -k https://<IP origin>:443` dari luar Cloudflare/WireGuard tidak terjawab (firewall origin)

### F7. `member_mismatch` end-to-end
- Lihat A12. Tambahan: log Odoo dan BFF hanya memuat nomor ter-mask; `platform.otp_audit` tidak bertambah entri `verified` baru.

### F8. Kredensial per service (least privilege, ADR-024)
- **When** penguji memakai kredensial `efn_ocr` untuk `UPDATE platform.price_hunter_report SET user_hash = …` atau `SELECT * FROM platform.loyalty_ledger`
- **Then** `permission denied`
- **When** memakai user Redis `efn-bff` untuk `KEYS *` / `FLUSHALL` / `GET foo`
- **Then** `NOPERM` (perintah berbahaya diblok; key di luar `otp:* dtok:* rl:* altcha:used:* ms:*` ditolak)
- **And** setelah login ALTCHA sukses, `SCAN` (sebagai admin) menemukan `altcha:used:<signature>` dan kunci rate limit `rl:altcha:ip:<hmac32>` — nama key **tidak** memuat IP/nomor HP mentah (SEC-24)
- **When** memakai user MinIO `efn-ocr` untuk `PutObject`/`DeleteObject`, atau `efn-bff` untuk `GetObject`
- **Then** `AccessDenied`
- **When** memakai user RabbitMQ `efn-ocr` untuk publish `loyalty.earn.requested` dengan properti `user_id=efn-bff`
- **Then** channel ditutup broker (`PRECONDITION_FAILED - user_id property set to 'efn-bff' but authenticated user was 'efn-ocr'`)
- **When** (`RABBITMQ_STRICT_PERMISSIONS=true`, default G6) user `efn-bff`/`efn-ocr` mencoba `queue.declare` NON-pasif atau `queue.delete` pada `ocr.requested`
- **Then** `ACCESS_REFUSED`; BFF & worker tetap start normal (passive declare) — bila tidak, catat sebagai temuan dan pakai fallback `false`

---

## G. Kontrak v3 — ALTCHA, consent, DSAR, payment, rotasi kunci, mTLS, WireGuard (docs/04 §8, gelombang 5–6)

Staging: `APP_ENV=staging` (image production), `PAYMENT_PROVIDER=mock`, `CONSENT_*` diisi versi + SHA-256 teks DRAFT (nilai sama dengan `infra/compose/docker-compose.dev.yml`). Endpoint dev `POST /v1/pos/payments/:id/simulate` hanya tersedia bila `APP_ENV` ≠ production dan provider `mock` (G6). Semua langkah "BFF" dijalankan dari container uji di network `internal` dengan service key / Bearer uji.

### G1. Login dengan ALTCHA (SEC-06, §8.1)
- **Given** halaman `/masuk`, widget "Saya bukan robot" tampil tanpa memuat aset pihak ketiga (DevTools Network: hanya origin sendiri, tidak ada pelanggaran CSP di konsol)
- **When** pengguna mengisi nomor, menyelesaikan widget, menekan "Kirim kode"
- **Then** `202`, OTP terkirim; Redis berisi `altcha:used:<signature>` dengan TTL ≤ 6 menit
- **When** payload ALTCHA yang sama dikirim ulang (replay via `curl` ke `/api/auth/otp/request`)
- **Then** `400 captcha_required` (sekali pakai); widget di-remount dan meminta verifikasi baru
- **When** `POST /api/auth/otp/request` tanpa `altcha` / payload rusak / challenge > 5 menit
- **Then** `400 captcha_required` dengan pesan sama (tidak ada oracle); tidak ada WA terkirim; tidak ada entri `otp_audit`
- **When** > `ALTCHA_CHALLENGE_RL_LIMIT` (120) `GET /api/auth/challenge` dari satu IP dalam 15 menit
- **Then** `429`
- **And** endpoint POS `/v1/pos/otp/request` (service key) tetap jalan tanpa captcha

### G2. Consent lokasi & marketing (§8.4, §8.9, §8.11)
- **Given** login sukses, buka `/akun/privasi`; kedua toggle default **tidak aktif**
- **When** mengaktifkan "Lokasi perkiraan"
- **Then** `platform.consent` bertambah 1 baris `granted=true, version='location@2026-10-06'`, `text_sha256` = nilai `CONSENT_LOCATION_TEXT_SHA256`, `ip_enc` diawali byte versi kunci
- **And** di `/pantau-harga` opsi "sertakan lokasi" kini ditawarkan; laporan dengan lokasi menyimpan `geo_lat/lng`
- **When** menonaktifkan lagi
- **Then** baris baru `granted=false` (bukan UPDATE/DELETE); laporan berikutnya disimpan **tanpa** koordinat walau klien mengirim `lat/lng`
- **When** `PUT /v1/me/consents/location {granted:true, version:"location@2026-01-01"}` (versi lama)
- **Then** `409 stale_text`; UI meminta muat ulang
- **When** BFF dijalankan tanpa `CONSENT_MARKETING_*`
- **Then** mengaktifkan marketing → `503 consent_unavailable` ("Pengaturan persetujuan belum tersedia"); menonaktifkan tetap `200`
- **And** DBA: `UPDATE platform.consent SET granted=true …` / `DELETE …` sebagai `efn_bff` ditolak trigger

### G3. DSAR — unduh data dengan step-up (§8.9)
- **When** pengguna menekan "Unduh data saya" tanpa verifikasi ulang
- **Then** UI meminta kode OTP (dikirim ke nomor akun sendiri, tanpa captcha); setelah kode benar unduhan JSON otomatis berjalan (`Content-Disposition: attachment`), nomor HP di berkas tersamar
- **And** riwayat permintaan memuat `export` berstatus `completed`; > `DSAR_EXPORT_HOURLY_LIMIT` per jam → `429`
- **When** `GET /v1/me/data` langsung ke BFF tanpa `X-Step-Up-Token` / dengan token step-up milik member lain / access token biasa
- **Then** `403 step_up_required` / `step_up_invalid`

### G4. Hapus akun end-to-end lintas Platform → Odoo → ClickHouse (§8.4, §8.11)
- **Given** member uji punya: ledger poin di FNB & RTL, 2 laporan Price Hunter (foto di MinIO), consent, `res.partner` Odoo dengan HP yang sama (+ `pos.order` dengan email/mobile), baris di ClickHouse `loyalty_events` & `competitor_prices` (setelah 1 siklus ETL)
- **When** `/akun/data-saya` → "Mulai hapus akun" → OTP → centang pengakuan poin hangus → ketik `HAPUS`
- **Then** `202 { request_id, scheduled_at = now + DSAR_DELETE_GRACE_DAYS, points_forfeited = saldo }`; cookie sesi & step-up dihapus
- **When** login lagi dalam masa tenggang → "Batalkan penghapusan"
- **Then** status `cancelled`; akun aktif. Ulangi hapus; untuk uji set `DSAR_DELETE_GRACE_DAYS=0`
- **When** job penjadwal BFF berjalan (≤ `DSAR_JOB_INTERVAL_MS`)
- **Then** dalam SATU transaksi: `dsar_request.status=processing`, `stores_pending={odoo,clickhouse}`; `member` terhapus; ledger: entri `dsar_forfeit` per PT (`source=dsar`) lalu semua baris `member_ref_hash` = `sha256("erased:"||request_id)`, `member_ref_enc` NULL, `erased_at` terisi; laporan: `user_hash`=tombstone, `user_enc`/geo NULL; `otp_audit.subject`=tombstone; foto MinIO terhapus; `consent` tetap ada tanpa `ip_enc`
- **When** login dengan nomor yang sama saat `processing`
- **Then** OTP diverifikasi tetapi `409 account_deleting`; UI menampilkan "Akun ini sedang dalam proses penghapusan…" (bukan error generik)
- **When** cron Odoo `EFN: tarik permintaan erasure` berjalan (≤ 15 menit)
- **Then** `res.partner` (aktif & arsip, semua company) teranonim, `pos.order.email/mobile` kosong, chatter partner terhapus; faktur/`pos.order` tetap dengan partner teranonim; BFF menerima `complete` → `stores_pending={clickhouse}`
- **When** siklus ETL berikutnya
- **Then** `SELECT count() FROM efn_dwh.loyalty_events WHERE member_hash='<hash asli>'` = 0 (juga `competitor_prices.user_hash`); event `privacy.erasure.completed` (`user_id=efn-etl`, `message_id=erasure:<id>:clickhouse`) diterima → `status=completed`, `completed_at` terisi
- **And** baris tombstone di ClickHouse **tidak** dapat di-join ke subjek (hash tombstone ≠ hash mana pun di Odoo/platform)
- **Negatif:** publish `privacy.erasure.completed` sebagai `efn-ocr` (user_id efn-ocr) atau `idempotency_key` request lain → pesan ke `privacy.erasure.completed.dlq`, status tidak berubah; `POST /v1/pos/privacy/erasures/<id>/complete` untuk request `completed`/`cancelled` → `409 not_processing`, id tak dikenal → `404`
- **Catatan risiko (G6):** agregat poin di ClickHouse (`v_loyalty_daily`) kehilangan baris earn subjek tetapi memuat baris forfeit (negatif) — lihat laporan QA G6

### G5. QRIS POS via payment gateway mock (§8.3, §8.11; modul `efn_pos_payment_gateway`)
- **G5a Sukses:** **Given** metode "QRIS (EFN Gateway)" di outlet uji, order Rp50.000 → **When** kasir memilih metode → QR tampil di popup kasir (dan layar pelanggan bila didukung) → penguji memanggil `simulate {status:"paid"}` → **Then** dalam ≤ 4 s baris pembayaran *done*, order dapat divalidasi & tersinkron; `efn.pos.payment.intent` `paid` terhubung ke `pos.payment`
- **G5b Kedaluwarsa:** tidak dibayar sampai `expires_at` (`PAYMENT_QR_TTL_SECONDS`, uji 60 s) atau `simulate {status:"expired"}` → baris *retry*, kasir memilih metode lain; intent `expired`, `qr_string` dikosongkan
- **G5c Dibatalkan kasir:** tombol batal → BFF `cancelled`; QR baru untuk baris yang sama memakai idempotency key `…:2`
- **G5d Bayar terlambat:** setelah G5b/G5c, `simulate {status:"paid"}` → BFF menerima transisi `expired|cancelled → paid` dan menulis log WARNING "Pembayaran terlambat … refund manual". **Then (perilaku saat ini):** intent Odoo tetap `expired/cancelled` (tidak di-refresh); refund mengikuti SOP. Catat: belum ada alert otomatis (risiko QA G6)
- **G5e Nominal webhook beda:** kirim webhook mock bertanda tangan valid dengan `amount` ≠ intent → `200`, status BFF **tidak** berubah; bila Odoo menerima `paid` dengan nominal beda → intent `failed` (`amount_mismatch`), sinkron order ditolak
- **G5f Webhook ganda / replay:** webhook yang sama 2× → `200` keduanya, transisi sekali; signature salah / `t` > 300 s → `401 invalid_signature`; `GET` atau path lain di `api.<domain>/v1/webhooks/…` → ditolak Traefik
- **G5g Offline:** POS offline → metode QRIS tidak dapat dipilih / gagal dengan pesan; order dengan metode `efn_gateway` tanpa intent `paid` ditolak saat sinkron
- **G5h Provider belum dipilih (production):** `PAYMENT_PROVIDER=disabled` → `POST /v1/pos/payments` `503 payment_provider_unavailable`; kasir menerima "Gagal membuat QRIS. Gunakan metode lain."; `simulate` → `404`

### G6. SEC-07 nominal consume (§8.8)
- **When** OTP diminta untuk total Rp100.000, lalu kasir menambah item hingga Rp130.000 (toleransi 20%) dan memvalidasi
- **Then** consume `409 amount_mismatch` → order ditandai *review*; Rp115.000 → lolos; Rp40.000 (< 50%) → *review*

### G7. Rotasi kunci PII + CLI re-encrypt (U2, runbook `pii-key-rotation.md`)
- **Given** data terenkripsi v1 (F4, G2, G4)
- **When** `pii_encryption_key_v2` di-mount (`PII_ENCRYPTION_KEY_V2_FILE`), `PII_ACTIVE_KEY_VERSION=2`, deploy
- **Then** login/laporan/notifikasi tetap jalan; baris baru diawali `\x02`
- **When** `docker compose exec api-gateway node dist/cli/pii-reencrypt.js --dry-run` lalu tanpa `--dry-run`
- **Then** ringkasan per tabel/kolom (member, price_hunter_report, loyalty_ledger, otp_audit, consent), exit 0; dijalankan ulang → 0 baris berubah (idempoten)
- **When** tunggu ≥ 15 menit, set `PII_RETIRED_VERSIONS=1`, hapus secret v1, deploy
- **Then** BFF start normal. **Negatif:** bila masih ada ciphertext v1 (lewati langkah re-encrypt) → BFF gagal start (`PiiKeyRingCheck`); `PII_ACTIVE_KEY_VERSION=1` dengan v1 pensiun → gagal start dengan pesan konfigurasi

### G8. mTLS ditolak tanpa sertifikat klien (§8.5, §8.10)
- **When** dari container uji di network `internal`: `curl --cacert efn_ca_crt https://api-gateway:9464/metrics` (tanpa sertifikat klien)
- **Then** handshake TLS gagal (sertifikat klien diminta); dengan sertifikat CA internal ber-CN lain (mis. `grafana`) → `403`; dengan sertifikat `prometheus` → `200` metrik
- **And** `curl https://api-gateway:3000/metrics` → `404` (port API tidak melayani metrics); target Prometheus `api-gateway` (`:9464`) `up=1`
- **When** tanpa sertifikat klien ke `odoo:8443`, `superapp-web:3443`, `ollama:11434`, `metabase:3443`, `cadvisor:8443`, `loki:3100`, `alertmanager:9093`, `node-exporter:9100`, `rabbitmq:15691`
- **Then** semua menolak handshake; sidecar Odoo dengan sertifikat CN `ocr-worker` → ditolak (`--allow-cn` hanya `traefik`, `blackbox-exporter`)

### G9. Akses Odoo hanya via WireGuard (ADR-023, alokasi final)
- **When** perangkat POS outlet (peer `10.88.x.y`) membuka `https://erp.<domain>` lewat `wg-https`
- **Then** login tampil; sertifikat publik valid (ACME DNS-01)
- **When** laptop staf (peer `10.89.x.y`) membuka `ops.`/`bi.`/`status.`
- **Then** diizinkan; dari peer outlet `10.88.x.y` ke `ops.`/`bi.` → `403` (`internal-ops` = staf saja)
- **When** `erp.<domain>` dibuka dari internet (tanpa WireGuard) atau lewat entrypoint publik `websecure`
- **Then** tidak ada router (404) / koneksi ditolak firewall; `/json/2/…`, `/xmlrpc`, `/jsonrpc` dari peer WireGuard → `403`
- **And** Wi-Fi tamu outlet (VLAN terpisah, contoh OpenWrt) tidak dapat menjangkau `10.88.0.1`
