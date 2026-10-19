# Security Review r15 — Gelombang 22 / paket restoran (branch `chore/wave-22`, working tree belum di-commit)

Tanggal: 19 Okt 2026 · Reviewer: efn-security-reviewer (read-only, tanpa shell; working tree dibaca langsung, bukan
`git diff`) · Nomor temuan lanjut dari SEC-163.
Kontrak: docs/04 §14 (A–E) + §14.7 L-70..L-81; risiko R-B12, R-B13, R-T20, R-L11, R-S16; PDP inventory v8 / DPIA §13.

## Ringkasan eksekutif

- **Tidak ada temuan Critical/High.** Permukaan baru QR meja tertutup rapat: token `fts1` HMAC (HKDF, kanonik,
  diperiksa sebelum DB, hanya header), user teknis Odoo fail-closed, harga & order dibangun server, `_get_open_order`
  baru mencegah kiriman QR menimpa order pelayan, event Odoo→BFF bertanda tangan tanpa fallback unsigned.
- **3 Medium — semuanya di jalur poin QR (`FNB_QR_POINTS_ENABLED`, default OFF):** poin di-earn atas order yang sudah
  di-refund (SEC-164, jawaban butir 4 lead), `pos_member_attached` tidak pernah dikirim Odoo sehingga klaim QR berebut
  dengan earn OTP kasir (SEC-165), dan sesi meja yang tak tertutup "menyerap" order tamu berikutnya di meja yang sama
  (SEC-166, PLAUSIBLE).
- **6 Low + 1 Info:** kunci idempotensi buka sesi berbasis hitungan (rusak setelah retensi 400 hari), filter PII catatan
  mudah dilewati, klaim poin "siapa cepat" tanpa koreksi staf, redeem online tanpa step-up, notifikasi hangus tanpa
  saklar consent, perluasan hak Sales Manager Online, rate limit event tidak terpasang.
- **Verdict: GO merge** (QR, poin QR, poin online, hangus, anonimisasi semuanya OFF di production). **Pemblokir
  aktivasi `FNB_QR_POINTS_ENABLED=true`: SEC-164/165/166.** QR pesan-saja (tanpa poin) boleh dipilot setelah SEC-167
  dijadwalkan.

## Tabel temuan

| ID | Sev | Area | File:baris | Risiko | Pemblokir |
|---|---|---|---|---|---|
| SEC-164 | Medium | Earn QR atas order yang sudah di-refund | `efn_pos_bff_order/models/efn_pos_table_session.py:135-150`; `efn_pos_loyalty_ledger/models/pos_order.py:532-535, 575-579`; `efn_pos_bff_order/models/efn_pos_qr_order.py:427-439, 549`; `api-gateway/src/pos/pos-loyalty.service.ts:615-636` | R-B13, R-B12 | Aktivasi poin QR |
| SEC-165 | Medium | `pos_member_attached` tidak dikirim → earn QR vs OTP kasir berebut | `efn_pos_table_session.py:143-150`; `api-gateway/src/fnb/fnb-pos.service.ts:365` | R-B12 | Aktivasi poin QR |
| SEC-166 | Medium (PLAUSIBLE) | Sesi meja open/expired menyerap order tamu berikutnya | `efn_pos_bff_order/models/pos_order.py:132-158`; `efn_pos_table_session.py:165-180`; `fnb-pos.service.ts:354-381` | R-B12 | Aktivasi poin QR |
| SEC-167 | Low | Kunci idempotensi buka sesi = hitungan sesi → tabrakan setelah retensi | `efn_pos_bff_order/models/pos_config.py:260-268`; `api-gateway/src/fnb/fnb-pos.service.ts:254-257`; `fnb.model.ts:221` | R-T20 | Sebelum 400 hari produksi QR |
| SEC-168 | Low | Filter PII catatan dapat dilewati; karakter format tidak dibuang | `api-gateway/src/fnb/fnb.model.ts:145-161`; `efn_pos_bff_order/models/qr_common.py:42-43, 91-101` | R-L01 | — |
| SEC-169 | Low | Klaim poin "siapa cepat" oleh pemegang token, tanpa koreksi staf | `api-gateway/src/fnb/fnb.service.ts:463-493` | R-B12 | Aktivasi poin QR (bersama SEC-165) |
| SEC-170 | Low | Redeem poin online tanpa step-up; env kontrak belum ada | `api-gateway/src/orders/online-points.ts:137-157`; docs/04 §14.C.1 no. 4 | R-B13 | — (keputusan D-22-10) |
| SEC-171 | Low | Notifikasi hangus WA tanpa saklar consent/dasar hukum di kode | `api-gateway/src/loyalty/loyalty-inactivity.job.ts:183-204`; `notification/README.md:27` | R-L11, R-L01 | Aktivasi `LOYALTY_EXPIRY_ENABLED` |
| SEC-172 | Low | Sales Manager Online + `stock.group_stock_user` (perluasan hak) | `efn_setup_role/data/efn_role_data.xml:19-20, 111, 213, 315, 417, 519, 614` | R-S16 | Aktivasi `efn_setup_role` |
| SEC-173 | Info | `FNB_RL_EVENTS_*` didefinisikan tetapi tidak dipakai | `api-gateway/src/config/app-config.ts:2045-2055`; `fnb/fnb.controller.ts:94-99` | — | — |

