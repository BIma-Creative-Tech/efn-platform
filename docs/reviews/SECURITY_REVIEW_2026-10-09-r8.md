# Security & Compliance Re-review (r8): Gelombang 14, Fase 2 F&B + KDS (2026-10-09)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Re-review atas `SECURITY_REVIEW_2026-10-07-r7.md` setelah Gelombang 14. Basis: `main` @ `246dc5c`.

Cakupan yang dibaca:
- Odoo: seluruh `efn_pos_kds/**` (model, controller, security, view, template, JS POS & layar KDS, test, README), `efn_setup_pos` (manifest, README checklist F&B, `data/`, test), `efn_core/tests/test_rpc_surface.py`, `efn_audit/models/{base.py,efn_audit_log.py}` (isi sink), `efn_pos_loyalty_ledger/models/*` (status SEC-67/68).
- Infra: `infra/observability/{promtail.yml,promtail-journal.yml,loki-rules/*}`, `infra/traefik/traefik.yml`, `infra/compose/docker-compose.yml` (router Odoo, ETL), `scripts/lib/deploy-validate.sh`, `scripts/deploy.sh` (cutoff), `.github/workflows/ci.yml`.
- ETL: `services/etl/efn_etl/{loyalty_recon.py,loki.py,sink.py,audit_job.py,config.py,__main__.py}`, `services/etl/sql/odoo-readonly-grants.sql` (bagian G14).
- Dokumen: `docs/08` §7 (F-43..F-54, temuan 29–35, E-26, §7.4), laporan Gelombang 14 (odoo-engineer, data-ai, devsecops), `docs/compliance/PDP_DATA_INVENTORY.md` (v4).

Metode: membaca kode dan konfigurasi. **Reviewer tidak menjalankan apa pun.** Bukti runtime dikutip dari laporan agent G14 (304 unit test, tour 19/19, dry-run Promtail, siklus ETL di dev). Perilaku yang tidak bisa dipastikan dari kode ditandai **PLAUSIBLE**.

## Ringkasan eksekutif (untuk manajemen)

1. **Status 71 temuan lama: 53 Fixed, 8 Partial, 10 Open, 0 Regressed.** Dua temuan naik ke Fixed: SEC-51 (tanda tangan baris log audit kini diverifikasi otomatis setiap hari) dan SEC-65 (inventaris data pribadi v4 sudah memuat tabel loyalty). Rekonsiliasi harian poin BFF ↔ order POS Odoo kini berjalan dan ber-alert, sehingga celah terbesar SEC-61 (order fiktif) kini terdeteksi.
2. **Modul KDS (layar dapur) dibangun dengan benar dari sisi keamanan.** Akun layar hanya melihat tiket stasiunnya, tidak bisa membaca data pelanggan lewat API, tiket tidak memuat nama/HP/harga, dan kanal realtime memakai token acak (bukan pola Cybrosys yang mudah ditebak).
3. **Ada 8 temuan baru: 0 Critical, 0 High, 0 Medium, 5 Low, 3 Info.** Yang paling penting:
   - bug redaksi log lama sempat membuat nomor HP hampir utuh masuk Loki **dev** (34 baris). Dampaknya hanya di dev dengan data uji, karena staging/production belum ada. Bug sudah diperbaiki, tetapi belum ada uji otomatis yang mencegahnya terulang (SEC-72);
   - token di URL (mis. tautan reset password Odoo) ikut tercatat di access log dan tidak diredaksi (SEC-73);
   - mode QR-order ponsel `pos_self_order` tidak dikunci secara teknis, hanya lewat checklist (SEC-75).
4. **Rekomendasi: GO bersyarat** untuk pilot KDS di **satu outlet F&B**. Ada 8 syarat (bagian akhir). Tiga syarat terpenting:
   - stasiun memakai mode `kds_printer`, yaitu printer dapur tetap mencetak;
   - semua POS dipastikan `self_ordering_mode` ∈ {`nothing`,`kiosk`};
   - akun tablet dapur hanya ber-grup perangkat KDS, dengan 2FA aktif dan sebagai peer WireGuard.

## Status temuan lama (SEC-01..SEC-71)

