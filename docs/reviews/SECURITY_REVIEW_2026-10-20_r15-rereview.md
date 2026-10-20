# Security Re-review r15: Gelombang 22 (branch `chore/wave-22`, HEAD 096d3fa)

Tanggal: 20 Okt 2026 · Reviewer: efn-security-reviewer (read-only, tanpa shell; file dibaca langsung, bukan `git diff`)
Sumber: `docs/reviews/SECURITY_REVIEW_2026-10-19_r15.md`; keputusan lead docs/04 §14 v9.1, **L-82** dan **L-83**
(`docs/04_SERVICE_CONTRACTS.md:1411-1412`); laporan builder bagian G22 r15; QA G22 (`docs/agent-reports/efn-qa-engineer.md:754-761`).
Nomor temuan baru dimulai dari SEC-174.

## Ringkasan eksekutif

- **Enam dari tujuh temuan r15 yang wajib diperbaiki berstatus Fixed (SEC-164, 165, 167, 168, 171, 173), satu Partial
  (SEC-166).** Mekanisme K3 (snapshot close dibekukan, refund `deferred`/`awaiting`, flag poin hanya setelah 2xx) ditulis
  rapi dan dikunci dengan `touch_row`. Di sisi BFF, `earn_pending` menjadi lapis kedua.
- **Ada satu celah baru, SEC-174 (Medium, PLAUSIBLE), di jalur yang tidak disentuh perbaikan SEC-166.** Saat QR dicetak
  ulang, *semua* order sesi lama ikut dipindah ke sesi baru, termasuk yang sudah dibayar. Sesi lama ditutup tanpa event.
  Akibatnya poin tamu sebelumnya jatuh ke pengklaim sesi baru.
- **SEC-169/170 memang tertunda dengan default aman.** Sakelar `false` di compose, config, CI, dan `deploy.sh`, dan env
  step-up tidak ada di kode. Namun pemblokir aktivasi L-82 belum ditegakkan secara teknis: kedua sakelar dapat dinyalakan
  di production lewat variabel CI tanpa izin Superadmin (SEC-175, Low).
- **Kedua temuan QA terverifikasi.** P2 (flag ETL diteruskan compose dan divalidasi) dan P3 (`order_uuid` dikirim Odoo
  di submit/status/event dan divalidasi UUID oleh BFF).
- **Verdict: GO merge.** Poin QR, poin online, dan hangus tetap OFF di production. **Pemblokir `FNB_QR_POINTS_ENABLED=true`:
  SEC-174, keputusan SEC-169, hasil test Odoo hijau, dan SEC-175 (disarankan).** QR pesan-saja boleh dipilot.

## Tabel status temuan r15