## Jawaban atas lima pertanyaan khusus lead

1. **Event BFF tanpa rate limit — dapat diterima.** `/v1/pos/fnb/events` dikecualikan dari router publik
   (`docker-compose.yml:1268`), `ServiceKeyGuard` lalu `FnbEventHmacGuard` (HMAC atas raw body, jendela 300 s, 503 bila
   secret hilang, secret wajib ≠ service key & ≠ HMAC online), body ≤ 8 KB, `claim` idempoten + `seq` monoton. Penyerang
   harus sudah memegang dua secret. Limit tidak diperlukan sebagai kontrol keamanan; hanya rapikan konfigurasi mati
   (SEC-173).
2. **Tanpa step-up untuk redeem besar — risiko residu yang diterima (Low, SEC-170).** JWT 1 jam tanpa refresh token,
   `redeem_max_pct`, porsi tunai ≥ Rp1, reservasi atomik, dan kode ambil membatasi dampak. Namun `ONLINE_REDEEM_STEPUP_MIN_POINTS`
   yang dijanjikan kontrak §14.C.1 no. 4 belum ada di kode — bila klien memintanya, perlu pekerjaan baru.
3. **Consent notifikasi hangus — default OFF terpenuhi.** `LOYALTY_EXPIRY_ENABLED=false` di semua lingkungan, dan di
   production nilai `true` hanya diterima bila Superadmin mencantumkannya di `EFN_ALLOW_FEATURE_ON` host
   (`deploy-validate.sh:45-48, 433+`); manifest CI saja tidak cukup. Dry run tidak meng-enqueue pesan. Kekurangannya:
   setelah gerbang dibuka, kode mengirim ke semua member bersaldo tanpa memeriksa consent (SEC-171).
4. **Refund sebelum sesi ditutup — ya, poin dapat di-earn atas order yang sudah di-refund (SEC-164).** Jendelanya lebih
   lebar daripada yang tercatat di README: perlindungan `earn_pending` BFF tidak pernah aktif karena BFF tidak mengenal
   `order_uuid` order QR.
5. **Menimpa order pelayan & sesi meja lain.** Perbaikan `_get_open_order` benar (lihat Kontrol yang sudah benar).
   Menebak sesi meja lain tidak realistis (id 128 bit + HMAC-SHA256, `timingSafeEqual`, tanpa oracle). Penyalahgunaan
   yang realistis adalah **memegang token sah** (slip difoto, tamu lama): memesan ke meja itu sampai sesi ditutup
   (diredam mode `waiter`) dan mengklaim poin meja (SEC-169), dan bila sesi tidak tertutup, menerima poin tamu berikutnya
   (SEC-166).

## Detail