| ID | Sev. awal | Status r8 | Bukti utama (file:line) | Catatan sisa |
|---|---|---|---|---|
| SEC-01..10 | High/Medium | **Fixed** | tidak berubah sejak r7 | SEC-03 PLAUSIBLE runtime |
| SEC-11 | Medium | **Partial** | `docs/compliance/PDP_DATA_INVENTORY.md:6` (v4 2026-10-07) | DPO, koreksi/ganti nomor (Q-P9..P14). Item G14 belum masuk inventaris (SEC-79) |
| SEC-12..32 | Low/Medium/Info | **Fixed** | tidak berubah | — |
| SEC-33 | Medium | **Open** | tidak disentuh G14 | blocker sebelum vendor payment aktif |
| SEC-34..37 | Low | **Fixed** | — | — |
| SEC-38 | Low (PLAUSIBLE) | **Open** | — | DPO |
| SEC-39..42 | — | **Fixed** | — | — |
| SEC-43 | Medium | **Partial** | tidak berubah | butir 5 (`base.group_system` hanya Superadmin) masih keputusan/prosedur |
| SEC-44..46 | — | **Fixed** | — | — |
| SEC-47 | Info | **Partial** | `scripts/check-odoo-deps.sh:24` masih glob `efn_*/__manifest__.py` | tidak berubah |
| SEC-48 | Info | **Partial** | BFF `route-pii-audit` statis, tidak berubah | lihat juga SEC-79: controller Odoo `efn_*` baru tanpa gerbang |
| SEC-49, 50 | Medium | **Fixed** | SEC-50 diperluas: `deploy-validate.sh:23,26,158-164`, `deploy.sh:218-225` | — |
| **SEC-51** | Low | **Fixed** | `sink.py:86-110` (verifikasi `mac` HMAC, `compare_digest`), `:331-370` (`run_sink_check`), `audit_job.py:281-296` (gagal Loki = `sink_error` → WARNING), `loki-rules/efn-ops-logs.yml:12-21` (alert WARNING), `__main__.py:195-208`. Runtime data-AI G14: 3 baris injeksi QA terdeteksi `sink_mac_invalid` | baris palsu ber-newline di stream lain tetap mungkin (SEC-71). Sink nonaktif bila `ETL_AUDIT_COPY_ENABLED=false` (production ditolak oleh SEC-50) |
| SEC-52 | Low | **Partial** | butir 3: jangkar antar-hari otomatis (`sink.py:250-272`, `anchor_mismatch/missing/absent`). Butir 5 kini termitigasi: log `purge` palsu via SQL → `log_missing_sink` (`sink.py:186-192`) | butir 4 (HMAC `row_hash` salinan) belum, sehingga alarm palsu masih mungkin (laporan data-AI) |
| SEC-53, 54 | Low | **Fixed** | — | — |
| SEC-55 | Info | **Open** | — | backlog |
| SEC-56 | Info | **Fixed** | — | — |
| SEC-57 | Info | **Open** | — | backlog |
| SEC-58 | High | **Fixed** | `test_rpc_surface.py:37-67` (+7 entri KDS, semuanya ditinjau di r8) | `/json/2` belum diuji nyata |
| SEC-59, 60 | Medium | **Fixed** | tidak berubah | sisa ada di SEC-67/68 |
| SEC-61 | Medium | **Partial** | butir 1 **selesai**: `loyalty_recon.py:271-346` (7 kelas anomali critical, termasuk `earn_without_paid_order` = order fiktif), `promtail.yml:154-163`, `loki-rules/efn-loyalty.yml:83-125` (alert anomali, open items, dead-man 26 jam). Butir 2 **selesai**: `loyalty_recon.py:139-157,349-362` (outbox `hold/pending/failed`, order/reservasi `review` menua). Runtime: anomali sengaja terdeteksi (laporan data-AI) | belum: saldo negatif per member (`loyalty_recon.py:38-39`). Butir 3 (admin `_efn_post` lewat server action) hanya terdeteksi bila tidak ada order Odoo yang cocok |
| SEC-62 | Low | **Fixed** | — | — |
| SEC-63 | Low | **Partial** | tidak berubah | Q-27 |
| SEC-64 | Low | **Open** | grep `MB_ENCRYPTION_SECRET_KEY` di `infra/` = 0 | — |
| **SEC-65** | Low | **Fixed** | `PDP_DATA_INVENTORY.md:62-70,86-91,161-165,191` (loyalty, `erasure_suppression`, Q-P12) | durasi suppression = keputusan DPO (Q-P12, dilacak di SEC-11). Baris `:132` usang ("mac belum di-wire") |
| SEC-66 | Info | **Open** | — | backlog |
| SEC-67 | Low | **Open** | `efn_pos_loyalty_outbox.py:136-173` tidak berubah (grep `restore_points` = 0) | — |
| SEC-68 | Low | **Open** | `pos_order.py:325-333` tidak berubah | — |
| SEC-69 | Info | **Open** | `deploy-validate.sh` tanpa pagar `LOYALTY_*` (grep = 0) | — |
| SEC-70 | Info | **Partial** | `ci.yml:316` `EFN_TOUR_MIN_TESTS: "19"`, `docs/04:208` kini `_efn_get` | belum: cek AST pemeriksa di statement pertama, `ORM_OVERRIDES` (`test_rpc_surface.py:34`) masih membebaskan override ORM, `/json/2` nyata |
| SEC-71 | Info | **Open** | stream loyalty BFF tetap dicocokkan **setelah** redaksi (`promtail.yml:86-98` sebelum `:113`); rule Prometheus untuk `efn_loyalty_anomaly_total` belum ada | — |

**Rekap:** Fixed 53 · Partial 8 (SEC-11, 43, 47, 48, 52, 61, 63, 70) · Open 10 (SEC-33, 38, 55, 57, 64, 66, 67, 68, 69, 71) · Regressed 0.

## Temuan baru