| ID | Sev asal | Status | Bukti perbaikan (file:baris) | Test yang benar-benar menguji |
|---|---|---|---|---|
| SEC-164 | Medium | **Fixed** (menunggu hasil test Odoo) | Odoo: `efn_pos_bff_order/models/efn_pos_table_session.py:140-216` (neto refund, neto ≤ 0 dibuang), `:255-306` (flag setelah 2xx, refund `awaiting` → reverse, alert); `models/efn_pos_qr_event_outbox.py:247-259, 288-302, 314-348` (bekukan dulu, POST body beku, kunci sesi di savepoint); `models/pos_order.py:211-247` (`_efn_qr_refund_mode`); `efn_pos_loyalty_ledger/models/pos_order.py:574-647` (reverse hanya mode `reverse`, rasio terhadap neto snapshot). BFF: `pos/pos-loyalty.service.ts:622-659` (`earn_pending` bila order QR dikenal dan close belum diproses); `fnb/fnb-pos.service.ts:342-343` + `fnb.repository.ts:251-259` (`close_event_at` di transaksi earn); migrasi `11-platform-g22.sql:63-64, 112, 123-124` | Odoo (`tests/test_qr_session.py`): `test_refund_before_close_is_netted_without_reverse`, `test_refund_after_close_before_first_attempt_is_netted`, `test_frozen_snapshot_reused_and_awaiting_refund_reversed_on_2xx`, `test_awaiting_refund_kept_and_alerted_when_close_fails`, `test_full_refund_before_close_excluded`, `test_refund_after_close_ratio_against_net`. Hanya HTTP yang di-mock; ORM/ledger asli → test bermakna. BFF (`pos/pos-loyalty.spec.ts:1139, 1155`): memakai `fake-loyalty-db.ts`, jadi SQL `resolveQrMember` tidak diuji terhadap PostgreSQL. Jalur bentrok serialisasi (`session_busy`) **tidak diuji** (diakui builder). |
| SEC-165 | Medium | **Fixed** | Odoo: `pos_order.py:191-209` (attached = ledger involved ATAU partner ber-`member_ref`), `efn_pos_table_session.py:205-214` (field selalu dikirim; hanya order `false` yang di-earn); BFF: `fnb-pos.service.ts:146-151, 398-402` (field absen → tidak earn + anomali critical, fail-closed) | Odoo `test_pos_member_attached_not_points_eligible`, `test_13_pay_closes_session_and_signed_event` (payload memuat `pos_member_attached:false`); BFF `fnb.spec.ts:568` "K2/SEC-165" (`ctx.earn.calls` kosong). |
| SEC-166 | Medium | **Partial** | `efn_pos_table_session.py:110-125` (`_efn_may_attach`: sesi `open`, `date_order ≥ opened_at`, wajib baris kiriman QR sesi itu); `pos_order.py:174-188` (refund dan order yang belum tertaut tidak diserap). Order tamu berikutnya tanpa QR tidak lagi masuk. **Belum tertutup:** order yang sudah tertaut (jangkar/carried) tetap dihitung walau dibayar setelah sesi `expired`, dan cetak ulang membawa order yang sudah dibayar → **SEC-174** | Odoo `test_sec166_pending_submission_next_guest_not_absorbed` (sesi open dan expired; nyata). Tidak ada test untuk cetak ulang atau order jangkar yang dipakai ulang. |
| SEC-167 | Low | **Fixed** (residu Info SEC-176) | `pos_config.py:215-238` (MAX(print_no)+1 setelah `touch_row` meja, batas 99 999 999), `:338-350` (respons BFF yang menunjuk sesi Odoo yang sudah ada → ditolak, sesi open tidak ditutup); BFF `OPEN_KEY_RE` ≤ 8 digit | Odoo `test_sec167_print_no_monotonic_after_retention`, `test_sec167_bff_pointing_to_existing_session_rejected`; BFF `fnb.spec.ts:530`. |
| SEC-168 | Low | **Fixed** (residu kecil) | BFF `fnb/fnb.model.ts:153-202`; Odoo `models/qr_common.py:42-149`. Urutan identik: NFKC → hapus rentang format → kontrol → spasi → ≤ 140 code point → `@` / Σ Nd ≥ 8 / pola ASCII lama | BFF `fnb.spec.ts:502`, Odoo `test_qr_submit.py:320` `test_10b_note_pii_filter_vectors`. Kedua set vektor **tidak identik** (bukan fixture bersama), tetapi mencakup kelas yang sama. Residu: karakter Cf lain (U+00AD, U+061C *Arabic Letter Mark* = kontrol bidi, U+180E, karakter tag U+E00xx) tidak dihapus. Dampaknya hanya spoofing tampilan KDS minor; jumlah digit tetap menahan nomor HP. |
| SEC-171 | Low | **Fixed** | `config/app-config.ts:1903-1911` (default `true`; nilai salah ketik → `ConfigError`, bukan diam-diam `false`); `loyalty/loyalty-inactivity.job.ts:184, 206-212, 544-556` (consent `marketing` terbaru = granted, member aktif; tanpa consent → notice tetap + bukti `in_app`, tanpa WA); migrasi `11-platform-g22.sql:270-281`; compose `docker-compose.yml:1229`; `deploy-validate.sh:50-54, 454-467` (`false` di production hanya dengan `EFN_ALLOW_FEATURE_ON`) | `loyalty-inactivity.job.spec.ts:235, 256` (konfigurasi asli + store fake); `scripts/host/test-efn-deploy.sh` 179b/179c, 251-255. SQL consent dibaca manual: kolom cocok dengan `03-platform-v3.sql:32-43`. |
| SEC-173 | Info | **Fixed** | `fnb/fnb-pos.service.ts:312-319` (limit per `config_id` di handler, jadi selalu setelah `ServiceKeyGuard` + `FnbEventHmacGuard`, `fnb.controller.ts:81-99`, dan sebelum `claim` → 429 tidak memakan kunci idempotensi); default `app-config.ts:2073` | `fnb.spec.ts:624` "SEC-173" (429 lalu retry `applied`, bukan `duplicate`). |
| SEC-169 | Low | **Tertunda (L-82 b), Open** | Kode klaim tidak berubah (benar, sesuai keputusan). `FNB_QR_POINTS_ENABLED` default `false`: `app-config.ts:1804`, `docker-compose.yml:1192`, `.github/workflows/deploy.yml:310`, `scripts/deploy.sh:392`, `.env.example:162`. Belum ada gerbang host → SEC-175 | — |
| SEC-170 | Low | **Tertunda (L-82 a), Open** | `ONLINE_REDEEM_STEPUP_MIN_POINTS` **tidak ada** di `services/api-gateway/src` (grep nihil). Klaim kontrak sudah diralat (`docs/04:1235`). `ONLINE_LOYALTY_REDEEM_ENABLED` default `false`: `app-config.ts:1866`, `docker-compose.yml:1208`, `deploy.yml:320`, `deploy.sh:402`. Belum ada gerbang host → SEC-175 | — |
| SEC-172 | Low | **Diterima (L-82 c)**, residu sebagian tercatat | Tercatat di R-S16 (`docs/01:210`). README `efn_setup_role` **belum** mencatat residu/kompensasi: baris `README.md:35` hanya menyebut alasan, tanpa review akses bulanan dan tanpa opsi SoD → SEC-177 | `efn_setup_role/tests/test_setup_role.py:36, 248` |