### SEC-164 (Medium) — Poin QR di-earn atas order yang sudah di-refund
**Mekanisme.** `efn_qr_points_eligible` baru dipasang di `_efn_paid_orders_payload` saat sesi **ditutup**
(efn_pos_table_session.py:142). Refund sebelum itu: `_efn_loyalty_process_refund` (pos_order.py:532-535 ledger) melihat
flag False → **tidak ada reverse sama sekali**. Payload close tetap memuat order asal dengan `eligible_amount` penuh
(dihitung dari baris order asal, tanpa mengurangi refund) → BFF meng-earn penuh.
Jendela kedua (tidak tercatat di README): refund **setelah** close di Odoo tetapi **sebelum** BFF memproses
`session_closed` (outbox QR retry 1 m→1 j selama BFF/jaringan bermasalah, atau antre di belakang event ber-seq lebih
kecil). Reverse QR terkirim lebih dulu lewat outbox loyalty, BFF `resolveQrMember` (pos-loyalty.service.ts:615-636)
mencari `order_uuid` di `fnb_submission`/`fnb_table_session`. Kolom itu **selalu NULL** karena Odoo tidak pernah
mengirim `order_uuid`: hasil `efn_qr_submit` (efn_pos_qr_order.py:427-439) dan `efn_qr_status` (549) tidak memuatnya,
event `submission_status` juga tidak, dan body buka sesi tanpa `order_uuid` (L-76). Akibatnya BFF menjawab `200 {0,0}`
(bukan `409 earn_pending`), outbox reverse `done`, lalu event close datang dan earn penuh tercatat. Kontrol K-6 untuk QR
praktis tidak aktif.
**Skenario.** Member A mengklaim sesi meja; order Rp2.000.000 dibayar, meja masih punya draft (tamu lain/kiriman QR
tertunda). Kasir (kolusi, atau refund sah) me-refund order tersebut → tidak ada reverse. Meja kosong → close `paid` →
A mendapat poin penuh atas belanja yang uangnya sudah kembali. Varian tanpa kolusi terjadi setiap kali BFF sedang
gangguan saat refund.
**Rekomendasi.**
(a) Odoo: saat membangun payload close, hitung `eligible_amount` **neto** (kurangi `_efn_loyalty_eligible_amount(refund=True)`
semua order refund ber-state paid yang menunjuk order itu; lewati order yang sepenuhnya di-refund).
(b) Odoo: pasang `efn_qr_points_eligible` saat order dilekatkan ke sesi (`_efn_qr_after_final`), bukan saat close, dan
tunda reverse QR (state `pending`, retry) selama event `session_closed` sesi itu belum `done` di outbox QR — dapat diperiksa
lokal di Odoo tanpa perubahan kontrak.
(c) BFF/kontrak: kembalikan `order_uuid` di hasil `efn_qr_submit` dan event `submission_status` (field sudah dibaca
`parseQrSubmit`/`applySubmission`), atau sertakan `table_session_id` di reverse `earn_channel:"qr"`, supaya `earn_pending`
benar-benar terpicu.
(d) Test regresi: refund sebelum close dan refund sebelum event close diproses → total earn = neto.

### SEC-165 (Medium) — `pos_member_attached` tidak pernah dikirim; earn QR berebut dengan earn OTP kasir
Kontrak §14.A.4 dan L-80 mengandalkan `pos_member_attached=true` agar BFF tidak meng-earn order yang sudah diberi
member di kasir. `_efn_paid_orders_payload` (efn_pos_table_session.py:143-150) hanya mengirim `order_uuid`,
`eligible_amount`, dan `company_code`. BFF membaca field itu sebagai `false` (fnb-pos.service.ts:144, 365). Dua outbox
berbeda (earn POS dan event QR) kemudian berebut kunci `pos_earn:<order_uuid>`. Bila event QR tiba lebih dulu, pengklaim
QR mendapat poin, earn kasir untuk member yang benar-benar membayar dengan OTP menjadi `409` final, order masuk `review`,
dan anomali critical `qr_pos_member_conflict` menyala. Dikombinasikan dengan SEC-169 (siapa pun pemegang token bisa
mengklaim lebih dulu), ini menjadi jalur pencurian poin yang hanya bisa dipulihkan manual.
**Rekomendasi.** Kirim `pos_member_attached = bool(order.partner_id punya member_ref sah)` (atau lebih tegas: keluarkan
order ber-member POS dari `orders`). Tambahkan test lintas modul (Odoo payload ↔ parser BFF) agar kunci kontrak yang
"opsional" di parser tidak hilang diam-diam lagi.