| ID | Severity | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|
| SEC-72 | Low | Bug redaksi Promtail (sebelum G14) memasukkan nomor HP hampir utuh dan token Bearer ke Loki dev; sudah diperbaiki, tetapi tanpa uji regresi dan sumber log mentah belum dilacak | `promtail.yml:86-98`, `promtail-journal.yml:55-61`, `ci.yml` (tidak ada uji pipeline) | R-L01, UU PDP |
| SEC-73 | Low (PLAUSIBLE) | Rahasia di query string (token reset password Odoo, `access_token` self-order) tercatat utuh di access log Traefik dan log werkzeug Odoo → Loki 30 hari; runbook menyatakan sebaliknya | `infra/traefik/traefik.yml:10-16`, `promtail.yml:93-98`, `docs/runbooks/security-incident.md:107` | R-S03, R-S10 |
| SEC-74 | Low | Akun perangkat KDS = user internal: permukaan `base.group_user` di luar KDS; constraint grup berbasis denylist dan bisa dilewati lewat form Grup | `efn_pos_kds/models/res_users.py:10-37`, `security/efn_pos_kds_security.xml:12-16,56-63` | R-S03, UU PDP |
| SEC-75 | Low | `pos_self_order` (auto-install dengan `pos_restaurant`) tidak dikunci teknis ke `nothing`/`kiosk`; route `auth='public'` tanpa rate limit dapat dijangkau semua peer WireGuard | `efn_setup_pos/__manifest__.py:16`, `README.md:166,172-173`, `docs/08:307,317-318` | R-S01, ADR-023 |
| SEC-76 | Low | Hook tiket KDS menelan semua exception tanpa alert. Pada mode `kds`, printer cadangan dilewati selama layar hidup, sehingga pesanan bisa hilang diam-diam | `efn_pos_kds/models/pos_order.py:61-73`, `static/src/pos/pos_store_patch.js:117-133` | R-B01 |
| SEC-77 | Info | Token kanal KDS hanya dirotasi manual (tidak saat user perangkat dicabut). `EFN_KDS_READY` dikirim di kanal token `pos.config` yang menjadi semi-publik bila self-order aktif. Kasir dapat membaca `device_user_ids` stasiun | `efn_kds_station.py:46,238-246`, `efn_kds_ticket.py:184-195`, `ir.model.access.csv:3` | R-S10 |
| SEC-78 | Info | Cakupan redaksi: HP berpemisah tidak teredaksi, tanpa batas kata, token selain Bearer tidak teredaksi. Stream `efn_audit_sink` dikecualikan sementara `note` purge manual berupa teks bebas admin | `promtail.yml:86-98`, `efn_audit/models/efn_audit_log.py:164-167`, `base.py:205-223` | R-L01 |
| SEC-79 | Info | Dokumentasi & gerbang G14: inventaris PDP belum memuat tiket KDS/hasil recon. Gerbang CI hanya memindai metode model, tidak memindai `http.route` di addon `efn_*` | `PDP_DATA_INVENTORY.md`, `test_rpc_surface.py:98-119` | UU PDP, R-S01 |

---

## Detail temuan baru

### SEC-72: Low: Kebocoran HP/Bearer historis akibat bug redaksi Promtail (dev)
**Fakta (laporan devsecops G14.1/G14.4):**
- Stage `replace` Promtail hanya mengganti *capture group*. Ekspresi lama `(\+?62|0)8[1-9][0-9]{7,11}` hanya mengganti awalan, sehingga sisa nomor masuk Loki (`[PHONE_REDACTED]81298765432`). Ekspresi Bearer tanpa grup tidak meredaksi apa pun.
- Di Loki dev ditemukan 34 baris berpola `[PHONE_REDACTED]\d{6}` dalam 30 hari.

**Perbaikan G14 (diverifikasi dari kode):**
- `promtail.yml:93-98` dan `promtail-journal.yml:56-61` kini memakai `((?:\+?62|0)8[1-9][0-9]{7,11})` (seluruh nomor = satu grup luar) dan `(?i)bearer\s+([a-z0-9\-_\.~+/]+=*)` (token = grup).
- Untuk format `0812…`, `62812…`, `+62812…`, dan `%2B62812…` (URL-encoded), seluruh nomor kini terganti. Format ter-mask `62812****789` dibiarkan.
- Dry-run devsecops (G14.2) mengonfirmasi hasil ini.

**Penilaian dampak:**
- Hanya **dev**. Staging/production belum ada, dan nomor di dev berasal dari smoke/fixture fiktif (PLAUSIBLE; cocok dengan probe R2 `?member_ref=62812…` yang tercatat di access log Traefik, `docs/testing/E2E_SMOKE_REPORT.md:77`).
- Ini **bukan** kegagalan perlindungan data pribadi nyata yang wajib dinotifikasi 3×24 jam (UU PDP Pasal 46). Perlakukan sebagai *near-miss*.

**Mengapa tetap diberi ID:**
- Kontrol PDP utama untuk log rusak tanpa terdeteksi sejak diperkenalkan.
- Tidak ada uji CI yang mencegahnya terulang (grep `promtail` di `ci.yml` hanya cek sintaks/daftar, `:381`).
- Baris yang mengandung nomor menunjukkan ada komponen yang mencatat nomor **mentah di sumber**, padahal CLAUDE.md mewajibkan mask di sumber.

**Rekomendasi:**
1. Tambahkan job CI `promtail --dry-run` dengan fixture tetap, lalu assert output. Fixture minimal:
   - HP dalam 6 format, termasuk berpemisah (SEC-78);
   - Bearer/JWT;
   - baris sink valid (harus utuh);
   - jangkar;
   - baris loyalty.