### Verifikasi temuan QA G22

| QA | Status | Bukti |
|---|---|---|
| **P2** flag ETL G21/G22 tidak diteruskan compose | **Terverifikasi diperbaiki** | `infra/compose/docker-compose.yml:1630-1637` (empat flag, default `false`); `docker-compose.dev.yml:198-199` (sengaja tidak dipaksa `true`); `scripts/lib/deploy-validate.sh:42` (variabel rilis), `:242-246` (boolean), `:275-288` (prasyarat = cermin `efn_etl/config.py:259-275`); `scripts/deploy.sh:287-294, 418-421`; `deploy.yml:338-341`; `check-encryption.sh:285`. Test: `test-efn-deploy.sh` 179e-179h, 259-269. |
| **P3** `order_uuid` per kiriman tidak pernah terisi | **Terverifikasi diperbaiki (K1)** | Odoo: hasil submit `efn_pos_qr_order.py:446-449`, replay hasil lama `:376-377`, `efn_qr_status` `:571, 573-579`, event `efn_pos_qr_event_outbox.py:98-99, 150, 182-186`. BFF: `odoo-qr` `parseQrSubmit`/`parseQrStatus` menolak nilai non-UUID (`fnb.spec.ts:536-544`); event `fnb-pos.service.ts:189-194`; diisi saat submit/rekonsiliasi `fnb.service.ts:340-344, 437-441` dan dari event bila masih kosong, uuid berbeda → anomali (`fnb-pos.service.ts:361-376`). Test Odoo `test_01_…` (:76-79), `test_08_idempotency` (:281), `test_11_…` (:397-404). BFF masih menerima `order_uuid` absen (kompatibilitas Odoo lama, `fnb.spec.ts:424`). Ini wajar karena earn tidak bergantung padanya. |

## Temuan baru

| ID | Sev | Area | File:baris | Risiko | Pemblokir |
|---|---|---|---|---|---|
| SEC-174 | Medium (PLAUSIBLE) | Cetak ulang QR memindahkan order **yang sudah dibayar** ke sesi baru; sesi lama ditutup tanpa event | `efn_pos_bff_order/models/pos_config.py:356-361, 379`; `efn_pos_table_session.py:140-143, 187-216, 309-319`; kontrak `docs/04:1117-1120` | R-B12, R-B13 | Aktivasi `FNB_QR_POINTS_ENABLED` |
| SEC-175 | Low | Pemblokir aktivasi L-82 tidak ditegakkan teknis (poin QR & redeem online bisa ON di production lewat variabel CI) | `scripts/lib/deploy-validate.sh:50`; `.github/workflows/deploy.yml:310, 320` | R-B12, R-B13, R-S16 | Disarankan sebelum pilot QR |
| SEC-176 | Info | Reset `print_no` ke 1 setelah 400 hari tidak aman bila retensi sesi BFF > 400 hari | `pos_config.py:218-220`; `api-gateway/src/config/app-config.ts:1797`; `deploy-validate.sh:415` | R-T20 | — |
| SEC-177 | Info | README `efn_setup_role` tidak memuat residu SEC-172 yang dijanjikan L-82 c / R-S16 | `efn_setup_role/README.md:35, 75-81`; `docs/01:210` | R-S16 | — |

### SEC-174 (Medium, PLAUSIBLE): Cetak ulang membawa order dibayar → poin tamu sebelumnya jatuh ke pengklaim baru

**Mekanisme.** Di `efn_qr_session_open` jalur cetak ulang, `carried_orders = current.order_ids`
(`pos_config.py:360`) memuat **semua** order sesi lama, termasuk yang sudah `paid`. Semuanya lalu ditulis ke sesi baru
(`:379`). Sesi lama ditutup `reissued` dengan `emit=False` (`:361`), jadi tidak ada event `session_closed` untuk
sesi itu. Saat sesi baru ditutup `paid`, `_efn_paid_orders()` (`efn_pos_table_session.py:140-143`) memasukkan order lama
tersebut ke `close.orders[]`. BFF lalu meng-earn semuanya untuk member yang mengklaim **sesi baru**
(`fnb-pos.service.ts:403-413`). `markCloseProcessed` hanya menandai sesi pendahulu, tidak meng-earn untuk pengklaimnya.