### SEC-166 (Medium, PLAUSIBLE) — Sesi meja open/expired menyerap order tamu berikutnya
`_efn_qr_after_final` (pos_order.py:149-158) melekatkan **setiap** order yang dibayar di meja + config yang sama ke sesi
`open` atau `expired` mana pun, tanpa batas waktu. Sesi hanya tertutup bila meja tidak lagi punya draft
(efn_pos_table_session.py:165-180) atau saat sesi POS ditutup. BFF meng-earn semua order di payload close tanpa
memeriksa waktu (fnb-pos.service.ts:354-381).
**Skenario.** A (member, pemegang token) mengklaim poin, lalu sebelum pergi mengirim satu kiriman QR kecil. Di mode
`waiter` kiriman itu menjadi draft `pending_confirmation` di meja. Bila pelayan tidak menghapusnya (alert stale hanya
sekali), sesi tetap `open` lalu `expired`. Semua tamu berikutnya di meja itu yang tidak memakai QR membayar → order
mereka dilekatkan ke sesi A → saat tutup kasir, close `paid` membawa semua order → A mendapat poin atas tagihan orang
lain (dibatasi plafon earn harian/per order). Hal yang sama terjadi bila draft kosong/terlupa tertinggal di meja.
**Rekomendasi.** (a) Lekatkan order hanya bila `create_date ∈ [opened_at, expires_at]` dan order itu sudah ada (draft) di
meja saat sesi masih hidup; (b) saat sesi `expired`, bekukan daftar order yang memenuhi syarat; (c) auto-tolak draft QR
`pending_confirmation` yang lebih tua dari N menit ketika order lain di meja sudah dibayar, atau tutup sesi saat order
jangkar dan semua order QR sesi final; (d) BFF: tolak/anomali bila close memuat order > N atau total eligible >
`efn_qr_max_amount`.

### SEC-167 (Low) — Kunci idempotensi buka sesi berbasis hitungan → tabrakan setelah retensi
`print_no = search_count(sesi config+meja) + 1` (pos_config.py:260), kunci `fnb_open:<config>:<table>:<print_no>`.
Retensi Odoo menghapus sesi > 400 hari (efn_pos_table_session.py:199-206) sehingga hitungan turun dan `print_no` baru
sama dengan nomor sesi yang masih ada (umumnya kemarin). BFF menyimpan kunci 180 hari dan me-replay sesi lama (biasanya
sudah `closed`, fnb-pos.service.ts:254-257) atau `409 idempotency_conflict`. Odoo lalu memakai record sesi lama
(`existing`, pos_config.py:320-323) dan mencetak QR yang langsung `409 table_session_closed`. Mulai hari ke-401 hal ini
terjadi deterministik di setiap meja aktif. Selain itu `OPEN_KEY_RE` membatasi `n ≤ 9999` (fnb.model.ts:221).
Ini bukan kebocoran data, tetapi gangguan ketersediaan yang pasti terjadi (R-T20).
**Rekomendasi.** `print_no = max(print_no) + 1` dari sequence/kolom monoton per (config, meja) yang tidak ikut terhapus
(mis. counter di `restaurant.table`), atau kunci `fnb_open:<config>:<table>:<uuid4>` yang disimpan sebelum memanggil BFF.
Odoo juga sebaiknya menolak respons BFF yang menunjuk sesi Odoo ber-state non-`open`.

### SEC-168 (Low) — Filter PII catatan dapat dilewati; karakter format tidak dibuang
BFF `CONTROL_RE` (fnb.model.ts:145) hanya membuang C0/C1/U+2028/2029. Karakter zero-width (U+200B–200D), bidi
(U+202A–202E, U+2066–2069), dan BOM lolos. `PHONE_LIKE_RE` (`\d[\d\s.-]{6,}\d`) dapat dilewati dengan
`0812​3456​789`, angka fullwidth `０８１２…`, atau `0812-3456 / 789`. Odoo (`qr_common.py:42-43`) lebih sempit lagi
(hanya C0+DEL), sehingga kedua lapis tidak identik. Dampak: nomor HP tamu tersimpan di `pos.order.line.note` (90 hari)
dan tampil di KDS/printer. Karakter bidi juga dapat memutar tampilan teks di layar dapur (spoofing tampilan).
**Rekomendasi.** Normalisasi NFKC, buang kategori `\p{Cf}`/`\p{Cc}`/`\p{Zl}`/`\p{Zp}` (flag `u`), lalu tolak bila jumlah
`\p{Nd}` dalam satu jendela ≥ 8 setelah membuang non-digit. Samakan aturan BFF↔Odoo dengan vektor uji bersama (pola AC-2).
Catat filter ini sebagai upaya terbaik di DPIA §13 (huruf "O" dan "l" sebagai angka tetap lolos).