2. Lacak sumber dengan query Loki dev `sum by (service) (count_over_time({container=~".+"} |~ "\\[PHONE_REDACTED\\]\\d{6}" [30d]))`. Perbaiki di sumber, misalnya probe smoke R2 dan access log (SEC-73).
3. Hapus baris dev lewat delete API Loki atau biarkan retensi 30 hari habis (± 7 Nov 2026). Catat di register insiden sebagai near-miss.
4. Sebelum staging pertama menerima log, jalankan ulang query (2) di staging. Bila tidak nol, ikuti `breach-response-72h.md`.

### SEC-73: Low (PLAUSIBLE): Rahasia di query string masuk access log tanpa redaksi
**Lokasi:**
- `infra/traefik/traefik.yml:10-16`: `accessLog.fields` hanya men-drop header `Authorization`/`Cookie`. Field default lain (termasuk `RequestPath`, yang di Traefik memuat query string) tetap tercatat.
- Log werkzeug Odoo mencatat baris request lengkap dengan query.
- Redaksi Promtail (`promtail.yml:93-98`) hanya mencakup HP dan Bearer.
- `docs/runbooks/security-incident.md:107` menyatakan "access log Traefik … (tanpa query string)". Untuk Traefik pernyataan ini **tidak sesuai konfigurasi**; yang benar hanya untuk s3-gateway (`%HPO`).

**Skenario:**
1. Admin menekan "Send password reset" untuk user internal. User membuka `https://erp.<domain>/web/reset_password?db=…&token=<T>` lewat WireGuard.
2. Token tercatat di access log Traefik dan log Odoo, lalu masuk Loki (30 hari).
3. Pemegang akses Grafana/Loki (ops) membaca token dan memakainya sebelum kedaluwarsa atau sebelum dipakai korban. Akibatnya akun internal diambil alih. Hambatan tersisa hanya 2FA: `employee_required` meminta kode via email bila TOTP belum diaktifkan.

Sama halnya untuk URL self-order `?access_token=` (SEC-75) dan pola URL bertoken lain di masa depan. **PLAUSIBLE:** bergantung pada `auth_signup` terpasang/dipakai dan masa berlaku token Odoo 19. Tidak diverifikasi runtime.

**Rekomendasi:**
- Tambahkan redaksi nilai parameter sensitif di Promtail (nilai = grup tunggal), misalnya `(?i)[?&](?:token|access_token|api_key|signature|x-amz-signature|code)=([^&\s"]+)`. Masukkan ke fixture CI SEC-72.
- Pertimbangkan `accessLog.fields.names.RequestPath: drop` plus `RequestAddr`/router saja untuk host `erp.`. Karena Traefik tidak bisa memilih per-router, alternatifnya adalah redaksi di Promtail.
- Turunkan level `werkzeug` Odoo (`--log-handler werkzeug:WARNING`) di production.
- Perbaiki teks runbook.

### SEC-74: Low: Permukaan akun perangkat KDS dan constraint grup
**Yang sudah benar (diverifikasi):**
- Rule `res.partner` perangkat (`efn_pos_kds_security.xml:58-63`) membatasi `search/read/name_search` langsung ke partner diri dan company.
- Diuji dengan set modul lengkap (`test_kds.py:386-395`). Tidak ada grup lain yang memberi rule `res.partner` lebih lebar kepada `base.group_user` di set modul ini, sehingga rule OR tidak membatalkannya.
- `order_id/config_id/session_id` tiket ber-`groups` POS user (`efn_kds_ticket.py:60-69`).

**Masalah:**
1. **Akun perangkat adalah user internal** (`base.group_user`, README langkah 2, `test_kds.py:87`). Akibatnya ia mewarisi seluruh ACL baca `base.group_user` lewat RPC, bukan hanya model KDS. Contohnya:
   - `product.product`/`product.template`, termasuk `standard_price` (harga pokok ber-`groups="base.group_user"`), padahal README menyatakan "tanpa harga";
   - `hr.employee.public` (pos_hr → hr), berisi kontak kerja karyawan;
   - kanal Discuss karyawan.

   Selain itu, ORM merender `display_name` many2one lewat `sudo()`. Model lain yang dapat dibaca `base.group_user` dan punya field partner dapat menampilkan **nama** partner walau rule `res.partner` menolak baca langsung (**PLAUSIBLE**, tergantung ACL `loyalty.card` dll. di 19.0).
2. **Constraint denylist** (`res_users.py:10-14`) hanya menolak `group_pos_user`, `group_erp_manager`, dan `group_system`. Grup akuntansi, penjualan, stok, `group_partner_manager`, dan lainnya lolos.
3. **Constraint bisa dilewati.** `@api.constrains("group_ids")` di `res.users` (`:20`) hanya berjalan saat `res.users` ditulis. Admin yang menambah user lewat tab *Users* di form `res.groups` (menulis `res.groups.user_ids`), atau menambah `implied_ids`, tidak memicu constraint.
4. Rule perangkat ada di blok `noupdate="1"` (`efn_pos_kds_security.xml:18`). Koreksi domain di versi berikut tidak akan diterapkan ke DB yang sudah ter-install tanpa migrasi.

**Skenario:** tablet dapur (sesi persisten, dipakai bergantian) dicuri atau dipakai orang dapur. Lewat `/web/dataset/call_kw` (dalam WireGuard), orang itu membaca daftar produk + harga pokok dan direktori karyawan. Bila PLAUSIBLE butir 1 benar, ia juga membaca nama member dari relasi.