**Skenario.** Rombongan 1 (member X mengklaim sesi A) membayar Rp1,5 juta. Satu kiriman QR `pending_confirmation`
tertinggal sebagai draft, sehingga sesi A tetap `open`. Ini prasyarat yang sama dengan SEC-166. Rombongan 2 duduk dan
pelayan menekan "Cetak QR". POS menjawab "Meja ini sudah punya QR aktif", lalu pelayan memilih cetak ulang (perilaku
normal). Order Rp1,5 juta milik rombongan 1 pindah ke sesi B. Member Y dari rombongan 2 mengklaim sesi B dan
mendapat poin atas tagihan rombongan 1, sedangkan X tidak mendapat apa pun. Varian insider: pelayan sengaja
menyisakan draft lalu mencetak ulang agar poin meja berpindah ke rekan (dibatasi plafon earn harian/per order).

**Varian terkait (sisa SEC-166):** order jangkar atau carried yang sudah tertaut tetap dihitung walau dibayar jauh
setelah sesi `expired`. Contohnya order jangkar kosong yang kemudian dipakai pelayan untuk tamu berikutnya. Rekomendasi r15
(b) "bekukan daftar order saat expired" belum diterapkan. Status PLAUSIBLE karena bergantung pada perilaku POS 19
(apakah draft kosong tersinkron dan dipakai ulang).

**Rekomendasi.**
1. Saat cetak ulang, bawa hanya order `draft`: `current.order_ids.filtered(lambda o: o.state == "draft")`. Order yang
   sudah dibayar tetap di sesi lama.
2. Bila sesi lama punya order dibayar, kirim event `session_closed reason=paid` untuk sesi lama, berisi order dibayar
   miliknya. BFF sudah menerima close untuk sesi berstatus `closed reissued`, dan `applyOrder` meng-earn ke pengklaim
   lama. Cara lain: tutup sesi lama `paid` lebih dulu, lalu buka sesi baru.
3. Di `_efn_close_orders`, keluarkan order yang dibayar setelah `expires_at` sesi (+ toleransi kecil), atau
   bekukan anggota sesi saat cron `expired`.
4. Tambahkan test Odoo: "bayar → draft tersisa → cetak ulang → close sesi baru tidak memuat order yang dibayar
   sebelumnya" dan "order jangkar dibayar setelah expired tidak dilaporkan".
5. Ralat kontrak §14.A.4 syarat 3: *carried* = order **draft** dari sesi sebelumnya.

### SEC-175 (Low): Pemblokir aktivasi L-82 hanya prosedural

L-82 (a)/(b) menyatakan poin QR dan redeem online tetap OFF sampai syarat re-review/G23 terpenuhi. Namun
`EFN_GATED_FEATURES` (`deploy-validate.sh:50`) hanya memuat `LOYALTY_EXPIRY_ENABLED`, `LOYALTY_INACTIVE_ANONYMIZE`,
dan `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT`. `FNB_QR_POINTS_ENABLED` dan `ONLINE_LOYALTY_REDEEM_ENABLED` diambil dari
variabel repo CI (`deploy.yml:310, 320`) dan hanya divalidasi sebagai boolean (`deploy-validate.sh:406-409`).

**Skenario:** pemegang akses CI (atau salah konfigurasi variabel) mengeset `FNB_QR_POINTS_ENABLED=true`. Manifest
bertanda tangan diterima di production, sehingga jalur SEC-174/SEC-169 aktif tanpa keputusan Superadmin. Pola untuk fitur
hangus sudah ada dan teruji. Ini tidak sama dengan memaksa default, karena variabel kosong tetap `false`.

**Rekomendasi:** tambahkan kedua variabel ke `EFN_GATED_FEATURES` (production `true` hanya dengan
`EFN_ALLOW_FEATURE_ON` di `/etc/efn/deploy.conf`), beserta test `test-efn-deploy.sh` yang sejenis dengan 179b/179c.
Opsional: `ONLINE_LOYALTY_EARN_ENABLED` juga.

### SEC-176 (Info): `print_no` kembali ke 1 vs retensi sesi BFF