### SEC-169 (Low) — Klaim poin "siapa cepat" tanpa jalur koreksi staf
`claim` (fnb.service.ts:463-480) cukup Bearer + token meja. Pengklaim pertama menang (`409 points_already_claimed`
untuk yang lain), hanya pengklaim yang bisa melepas, dan staf tidak punya cara melihat atau mereset klaim. Slip QR
tergeletak di meja dan bisa difoto dari meja sebelah, sehingga pihak lain bisa "mengunci" poin meja itu (R-B12).
**Rekomendasi.** Setelah SEC-165 diperbaiki, sediakan jalur korektif: (a) tombol POS "reset klaim poin" (RPC Odoo → event
`claim_reset` ke BFF), atau (b) earn OTP di kasir otomatis mengalahkan klaim QR (dengan `pos_member_attached`); dan
batasi klaim ke perangkat yang pernah mengirim pesanan di sesi itu, atau ke ≤ X menit setelah kiriman pertama. Tampilkan
indikator "poin meja sudah diklaim" (tanpa identitas) di POS.

### SEC-170 (Low) — Redeem poin online tanpa step-up
`plan()`/`reserve()` (online-points.ts:137-176) hanya mengandalkan Bearer. Pengambil alih sesi (perangkat dipinjam, XSS
di masa depan) dapat menukar hingga `redeem_max_pct` saldo untuk pesanan bayar-di-kasir dan mengambilnya. Faktor peredam:
JWT 1 jam tanpa refresh, ALTCHA, kode ambil, plafon kebijakan. Kontrak §14.C.1 no. 4 menyebut
`ONLINE_REDEEM_STEPUP_MIN_POINTS` (default 0), tetapi env itu tidak ada di kode.
**Rekomendasi.** Implementasikan env tersebut (pakai `consumeStepUp` yang sudah ada, `X-Step-Up-Token` sekali pakai) agar
tersedia sebagai saklar. Minimal kirim notifikasi transaksional "poin Anda dipakai" (push/WA utility) saat commit redeem
sebagai kontrol deteksi.

### SEC-171 (Low) — Notifikasi hangus WA tanpa saklar consent di kode
Fase notice (loyalty-inactivity.job.ts:183-204) meng-enqueue `notify.push` untuk semua member bersaldo yang jatuh tempo.
Dasar hukum hanya asumsi "pelaksanaan perjanjian program" (§14.D.4), consent `marketing` tidak diperiksa
(notification/README.md:27). Gerbang production sudah kuat (Superadmin + `EFN_ALLOW_FEATURE_ON`), tetapi bila Legal
memutuskan opt-in atau Meta mengategorikan template sebagai MARKETING, perlu perubahan kode, bukan konfigurasi. Ada
risiko gerbang dibuka setelah teks syarat disetujui, sebelum kategori Meta pasti.
**Rekomendasi.** Tambah `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT` (default `true` sampai Legal memutuskan) yang menyaring
kandidat lewat consent `marketing` aktif. Member tanpa consent tetap memperoleh informasi in-app (D.6) dan due tetap
≥ 30 hari. Catat keputusan di RoPA/DPIA §13.