**Rekomendasi:**
- Ganti denylist dengan **allowlist**: grup efektif user perangkat ⊆ {`base.group_user`, `group_kds_device`} + implied-nya.
- Tegakkan juga di override `res.groups.write` (`user_ids`, `implied_ids`), atau tambahkan test/cron audit harian ber-alert.
- Uji runtime: sebagai user perangkat, iterasi semua model yang dapat dibaca dan berisi many2one `res.partner`, lalu pastikan tidak ada nama pelanggan yang keluar.
- Opsi lebih kuat: akun perangkat sebagai user *share* (non-internal) dengan grup KDS saja dan `auth_totp.policy=all_required`. Konsekuensinya: kebijakan 2FA ikut berlaku untuk portal. Keputusan ini sebaiknya lewat ADR kecil.
- Pindahkan rule perangkat ke blok non-`noupdate` atau sediakan skrip migrasi.

### SEC-75: Low: Mode QR-order ponsel tidak dikunci teknis
**Lokasi:**
- `efn_setup_pos/__manifest__.py:16` memasang `pos_self_order`, yang juga auto-install bersama `pos_restaurant` (`docs/08:317`).
- Penguncian ke `self_ordering_mode='nothing'` hanya ada di checklist README (`README.md:166,172-173`). Tidak ada `ir.default`, constraint, maupun test (`tests/test_fnb_defaults.py` hanya preset).
- **PLAUSIBLE:** di CE 17/18, `pos.config.create` milik `pos_self_order` mengisi `self_ordering_mode='mobile'` untuk config restoran bila nilai tidak diberikan. Bila perilaku ini masih ada di 19.0, setiap POS restoran baru yang dibuat dari UI langsung mengaktifkan route publik.

**Skenario:**
- Route `/pos-self*` dan `/pos-self-order/process-order/*` bersifat `auth='public'` tanpa rate limit (`docs/08:307,318`).
- Traefik membatasi router `erp.` ke `internal-only` + WireGuard (`docker-compose.yml:844-880`), sehingga ponsel pelanggan di internet **tidak** dapat menjangkau. Dampak ke publik internet tertutup.
- Namun semua peer WireGuard (tablet KDS, kiosk, PC kasir, staf VPN) dapat memanggil route pra-autentikasi itu. Pemegang QR meja (token config 64-bit + meja 32-bit) dapat membuat order draft. Order ini melewati hook KDS dan membanjiri dapur, karena `sync_from_ui` publik ikut menjalankan `_process_order`.
- Token config juga menjadi nama kanal bus `pos.config`. Pemegang QR dapat berlangganan notifikasi POS standar dan `EFN_KDS_READY` (SEC-77).
- Bila suatu saat ada yang membuka path itu di Traefik (opsi C `docs/08` §7.4) tanpa ADR, seluruh permukaan menjadi publik.

**Rekomendasi:**
1. Tambahkan `ir.default` `pos.config.self_ordering_mode = 'nothing'` di `efn_setup_pos`.
2. Tambahkan constraint di modul `efn_*` yang menolak `mobile`/`consultation` kecuali `ir.config_parameter efn.pos.self_order_mobile_allowed=True`. Parameter ini hanya Superadmin yang boleh mengubah, diaudit `efn_audit` (filter `efn.%` sudah mencakup), dan baru dibuka setelah ADR Q-23.
3. Buat test yang memastikan config restoran baru ber-mode `nothing`.
4. Tambahkan pemeriksaan pra-go-live (SQL di runbook/deploy): `SELECT id FROM pos_config WHERE self_ordering_mode NOT IN ('nothing','kiosk')` harus 0.
5. Pasang middleware rate-limit khusus path `/pos-self` di router `erp.` (kiosk tetap jalan).

### SEC-76: Low: Kegagalan tiket KDS ditelan tanpa alert
**Lokasi:**
- `pos_order.py:67-72`: `except Exception` di sekitar `_efn_kds_after_sync` hanya mencatat `_logger.exception`.
- Savepoint (`:113-114`) benar: cache ORM dibersihkan dan transaksi order tetap sehat, sesuai tujuan "KDS di luar jalur transaksi".
- Masalahnya, **tidak ada** sinyal ke kasir/dapur dan **tidak ada** alert Loki (grep `kds` di `infra/observability` = 0). Error konkurensi (unik `(order, station, sequence_no)`, serialisasi) juga ditelan, sehingga tidak di-retry seperti biasanya di Odoo.

**Skenario (mode `kds`):**
1. Layar stasiun online, sehingga `printChanges` melewati printer cadangan (`pos_store_patch.js:117-119`) dan menganggap "terkirim" (`:133`).
2. Pembuatan tiket di server gagal (bug data, konflik), lalu ditelan.
3. Tidak ada tiket dan tidak ada cetakan. Pesanan tidak dimasak, dan tidak ada yang tahu sampai pelanggan komplain. Pada mode `kds_printer` (default pilot) printer tetap mencetak, sehingga dampaknya hanya layar tidak lengkap.

**Rekomendasi:**
- Rule Loki `count_over_time({service="odoo"} |= "EFN KDS: gagal membuat tiket" [5m]) > 0`, kirim ke on-call outlet.
- Tandai order (`efn_kds_failed`) dan kirim notifikasi bus ke POS (kasir mencetak ulang manual).
- Biarkan `OperationalError` serialisasi naik (jangan ditelan) agar Odoo me-retry request.
- Pertahankan `kds_printer` selama pilot (syarat GO).