Docstring `pos_config.py:218-220` menyatakan reset ke 1 aman "karena kunci `fnb_open` di BFF disimpan 180 hari".
Itu hanya nilai default. `FNB_SESSION_RETENTION_DAYS` diterima sampai 730 (`deploy-validate.sh:415`) dan 3650 di BFF
(`app-config.ts:1797`). Bila retensi BFF > 400 hari dan sebuah meja tidak dipakai > 400 hari, kunci
`fnb_open:<c>:<t>:1` masih ada di BFF. Akibatnya bisa replay sesi lama (yang sudah `closed`, sementara Odoo tidak lagi
punya record-nya sehingga pemeriksaan `existing` lolos) atau `409 idempotency_conflict`. Dampaknya hanya ketersediaan
QR meja itu, tanpa kebocoran data.

**Saran:** batasi `FNB_SESSION_RETENTION_DAYS` < 400 di `deploy-validate.sh` dan `app-config`, atau Odoo menolak
respons buka sesi dengan `expires_at` di masa lalu (saat ini nilainya diterima dan sesi dibuat sudah kedaluwarsa,
`pos_config.py:353-355`).

### SEC-177 (Info): Residu SEC-172 belum ada di README modul

L-82 c dan R-S16 (`docs/01:210`) menyatakan "residu dicatat di README `efn_setup_role`". Isi README saat ini hanya
"stock user: batal SO saat purchase_stock terpasang" (`README.md:35`). Kompensasi (review akses bulanan pemegang peran +
sampel transfer yang mereka validasi) dan opsi SoD G23 belum tercatat. **Saran:** tambahkan butir di "Belum / belum
terverifikasi" atau bagian "Risiko residu".

## Pencarian regresi perbaikan r15 (butir 4)

| Area | Hasil |
|---|---|
| Fail-open | Tidak ditemukan. `pos_member_attached` absen → tidak earn (`fnb-pos.service.ts:400-402`). Consent tanpa nilai sah → `ConfigError`. Secret HMAC hilang → retry, tidak pernah unsigned (`efn_pos_qr_event_outbox.py:238-243`). Event close gagal final → refund `awaiting` tetap bertanda + alert. |
| PII di log/event | Tidak ditemukan. Log Odoo baru hanya id/uuid (`efn_pos_table_session.py:299-306`, ledger `pos_order.py:591-593`). Log BFF `EFN_LOYALTY_QR_REVERSE` hanya `order_ref` yang disanitasi. `pos_member_attached` hanya boolean; HP partner dibaca `partner_member_ref` tanpa log. Heartbeat hangus hanya hitungan. |
| Idempotensi | Benar. Body close beku → retry identik, BFF replay `duplicate` 200. Rate limit dipasang sebelum `claim`, jadi 429 tidak membakar kunci. Kunci reverse QR `pos_reverse:<refund.uuid>` tetap. Satu catatan: refund yang menunjuk dua order asal sesi (jarang) memanggil `_efn_loyalty_enqueue_qr_reverse` dua kali dengan kunci sama (`efn_pos_table_session.py:284-285`). Perilaku `_efn_enqueue` saat ini tidak saya verifikasi, PLAUSIBLE dan dampaknya rendah. |
| SQL / permission multi-company | Tidak ada SQL dinamis baru dengan input klien (`touch_row` memakai `SQL.identifier`). Pencarian `sudo()` baru (`_efn_order_refunds`, `_efn_awaiting_refunds`, pencarian `uuid in` di `_efn_mark_close_sent`) dibatasi oleh order sesi itu sendiri, sehingga tidak melintasi PT. Field server baru (`efn_qr_eligible_amount`, `efn_qr_refund_netted`, `efn_qr_refund_awaiting`) masuk `ORDER_SERVER_FIELDS` (`pos_order.py:35-43`) dan dibuang dari klien. Migrasi 11: kolom baru tercakup `REVOKE`/`GRANT` tabel (`:312-319`), `efn_readonly` tidak mendapat kolom baru. |
| Sudo melebar | Tidak ada. Satu pengamatan (bukan regresi r15): `PosOrderLine.create` menerima `efn_qr_submission_ref` dari klien POS bila kiriman berada di config yang sama (`pos_order.py:299-316`). Pelayan yang memalsukan RPC bisa menempelkan ref kiriman ke order lain di meja yang sama agar lolos `_efn_may_attach`. Ini insider ber-hak POS, dibatasi meja dan sesi `open`. Dicatat untuk G23 bersama SEC-169, tanpa ID. |
| Konkurensi K3 | Desain benar (UPDATE no-op memaksa `SerializationFailure` di REPEATABLE READ, dan sisi yang kalah diulang). Jalur bentrok **tidak teruji** di test (butuh dua transaksi nyata). Disarankan test integrasi dua kursor sebelum aktivasi poin QR. |

## Kualitas test (bukan mock yang selalu lolos?)