### SEC-172 (Low) — Peran Sales Manager Online memperoleh `stock.group_stock_user`
Untuk mengatasi `AccessError` pembatalan SO (`purchase_stock` membaca `purchase.order.line` lewat `stock.move`),
efn_role_data.xml:19-20/111 memberi seluruh hak Inventory User (memvalidasi transfer/penerimaan, membuat pemindahan) ke
peran yang tugasnya penjualan online. Peran ini kini setara Merchandiser, dan kombinasi "konfirmasi/batal SO + validasi
pengiriman" melemahkan pemisahan tugas.
**Rekomendasi.** Perbaiki di titik masalah: jalankan pembacaan terkait pembatalan dengan `sudo()` terbatas di
`efn_online_order` (setelah cek akses SO), atau beri ACL baca saja ke model yang dibutuhkan. Bila grup tetap diberikan,
tambahkan aturan SoD/report (Sales Manager + validasi picking) dan catat sebagai residu di ADR-036/efn_role README.

### SEC-173 (Info) — `FNB_RL_EVENTS_*` tidak terpasang
`FNB_RL_DEFAULTS.EVENTS` (app-config.ts:2055) tidak dipakai di mana pun (`fnbRateLimit('OPEN_CONFIG')` satu-satunya
pemakai di jalur Odoo→BFF). Hal ini tidak menjadi celah (lihat jawaban no. 1), tetapi konfigurasi tanpa efek memberi
kesan kontrol yang tidak ada. **Saran:** pasang limit per `config_id` **setelah** kedua guard (bukan sebelum, agar tidak
bisa dipakai menahan event sah), atau hapus kunci itu beserta dokumentasinya.

## Kontrol yang sudah benar (diverifikasi di kode)

**Token & API publik QR (BFF)**
- `fts1`: HKDF-SHA256 dari secret ≥ 32 byte, HMAC atas `fts1|id_hex|exp`, regex panjang tetap + cek b64url kanonik,
  `timingSafeEqual` tanpa short-circuit untuk kunci aktif dan lama, kedaluwarsa dicek, `null` seragam → `401
  table_session_invalid` generik; diperiksa sebelum DB, rate limit IP (HMAC pepper) tetap dihitung untuk token palsu.
- Token hanya dari header `X-Table-Session` (query/path tidak dibaca). Traefik men-drop header itu dari access log,
  Promtail meredaksi `fts1\.` (termasuk bentuk URL-encoded, ada fixture). Frontend menyimpan token hanya di memori modul,
  menghapus fragment/query dengan `replaceState`, `referrer: no-referrer`, `no-store`, `noindex`.
- Bearer opsional, tetapi bila ada dan tidak valid → 401 (tidak diam-diam menjadi tamu). Member harus aktif. Klaim
  menolak akun yang sedang dihapus.
- Body `additionalProperties:false`. Klien tidak bisa mengirim harga/meja/config/member. `Idempotency-Key` per sesi +
  `request_sha256` (catatan hanya di-hash). Batas kiriman/item dicek di transaksi berkunci baris. `submission_id` asing
  → 404 (API1). Kiriman yang gagal mengembalikan kuota. Transport gagal → rekonsiliasi `efn_qr_status` sebelum `failed`.
  Circuit terbuka → 503 tanpa antre.
- Respons Odoo divalidasi ketat (`parseQrMenu/Submit/Status`), pesan Odoo tidak diteruskan, API key per PT (key PT lain
  tidak pernah dipakai), placeholder `disabled` ditolak.

**Odoo `efn_pos_bff_order`**
- `_efn_qr_check_caller`: grup QR + bukan share + aktif + tepat satu company + **tanpa grup lain** (fail-closed, log
  ERROR), `AccessError` seragam, context klien dibuang kecuali `lang` ber-allowlist, baru kemudian sudo + `with_company`.
  `config_id` bool/≤0 ditolak. Probe rotasi (`config_id=0`) hanya setelah cek user dan hanya dengan daftar kosong.
- Order QR dibangun server (harga 0 → `recompute_prices`, partner kosong, tanpa `last_order_preparation_change`).
  Produk dibatasi domain menu + `self_order_available`. PTAV wajib milik template, `no_variant`, aktif, bukan
  `is_custom`, satu per atribut non-multi. Batas nominal sesi dicek setelah harga dihitung (rollback total).
- **L-81 `_get_open_order`**: uuid persis selalu menang; jalur internal QR tanpa uuid cocok → order baru (uuid4 server,
  tidak dari klien); fallback meja standar tidak pernah memilih order ber-`efn_qr_order_ref`. Kiriman QR tidak dapat
  menimpa order pelayan, dan sinkron pelayan tidak menimpa order QR.