### SEC-77: Info: Siklus hidup token kanal KDS
- Token hanya dirotasi lewat tombol manager (`efn_kds_station.py:238-246`). Mencabut/mengganti `device_user_ids` atau mengarsipkan stasiun tidak merotasi token. Bus Odoo 19 tidak membatasi langganan kanal string (temuan `docs/08` #31), sehingga pemegang token lama tetap menerima `{type, ticket_ids, ts}`. Payload tanpa isi tiket (diuji `test_kds.py:437-440`) dan isi tetap butuh RPC ber-akses, jadi dampaknya rendah.
- `EFN_KDS_READY` (`efn_kds_ticket.py:184-195`: `order_id`, `table_id`, `ticket_id`, state) dikirim di kanal token `pos.config`. Token ini dimuat ke browser POS dan menjadi isi QR bila self-order aktif (SEC-75). Isinya tanpa PII.
- ACL `efn.kds.station` untuk POS user (`ir.model.access.csv:3`) tanpa rule pembatas. Kasir dapat membaca `device_user_ids` (login akun tablet) dan `last_seen` semua stasiun di company-nya.
- **Rekomendasi:**
  - Rotasi otomatis di `write` bila `device_user_ids`/`active` berubah.
  - Batasi field `device_user_ids` dengan `groups` manager.
  - Dokumentasikan bahwa QR self-order = akses ke kanal config.

### SEC-78: Info: Cakupan redaksi log dan pengecualian stream sink
- Pola HP (`promtail.yml:94`) tidak menangkap format berpemisah (`0812-3456-7890`, `+62 812 3456 7890`, `(+62) 812…`). Pola ini juga tanpa batas kata, sehingga potongan hash/UUID ikut terganti (kosmetik, lihat SEC-71).
- Token lain tidak teredaksi: `Basic …`, `X-Api-Key`, `discount_token`, kode OTP dalam teks. Sumber BFF sudah me-mask, dan Promtail hanya jaring kedua.
- Stream `efn_audit_sink` sengaja dikecualikan (`:86-87`) agar MAC tidak rusak. Itu benar, tetapi `note` purge manual berisi teks bebas dari pemanggil (`efn_audit_log.py:164-167`: `f"manual: {note}"`, `base.py:219`). Bila admin menulis nomor/nama di catatan purge, nilai itu tersimpan di Loki **400 hari** tanpa redaksi.
- **Rekomendasi:**
  - Mask pola HP/email di `note` **di sumber** sebelum MAC (`sink_event`).
  - Tambah rule deteksi (bukan modifikasi) `{stream="efn_audit_sink"} |~ "(\\+?62|0)8[1-9][0-9]{7,11}"` → warning.
  - Perluas pola HP dengan pemisah opsional, misalnya `((?:\+?62|0)[\s.-]?8[1-9](?:[\s.-]?[0-9]){7,11})`, lalu uji di fixture SEC-72.

### SEC-79: Info: Dokumentasi PDP dan gerbang controller untuk G14
- `PDP_DATA_INVENTORY.md` belum memuat:
  - `efn.kds.ticket(.line)`: `internal_note`/`note` adalah teks bebas kasir (bisa memuat nama pelanggan), `table_label`, `tracking_number`, retensi 90 hari PLACEHOLDER;
  - tabel ClickHouse `loyalty_recon_run/finding`;
  - stream Loki `loyalty_event="etl_recon"`.

  Baris `:132` juga masih menyebut "verifikasi mac belum di-wire".
- `test_rpc_surface.py:98-119` hanya memindai metode model. Controller `/efn_kds/<id>` (`controllers/main.py:14`) adalah `http.route` pertama di addon `efn_*`, dan masuk tanpa gerbang CI apa pun. Implementasinya saat ini benar (lihat bawah).
- **Rekomendasi:**
  - Perbarui inventaris (v5), termasuk SOP "tanpa data pelanggan di catatan dapur" dari README.
  - Tambahkan uji AST untuk `http.route` di `efn_*` dengan allowlist `(path, auth)`. Tolak `auth='public'`/`'none'` tanpa entri yang ditinjau security.

---

## Verifikasi area fokus G14 (kontrol yang sudah benar)

**`efn_pos_kds`**
- **Route `/efn_kds/<id>`** (`controllers/main.py:14-28`):
  - `auth='user'`, hanya GET;
  - stasiun tidak ada, stasiun lain, PT lain, atau kasir → **404 yang sama**, dan token tidak bocor (`test_kds_tour.py:33-52`);
  - anonim → redirect login;
  - `Cache-Control: no-store`;
  - router `erp.` internal-only + WireGuard + rate-limit.

  Tidak ada IDOR: `_efn_kds_check_device` (`efn_kds_station.py:160-177`) memeriksa grup (perangkat atau manager) + `check_access('read')` (rule company + rule stasiun milik perangkat) + company ∈ `user.company_ids` + keanggotaan `device_user_ids`.
- **RPC bump/recall/line_done** (`efn_kds_ticket.py:144-151,197-225,310-330`):
  - stasiun setiap tiket dicek, lalu `check_access` tiket/baris;
  - transisi dibatasi allowlist (`FORWARD`, recall dua pasang, batas waktu recall dari parameter > 0);
  - tiket batal ditolak;
  - penulisan `sudo` hanya setelah cek.

  Ketujuh entri allowlist (`test_rpc_surface.py:57-63`) sudah diperiksa satu per satu, dan masing-masing memanggil pemeriksa di awal. `action_rotate_token` = manager + `check_access('write')`. `efn_kds_station_status` = POS user + rule config + company, tanpa token. Uji akses lintas stasiun/PT/grup: `test_kds.py:372-422`.
- **Token kanal:**
  - uuid4 (`test_kds.py:448`), field ber-`groups` manager (`:46`), tidak dimuat ke POS (`:155-157`, diuji `:459-465`);
  - keluar hanya lewat halaman ter-autentikasi;
  - klien hanya menerima sidik SHA-256 12 heksa (tidak dapat dibalik);
  - log rotasi tanpa token (`:245`).
- **Data tiket:**
  - payload berkunci tetap tanpa partner/harga/pembayaran (`efn_kds_ticket.py:112-141`, diuji `test_kds.py:424-443` termasuk dump bus);
  - atribut dari `attribute_value_ids` server, bukan teks klien;
  - kuantitas dihitung server dari baris DB, sehingga klien tidak dapat memalsukan qty tiket;
  - retensi autovacuum.
- **Hook sync:** savepoint flushing membersihkan cache saat rollback. Kegagalan KDS tidak membatalkan transaksi kas (sesuai prinsip "AI/fitur pendukung di luar jalur transaksi"). Sisanya ada di SEC-76.
- **Printer cadangan:** gagal cek status = cetak (fail-safe, `pos_store_patch.js:85-97`). Routing printer standar tidak diubah. Offline tetap mencetak, dan tiket ditandai "sudah dicetak".
- **Layar KDS:** OWL tanpa `t-raw`/`markup`. Teks produk/catatan di-escape. `session_info` di-serialisasi `json.dumps` (pola CE).
- **Multi-company:** rule global `company_id in company_ids` di tiga model, `_check_company_auto`, `check_company` pada relasi.

**`efn_setup_pos` F&B:** preset delivery tidak tersedia di self-order (minimisasi alamat) dan takeout tanpa kalender demo, keduanya diuji (`test_fnb_defaults.py`). Checklist #20 dan peringatan `mobile` di README sudah benar secara isi. Kelemahannya hanya pada penegakan (SEC-75).

**Promtail (fix G14):**
- grup tangkapan kini mencakup seluruh nilai;
- sink dan jangkar dicocokkan **sebelum** redaksi dengan regex berjangkar dan hanya dari label Docker `service` yang benar, sehingga MAC tetap valid;
- `promtail-journal.yml` memakai pola yang sama dan hanya meneruskan unit EFN.

**ETL `loyalty_recon` / `loki` / `sink`:**
- **Loki:** mTLS dengan CA internal, `check_hostname`, TLS ≥ 1.2, sertifikat klien CN `etl` wajib bila https (`loki.py:158-167`, `config.py:189-200`). `http://` hanya dengan `EFN_ALLOW_PLAINTEXT`. Respons dibatasi 64 MB. Error tanpa isi baris.
- **Kueri LogQL:** konstanta (`sink.py:205-206`) yang di-`urlencode`, tanpa input pengguna. **Tidak ada vektor injeksi LogQL.**
- **SQL:** berparameter (`%(…)s`, `ANY(%(…)s)`). Grant Odoo per kolom tanpa PII (`odoo-readonly-grants.sql:118-122`).
- **Output tanpa PII:** sampel = id numerik, error dicatat hanya nama tipe exception (`__main__.py:133-137`), tanpa `member_hash`/HP/nama (`loyalty_recon.py:33-34`).
- **Fail-loud:** kegagalan Loki menjadi `sink_error` (alert), hasil terpotong menjadi `truncated` (alert), dan dead-man 26 jam.

**`deploy-validate.sh` cutoff `ETL_LOYALTY_RECON_NOT_BEFORE`** (`:158-164`, `deploy.sh:218-225`):
- nilai berasal dari manifest bertanda tangan;
- hanya format UTC kanonik, tanpa karakter kontrol;
- cutoff di masa depan ditolak;
- **memajukan** cutoff di production butuh `EFN_ALLOW_CONTROL_OFF` milik root;
- nilai kosong mempertahankan nilai lama.

Dalam satu deploy, pelaku hanya dapat "membutakan" maksimal jendela lookback (72 jam), dan itu pun butuh kunci penandatangan rilis. Catatan: `deploy.yml` belum meneruskan `CFG_ETL_LOYALTY_RECON_NOT_BEFORE` (laporan devsecops G14.4). Kegagalannya aman: tanpa cutoff, recon menghasilkan lebih banyak temuan, bukan lebih sedikit.

## UU PDP: status

| Kewajiban | Status r8 | Bukti / sisa |
|---|---|---|
| Consent server-side, hak akses, hak hapus | **Implementasi** | tidak berubah |
| Minimisasi di dapur (KDS) | **Implementasi** | tiket tanpa partner/HP/harga. Akun perangkat tidak membaca kontak secara langsung. Sisa: SEC-74 (permukaan internal user) dan catatan dapur berupa teks bebas (SOP) |
| Log tanpa PII | **Implementasi dengan celah** | fix regex G14. Belum ada uji regresi (SEC-72), query string (SEC-73), format berpemisah dan `note` sink (SEC-78) |
| Inventaris / RoPA | **v4, perlu v5** | KDS dan recon belum tercantum (SEC-79) |
| Insiden | **Near-miss dev** | 34 baris dev (SEC-72). Bukan notifikasi wajib selama staging/prod belum menerima log |
| DPO, koreksi nomor, retensi final | **Belum diputuskan** | Q-P9..P14 |

## Rekomendasi go/no-go: pilot KDS di satu outlet F&B

**Keputusan: GO bersyarat** untuk mengaktifkan `efn_pos_kds` di **satu outlet F&B pilot**.

Alasan:
- Tidak ada temuan Critical/High/Medium terbuka di jalur KDS.
- Akses layar terikat per stasiun dan per PT, data pelanggan tidak masuk dapur, dan token kanal tidak dapat ditebak.
- Kegagalan KDS tidak mengganggu kasir maupun pembayaran.
- Celah yang tersisa bersifat lokal (dalam WireGuard), membutuhkan admin/insider, atau berdampak operasional.

Syarat sebelum go-live:
1. **Mode stasiun `kds_printer`** untuk semua stasiun pilot, sampai SEC-76 (alert + notifikasi gagal tiket) selesai. Mode `kds` (printer hanya cadangan) **tidak** dipakai di pilot.
2. **Self-order terkunci:** query `SELECT id, name, self_ordering_mode FROM pos_config WHERE self_ordering_mode NOT IN ('nothing','kiosk')` = 0 rows di DB pilot, dicek ulang setelah setiap POS baru dibuat sampai `ir.default` + constraint SEC-75 ada. QR meja ponsel tidak diaktifkan sebelum ADR Q-23.
3. **Akun tablet dapur:**
   - satu user per layar, grup **hanya** `base.group_user` + `group_kds_device` (verifikasi via query grup efektif, karena constraint bisa dilewati, SEC-74);
   - 2FA aktif;
   - tablet = peer WireGuard sendiri, browser mode kiosk;
   - login tablet tidak dipakai untuk hal lain.
4. **Uji staging:** upgrade `efn_pos_kds` + `efn_setup_pos 19.0.1.4.0`, tour 19/19, buka `/efn_kds/<id>` dari peer non-perangkat → 404, dan uji runtime SEC-74 butir 1 (model ber-partner terbaca perangkat).
5. **Pergantian perangkat:** SOP rotasi token (tombol *Rotasi token kanal*) setiap kali tablet diganti, dicabut, atau hilang, ditambah pencabutan peer WireGuard (SEC-77).
6. **SOP kasir:** jangan menulis nama/HP pelanggan di catatan dapur (README). Masukkan ke pelatihan pilot.
7. **Log:** sebelum staging menerima log nyata, uji regresi Promtail (SEC-72 butir 1) dan redaksi parameter token (SEC-73) sudah aktif.
8. **Dokumen:** inventaris PDP v5 memuat tiket KDS (SEC-79). Keputusan Q-19 (stasiun, target menit, retensi) dan Q-24 (perangkat/printer) dicatat. Default saat ini PLACEHOLDER.

Syarat GO loyalty dari r7 tetap berlaku bila outlet F&B juga menyalakan poin.

**NO-GO / kembali ke printer saja:** bila layar menampilkan data pelanggan, perangkat dapat membuka stasiun/PT lain, atau log memuat "EFN KDS: gagal membuat tiket" berulang. Ubah mode stasiun ke `printer` (perilaku CE standar); POS tetap jalan.

## Prioritas perbaikan

1. **Sebelum pilot F&B (syarat GO):**
   - SEC-75 (ir.default + query cek);
   - SEC-74 (verifikasi grup akun tablet + uji runtime);
   - SEC-72 butir 1 dan SEC-73 (sebelum staging menerima log);
   - SEC-79 (inventaris).
2. **Selama pilot (≤ 2 minggu):**
   - SEC-76 (alert + notifikasi kasir);
   - SEC-74 (constraint allowlist + `res.groups.write`);
   - SEC-77 (rotasi otomatis);
   - SEC-78 (mask `note` di sumber, pola berpemisah).
3. **Dari r7 yang masih berlaku:**
   - SEC-67, SEC-68 (selama pilot loyalty);
   - sisa SEC-61 (saldo negatif);
   - SEC-69;
   - SEC-63 (Q-27).
4. **Sebelum vendor payment:** SEC-33.
5. **Hardening terjadwal:**
   - SEC-70 (AST pemeriksa, override ORM, `/json/2`);
   - SEC-71;
   - SEC-52 butir 4;
   - SEC-64, SEC-47, SEC-48.
6. **Backlog:** SEC-55, 57, 66, 38, SEC-11 (DPO).
7. **Verifikasi runtime yang masih dibutuhkan:**
   - perilaku default `self_ordering_mode` di `pos.config.create` 19.0 (SEC-75);
   - leak `display_name` relasi untuk akun perangkat (SEC-74);
   - token reset password Odoo 19 di access log (SEC-73);
   - badge "Siap" lewat websocket;
   - kiosk self-order menulis `last_order_preparation_change`;
   - dead-man ETL 26 jam nyata;
   - `/json/2` (SEC-58).