- Test Odoo r15 memakai ORM, POS, dan ledger asli. Hanya `requests.post` ke BFF yang di-patch, jadi assertion-nya
  bermakna. **Namun beberapa assertion bersyarat:** `if ledger:` / `if reverses is not None:`
  (`test_qr_session.py:188-190, 203-204, 214-216, 237-255`). Bila `efn_pos_loyalty_ledger` tidak terpasang di DB test,
  bagian K3 terpenting lolos tanpa diuji. **Pastikan `EFN_TEST_MODULES` CI memasang kedua modul**, dan ganti cabang
  bersyarat dengan `skipTest` agar terlihat di laporan.
- Test BFF fnb/pos-loyalty memakai repo/DB fake. Logika service teruji, tetapi SQL `resolveQrMember`,
  `markCloseProcessed`, dan `hasMarketingConsent` hanya saya periksa manual terhadap skema (cocok). Belum ada test
  PostgreSQL nyata.
- **Test Odoo belum ada hasilnya** (sedang berjalan menurut lead). Status Fixed untuk SEC-164/165/166/167/168 di sisi
  Odoo bergantung pada hasil itu. BFF Jest 830/0, `test-efn-deploy` 289/0, dan check-encryption OK sesuai laporan lead (tidak saya jalankan).

## Verdict

**GO merge** untuk `chore/wave-22`. Tidak ada Critical/High, tidak ada fail-open, dan tidak ada kebocoran PII. Semua jalur
poin baru OFF di production secara default.

**Pemblokir aktivasi yang masih berlaku:**

| Sakelar | Pemblokir |
|---|---|
| `FNB_QR_POINTS_ENABLED=true` | **SEC-174** diperbaiki + test; keputusan **SEC-169** (L-82 b); test Odoo r15 hijau (termasuk dengan `efn_pos_loyalty_ledger` terpasang); disarankan **SEC-175** (gerbang host) dan test bentrok K3 |
| `ONLINE_LOYALTY_REDEEM_ENABLED=true` | **SEC-170** (step-up + notifikasi, G23, L-82 a); disarankan SEC-175 |
| `LOYALTY_EXPIRY_ENABLED=true` | Teks syarat Legal + template Meta (L-73). SEC-171 sudah Fixed; `REQUIRE_CONSENT=false` hanya lewat `EFN_ALLOW_FEATURE_ON` |
| QR pesan-saja (`FNB_QR_ENABLED=true`, poin OFF) | Boleh dipilot. Syarat konfigurasi L-82 d (`efn_qr_session_hours × 3600 ≥ FNB_TABLE_SESSION_TTL_SECONDS`); SEC-176 dicatat untuk sebelum hari ke-400 |
| `efn_setup_role` | SEC-172 diterima; lengkapi README (SEC-177) |

## Kontrol yang sudah benar (ringkas)

- K3 dirancang tanpa jendela: snapshot dibekukan di bawah kunci baris sesi, POST di putaran terpisah, flag poin hanya
  setelah 2xx, refund tercermin tepat sekali (neto ATAU reverse), dan close gagal → alert `EFN_QR_REFUND_UNRESOLVED`.
- BFF memproses earn + klaim event + `close_event_at` dalam satu transaksi. Urutan baca di `resolveQrMember` sengaja
  dibuat agar tidak ada "close terlihat, earn belum".
- Respons BFF buka sesi yang menunjuk sesi Odoo lama ditolak sebelum sesi open disentuh.
- Filter catatan BFF dan Odoo kini satu aturan, dengan urutan yang terdokumentasi.
- Consent hangus default aman, dan salah ketik tidak bisa mematikan filter.
- Rate limit event dipasang setelah kedua guard dan sebelum `claim`.
- Batas body 16 KB / 100 order sama di kedua sisi.

## Belum diverifikasi oleh reviewer
- Hasil test Odoo (belum ada). Perilaku runtime POS 19 untuk order jangkar kosong (SEC-174 varian) dan `date_order`
  saat bayar.
- Frontend tidak dibaca ulang untuk r15 (di luar fokus; Vitest 469/0 menurut lead).
- Perilaku `efn.pos.loyalty.outbox._efn_enqueue` untuk kunci ganda (lihat tabel regresi).

## Verifikasi perbaikan (20 Okt 2026)

Lingkup: working tree `chore/wave-22` (belum di-commit), keputusan lead **L-84** (`docs/04_SERVICE_CONTRACTS.md:1413`).
File dibaca langsung. Hasil test berasal dari laporan lead dan tidak saya jalankan: Odoo 883/0, PII 978/0, ulang
`efn_pos_bff_order` 47/0, efn-deploy 295/0, Jest 96/0.