- Field server (`efn_qr_order_ref`, `efn_qr_table_session_id`, `efn_qr_send_claimed_at`, `efn_qr_points_eligible`,
  baris `efn_qr_submission_ref`) dibuang di `_process_order` (termasuk jalur sudo self-order) dan di `create/write`
  non-sudo. Penanda internal lewat `efn_core.scope`, bukan context RPC.
- Buka sesi: grup POS user + `check_access` config + company ∈ user + sesi POS opened + meja ∈ denah + order draft
  milik config & meja, `touch_row` per meja (balapan dua pelayan). `qr_url` divalidasi regex https (dev hanya bila
  `plaintext_allowed`). QR dirender reportlab tanpa cache LRU, URL tidak disimpan/di-log/dikembalikan sebagai teks.
- Outbox: secret dibaca per kirim (≥ 32 char), tanpa secret → retry + log nama env saja, **tidak pernah unsigned**.
  TLS + CA internal dari `efn.bff.client`, `allow_redirects=False`, kode error disanitasi regex, urutan per sesi
  dijaga, `seq` berbasis waktu, body ≤ 8 KB.
- Record rule multi-company untuk 3 model baru; ACL hanya baca untuk POS manager, tanpa ACL dokumen untuk grup teknis.
  Catatan baris QR dikosongkan 90 hari (SQL kolom `note` saja); sesi 400 hari.

**Event Odoo → BFF**: `ServiceKeyGuard` → HMAC raw body jendela 300 s → parser ketat (kunci idempotensi harus cocok
dengan `table_session_id`/event/seq, ≤ 50 order, `eligible_amount` ≥ 0 & ≤ 10^10, `company_code` per order = PT sesi
→ 409 + anomali critical), `claim` + `seq` → `ignored_stale`, 404/409 di-rollback.

**Poin online**: L-79 fail-closed (redeem hanya bila kebijakan + nilai tukar dari Odoo, bukan env), nilai dihitung
server, cek min/max-pct/porsi tunai, reservasi di bawah `lockMember`, restore diverifikasi ±Rp1 lalu dibatasi sisa
redeem, clawback rasio ≤ 1 dibulatkan ke atas, earn dengan plafon per order & harian.

**Hangus/anonimisasi**: gerbang production Superadmin (`EFN_GATED_FEATURES`), dry run, pengaman massal
`MAX_PER_RUN` (abort + alert critical), cek ulang jangkar di bawah lock, blocker (reservasi/order terbuka/DSAR), notice
≥ 30 hari dijamin `due_at`, template tanpa jumlah poin (L-76), anonimisasi butuh hangus aktif.

**Data/akses**: migrasi 11 REVOKE dari PUBLIC/readonly/ocr/etl untuk tabel baru, grant `efn_readonly` per kolom non-PII.
Grant ETL KDS per kolom (tanpa label, catatan, nama produk, token stasiun). Gateway HAProxy: 3 path eksak, metode
online lain tetap ditolak Odoo per grup. Rotasi key QR: precheck user (grup QR, bukan admin/POS, satu company = PT, tidak
dipakai bersama kanal online), key dibuat `with_user` (durasi grup ditegakkan), verifikasi di container BFF dengan
mTLS + CA. `efn_audit` kini mengecualikan field `mail.activity.mixin` per nama (activity_epoch teraudit).

## Belum diverifikasi oleh reviewer
- Perilaku runtime POS 19: apakah order refund diberi `table_id` (memengaruhi payload close, tidak mengubah SEC-164),
  apakah order kosong disinkron sebagai draft (memengaruhi kemungkinan SEC-166), `mergeTableOrders` membawa
  `efn_qr_submission_ref`.
- Jalur json2 Odoo 19 untuk argumen salah (status 400 vs 422) pada probe rotasi.
- DPIA §13 / PDP inventory v8 hanya dicocokkan untuk catatan QR dan tautan member, tidak dibaca utuh. `efn_setup_role`
  hanya dibaca untuk perubahan Sales Manager Online. Spec Jest/Odoo tidak dijalankan (tanpa shell).