### Status

| ID | Status | Bukti | Catatan |
|---|---|---|---|
| SEC-174 | **Fixed** (jalur cetak ulang). Varian `expired` **tetap Open**, terdokumentasi | `pos_config.py:356-371`: `carried_orders` hanya berisi order `draft`. Sesi lama yang punya order dibayar ditutup lewat `_efn_close("paid")`, yaitu outbox biasa plus snapshot L-83. Tanpa order dibayar, sesi lama ditutup `reissued` dengan `emit=False`. Penutupan terjadi setelah BFF menjawab 201 dan setelah cek `existing` (`:338-350`), jadi bila BFF gagal, sesi lama tetap open. | Test `test_sec174_reissue_keeps_paid_orders_on_old_session` (`test_qr_session.py:356-403`) memakai ORM dan outbox asli. Test ini memeriksa order dibayar tetap di sesi A, snapshot dan body POST sesi A hanya berisi order itu, flag poin dipasang setelah 2xx sesi A, close sesi B hanya berisi `fresh`, dan close A tidak dikirim ulang. Test kedua (`:405-419`) memastikan tanpa order dibayar tidak ada event, dan order batal tetap di A. |
| SEC-175 | **Fixed** | `deploy-validate.sh:50-53`: `FNB_QR_POINTS_ENABLED` dan `ONLINE_LOYALTY_REDEEM_ENABLED` masuk `EFN_GATED_FEATURES`, sehingga di production `true` hanya diterima dengan `EFN_ALLOW_FEATURE_ON` (`:459-471`). Nama-nama ini otomatis lolos validasi daftar izin (`:186`). | Test 210a–d (`test-efn-deploy.sh:637-640`). Test 179 (`FNB_QR_ENABLED` sebagai nama tak dikenal) tetap valid. `ONLINE_LOYALTY_EARN_ENABLED` tidak di-gate (opsional, diterima). |
| SEC-176 | **Fixed** (residu Info, lihat SEC-178) | Batas maks 400 di `app-config.ts:1796-1800`, `deploy-validate.sh:418`, dan `.env.example:170`. | Test 210e/f. Konfigurasi lama yang berisi nilai > 400 sekarang gagal validasi atau start. Ini disengaja, tetapi perlu dicek di variabel CI production sebelum rilis. |
| SEC-177 | **Fixed** (catatan format, lihat SEC-179) | `efn_setup_role/README.md:39-41` mencatat residu, alasan penolakan `sudo()`, dan kompensasi review akses bulanan beserta transfer yang divalidasi. | Opsi SoD G23 tidak disebut. Ini minor. |

### Pertanyaan lead tentang SEC-174 (jalur lain)

1. **Order jangkar.** Jangkar sesi lama yang masih `draft` ikut pindah ke B dan meng-earn untuk pengklaim B bila dibayar
   di sana. Ini sesuai desain L-70 (tagihan belum dibayar tetap satu siklus). Jangkar yang sudah dibayar tetap di A.
   **Sisa terbuka:** pada sesi yang sudah `expired` (bukan `open`), cetak ulang tidak menyentuh sesi itu
   (`current` hanya mencari `state=open`). Order jangkar atau carried yang masih tertaut ke sesi expired dan dibayar
   belakangan tetap dilaporkan untuk pengklaim lama. Arah poinnya kini kebalikan dari skenario awal: tamu baru
   memberi poin ke X, bukan Y. Status ini Open dan sudah tercatat di README `efn_pos_bff_order:209-210` serta L-84.
   Tetap menjadi pemblokir poin QR bersama SEC-169.
2. **Refund setelah cetak ulang.** Order dibayar milik A tetap punya `efn_qr_table_session_id = A`. Sesi A `closed`
   dengan close `pending`, sehingga `_efn_qr_refund_mode` (`pos_order.py:234-247`) mengembalikan `deferred` (snapshot
   belum beku) atau `awaiting` (snapshot sudah beku). Reverse baru dikirim setelah close A mendapat 2xx, dan BFF
   me-reverse dari catatan earn, yaitu ke member X. Hasilnya benar.
3. **Event `submission_status` sesi lama.** Kiriman yang order-nya ikut pindah tetap tercatat di
   `efn.pos.qr.order.table_session_id = A`, sehingga event statusnya tetap menunjuk sesi A di BFF. `applySubmission`
   (`fnb-pos.service.ts:352-379`) hanya mengubah status dan `order_uuid`, tanpa earn. `seq` tetap naik karena
   `_efn_next_seq` berbasis epoch. Tidak ada jalur poin di sini.
4. **BFF menerima close `paid` untuk sesi `reissued`.** `handleEvent` (`fnb-pos.service.ts:320-345`) tidak memeriksa
   status sesi. `applyOrder` meng-earn ke `s.member_id` sesi A, yaitu pengklaim lama. Status tidak diubah karena sesi
   sudah tidak `open`, lalu `markCloseProcessed(A)` dijalankan. `member_id` A masih ada, karena pelepasan tautan
   baru terjadi `FNB_SESSION_MEMBER_LINK_DAYS` setelah `closed_at`, sedangkan close tiba dalam hitungan menit.
   Hasilnya benar. Satu-satunya cakupan BFF untuk kasus ini adalah test lama `fnb.spec.ts:604-607`. Tidak ada test
   BFF baru yang khusus untuk "close paid pada sesi yang sudah `reissued`". Disarankan menambahkannya (Info).

### Temuan baru

| ID | Sev | Area | File:baris |
|---|---|---|---|
| SEC-178 | Info | `markCloseProcessed` menandai pendahulu `reissued` saat close sesi pengganti. Asumsinya ("Odoo tidak mengirim close untuk sesi yang diganti") sudah tidak benar setelah SEC-174 | `fnb.repository.ts:76-81, 251-258`; `fnb-fakes.ts:121-128` |
| SEC-179 | Info | Rujukan footnote SEC-172 berada di sel ekstra tabel, sehingga di GFM sel itu dibuang dan catatannya tidak tampil di tampilan GitHub | `efn_setup_role/README.md:35, 39` |

**SEC-178 (Info).** Di BFF, sesi A berstatus `closed reissued`. Bila close B diproses lebih dulu daripada close A
(misalnya close A sedang di-retry karena 5xx), `close_event_at` A ikut terisi padahal earn A belum terjadi. Dalam
jendela itu, `resolveQrMember` untuk order A melihat `close_processed=true` tanpa catatan earn dan akan menjawab 200
nol, bukan `earn_pending`. Akibatnya lapis kedua K3 melemah. **Tidak dapat dieksploitasi** karena lapis pertama di
Odoo menahan reverse: refund atas order A berstatus `deferred`/`awaiting` sampai close A mendapat 2xx (`pos_order.py:232-247`).
Saran: perbarui komentar antarmuka. Bila mungkin, tandai pendahulu hanya bila sesi pendahulu tidak punya order yang
dikenal sebagai dibayar (atau cukup diterima sebagai residu), dan sinkronkan fake-nya.

**SEC-176 residu (bagian SEC-178/Info, tanpa ID terpisah).** Batas 400 sama dengan retensi Odoo, bukan lebih kecil.
Odoo menghapus berdasarkan `opened_at`, sedangkan BFF berdasarkan `COALESCE(closed_at, expires_at)`
(`fnb.repository.ts:457-461`), yang beberapa jam lebih lambat. Untuk meja yang hanya pernah punya satu sesi dan tidak
dipakai tepat ±400 hari, ada jendela sekitar TTL ditambah interval cron. Di jendela itu kunci `fnb_open:<c>:<t>:1`
masih ada di BFF. Dampaknya hanya ketersediaan: QR mati atau `409`. `expires_at` di masa lalu dari replay BFF masih
diterima (`pos_config.py:353-355`). Saran: batas 395, atau tolak `bff_expires < now`.

### Regresi

- **Tidak ditemukan regresi fungsional atau keamanan.** Urutan baru di cetak ulang aman: `_efn_close("paid")` mengunci
  baris sesi A dan mengantrekan close. Snapshot awal hanya memuat order dibayar, sehingga draft yang dipindah sesudahnya
  tidak memengaruhi isinya. Pembekuan di putaran outbox membaca `order_ids` A setelah pemindahan dan tetap hanya berisi
  order dibayar. Sudo yang dipakai masih terbatas pada order sesi itu sendiri. Log `EFN_QR_SESSION_REISSUE_PAID` hanya
  berisi id, tanpa PII.
- `assertLogs` di tiga test negatif (`test_qr_session.py:443, 471, 537`) juga memastikan log ERROR benar-benar
  muncul. Jadi assertion bertambah, bukan disembunyikan.

### Verdict (20 Okt 2026)

**GO merge.** SEC-174 (jalur cetak ulang), SEC-175, SEC-176, dan SEC-177 tertutup. Pemblokir
`FNB_QR_POINTS_ENABLED=true` yang tersisa:
- SEC-169 (G23);
- varian `expired` SEC-174 (order tertaut yang dibayar setelah expired);
- test bentrok serialisasi K3;
- izin `EFN_ALLOW_FEATURE_ON` dari Superadmin (kini ditegakkan secara teknis).

SEC-178/179 (Info) boleh dikerjakan di G23.
