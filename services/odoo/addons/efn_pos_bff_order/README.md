# efn_pos_bff_order — Pesanan QR ponsel lewat BFF (E-27)

**Versi** 19.0.1.1.0 (G22 r15) · **Lisensi** LGPL-3 · **Odoo** 19.0 Community · tanpa dependency pihak ketiga (ADR-031).

## Tujuan

Tamu restoran (PT F&B: PB, BCF) memindai **QR sesi meja** yang dicetak pelayan, memesan dari ponsel di Super-App,
lalu membayar di kasir. Odoo tidak pernah dibuka ke internet: Super-App → BFF → `odoo-rpc-gateway` → `/json/2`
(user teknis per PT). Setiap kiriman menjadi **satu `pos.order` draft baru di meja** (L-70) dengan harga & pajak
dihitung Odoo (`pos_self_order.recompute_prices`), lalu pelayan menekan *Order* (default, L-71) atau terminal POS
mengirim otomatis ke dapur (mode `auto`). Server tidak pernah membuat tiket/mencetak sendiri.

## Kontrak & keputusan

- `docs/04_SERVICE_CONTRACTS.md` §14.A (A.2 token, A.4 endpoint Odoo→BFF, A.5 RPC path 13–15) + §14.7
  **L-70, L-71, L-72, L-74, L-76** (kontrak menang atas `docs/08` §13.1b).
- ADR-032 (Accepted opsi B), ADR-023 (Odoo privat), ADR-013 (printer = browser), ADR-027 (pajak dari Odoo), ADR-031.
- Risiko dimitigasi: **R-S03** (Odoo tidak diakses langsung; token dinamis ≤ 4 jam, bukan `access_token` statis),
  **R-B01** (harga hanya dihitung server), **R-L01/R-L09** (PB1 dine-in lewat FP preset, tanpa tarif di kode),
  SEC-58 (permukaan RPC), SEC-75 (kunci self-order mobile tidak dibuka), SEC-77 (notifikasi kanal partner kasir).
  r15: **SEC-164** (neto refund + urutan refund/close, R-B13), **SEC-165** (`pos_member_attached`, R-B12),
  **SEC-166** (sesi tidak menyerap order tamu berikutnya), **SEC-167** (`print_no` monoton, R-T20), **SEC-168**
  (filter PII catatan NFKC + Cf, R-L01). Kontrak docs/04 §14 + K1–K3 r15. r15 re-review: **SEC-174** (cetak ulang
  tidak memindahkan order yang sudah dibayar ke sesi baru, R-B12/R-B13).

## Isi

| Bagian | Isi |
|---|---|
| `pos.config` (+) | `efn_qr_enabled`, `efn_qr_preset_id` (wajib `service_at='table'`, ∈ preset POS, FP company sama), `efn_qr_confirm_mode` `waiter`\|`auto` (default `waiter`), `efn_qr_session_hours` (1..4), `efn_qr_max_submissions` (**10**, L-76), `efn_qr_max_items` (150), `efn_qr_max_amount` (Rp3.000.000 PLACEHOLDER), `efn_qr_stale_minutes` (5). Constraint: POS restoran + presets + PT kode PB/BCF. Diaudit `efn_audit` (aturan `audit_rule_pos_config_qr`). Tampil di Settings POS (blok "QR ponsel Super-App"). |
| `efn.pos.table.session` | Sesi meja: **hanya** `bff_session_id` (UUID dari BFF), bukan token/URL. Satu `open` per (config, meja). Banyak order per sesi. Tutup: `paid`/`cancelled` (meja tanpa draft lagi), `closed_by_staff`, `reissued` (cetak ulang tanpa order dibayar) atau `paid` + event (cetak ulang dengan order dibayar, SEC-174); cron → `expired`. Retensi **400 hari** (L-76, cron harian). |
| `efn.pos.qr.order` | Satu record per kiriman (idempotensi `(config, idempotency_key)` + `payload_sha256`, `submission_ref` unik), status kiriman, **fasad 3 RPC BFF**. Tanpa teks catatan. |
| `efn.pos.qr.event.outbox` | Event `submission_status` / `session_closed` → BFF `POST /v1/pos/fnb/events` (HMAC `odoo_fnb_event_hmac`, seq berbasis waktu per sesi, retry 1 m→1 j, final 4xx). |
| `pos.order` / `pos.order.line` (+) | `efn_qr_order_ref`, `efn_qr_table_session_id`, `efn_qr_send_claimed_at`; baris `efn_qr_submission_ref` (ikut saat order meja digabung). Field milik server: nilai klien dibuang. `source` tetap `pos`. |
| Hook `efn.kds.ticket` | `_inherit` saja (tanpa mengubah `efn_pos_kds`): tiket dibuat/berubah → status kiriman → event. |
| POS (OWL) | Tombol **QR Meja** (menu tombol lain; **nonaktif saat offline**), dialog QR (gambar dari server) + cetak slip lewat `pos.printer.print`, toast `EFN_QR_ORDER`/`EFN_QR_STALE` (kanal partner kasir), mode `auto`: klaim → `loadServerOrders` → `sendOrderInPreparationUpdateLastChange`. |
| Cron | outbox (1 m), kedaluwarsa sesi (5 m), alert `EFN_QR_ORDER_STALE` (1 m), retensi sesi 400 h & **catatan baris QR dikosongkan setelah 90 hari** (harian, SQL kolom `note` saja — total tidak berubah). |

## RPC (json2 persis)

User teknis `efn_qr_pb` / `efn_qr_bcf` (grup `efn_pos_bff_order.group_qr_bff` + Internal User saja, tepat satu
company; grup lain → ditolak). `api_key_duration` grup = **35 hari** (rotasi 30 hari). Galat bisnis =
`UserError("efn_qr:<kode>")` → HTTP 422; akses = `AccessError` seragam.

| # | HTTP | Body (kwargs) | Hasil |
|---|---|---|---|
| 13 | `POST /json/2/efn.pos.qr.order/efn_qr_menu` | `{"config_id": 7, "since": null\|"<ISO Z>", "cursor": null\|"<id>", "limit": 200}` | `{config:{config_id, company_code, outlet_name, qr_enabled, confirm_mode}, items:[{product_id, name, price_excl, tax_labels, available, attributes:[{attribute_id, name, display_type, required, values:[{id, name, price_extra_excl}]}]}], deleted:[id], next_cursor, server_time}` |
| 14 | `POST /json/2/efn.pos.qr.order/efn_qr_submit` | `{"config_id": 7, "table_session_ref": "<uuid>", "submission": {"idempotency_key": "fnb:submit:<uuid>", "submission_ref": "<uuid>", "lines": [{"product_id": 1, "qty": 1, "attribute_value_ids": [3], "note": "..."}]}, "order_uuid": "<opsional>"}` | `{submission_ref, state: "pending_confirmation"\|"accepted", order_uuid, lines:[{product_id, name, qty, attribute_value_ids, attributes_text}], amount_indicative_excl}` |
| 15 | `POST /json/2/efn.pos.qr.order/efn_qr_status` | `{"config_id": 7, "submission_refs": ["<uuid>", …≤100]}` | `[{submission_ref, state, seq, order_uuid}]` (`absent`: `seq 0`, `order_uuid null`). **Probe rotasi key:** `{"config_id": 0, "submission_refs": []}` → `[]` (200). |

**K1 (r15):** `order_uuid` = uuid `pos.order` yang dibuat untuk kiriman (UUID v4 server), sama di hasil submit
(termasuk replay; hasil lama tanpa kunci ini diisi dari record), di `efn_qr_status`, dan di event `submission_status`
(`submission: {submission_ref, status, order_uuid}`). Nilainya **stabil**: tetap uuid order asal walau order itu
kemudian digabung pelayan ke order lain atau dihapus (ditolak); `null` hanya bila kiriman tidak dikenal/tanpa order.
Earn tetap mengikuti `close.orders[]` (order yang benar-benar dibayar).

Kode galat: `session_closed`, `pos_session_closed`, `item_unavailable`, `invalid_input`, `idempotency_conflict`,
`qr_disabled`, `limit_exceeded`. r15: `item_unavailable` membawa id produk bila diketahui —
`efn_qr:item_unavailable:<id>,<id>` (urut naik, unik, ≤ 30 id desimal, tanpa spasi); prefiks `efn_qr:item_unavailable`
tetap stabil. Event Odoo → BFF: body ≤ 16 KB, `close.orders` ≤ 100 entri (lebih → outbox `failed`
`too_many_orders` + alert). Catatan ditolak (`invalid_input`) menurut **Filter catatan** di bawah; disimpan
sebagai JSON catatan baris POS 19 (`[{"text","colorIndex"}]`, tampil di KDS/printer dapur). `name` di item menu
ditambahkan (A.6 "bila perlu") — BFF wajib menerima kunci ini.

### Filter catatan (SEC-168, r15) — aturan yang WAJIB identik di BFF (`sanitizeNote`)

Odoo (`qr_common.normalize_note` / `note_has_pii` / `clean_note`) **sama persis** dengan BFF `sanitizeNote`
(`services/api-gateway/src/fnb/fnb.model.ts`, BFF r15). Urutan wajib:

0. `None` → tanpa catatan. Bukan string atau panjang mentah > 1000 → tolak.
1. Normalisasi **NFKC** (angka fullwidth U+FF10..U+FF19 → 0..9, U+FF20 → `@`, spasi lebar → spasi).
2. **Hapus** (bukan ganti spasi) karakter format: U+200B–U+200F, U+202A–U+202E, U+2060–U+2064, U+2066–U+2069,
   U+FEFF (`NOTE_FORMAT_RANGES`, daftar yang sama dengan BFF; bukan seluruh kategori Cf).
3. Kontrol U+0000–U+001F, U+007F–U+009F, U+2028, U+2029 → spasi.
4. Runtun whitespace → satu spasi U+0020; trim. 5. Kosong → tanpa catatan.
6. > 140 **code point** (Python `len`) → tolak.
7. Tolak bila memuat `@`, ATAU (a) jumlah digit desimal Unicode (Nd, Python `str.isdecimal`) di SELURUH catatan
   ≥ 8, ATAU (b) cocok deret ASCII lama `[0-9][0-9\s.-]{6,}[0-9]`. Tanpa "jendela": pemisah apa pun
   (`0812-3456 / 789`, huruf di antara digit) bukan bypass selama total digit ≥ 8.

Vektor uji bersama (`tests/test_qr_submit.py::test_10b_note_pii_filter_vectors`; dibangun dengan `chr(0x...)`, notasi
`<U+XXXX>` = satu karakter dengan kode titik itu). Ditolak: angka fullwidth `<U+FF10><U+FF18><U+FF11>...<U+FF19>`,
`0812<U+200B>3456<U+200C>789`, `0812<U+2060>3456<U+FEFF>7890`, `<U+202E>0987654321<U+202C>`,
`telp <U+2066>0812<U+2069> 3456 7890`, `0812<U+00AD>3456<U+00AD>789` (U+00AD tidak dihapus; ditolak karena 11
digit), `0812a3456b789`, 1001 karakter mentah, `0812-3456 / 789`, `meja 1 kursi 2 nomor 3456 kode 78`,
`budi<U+FF20>example.com`, `0812<U+0085>3456789`. Diterima: `<U+FF54><U+FF41><U+FF4E><U+FF50><U+FF41> es` → `tanpa es`,
`tanpa<U+200B> gula` → `tanpa gula`, `tanpa<U+2028>es` → `tanpa es`, `2 es teh, 1 kopi, meja 12, 345` (7 digit),
140 x `x` + 50 x U+200B → 140 x `x`.
Upaya terbaik (DPIA §13): huruf `O`/`l` sebagai angka atau angka ditulis dengan kata tetap lolos.

**RPC POS** (bukan gateway, RPC_ALLOWLIST): `pos.config.efn_qr_session_open([[config_id], table_id, order_uuid,
reissue])` → `{qr_png_b64, expires_at, table_label, print_no}` atau `{error_code: qr_disabled|order_invalid|
session_exists|bff_unavailable, message}`; `pos.config.efn_qr_session_close([[config_id], table_id])`;
`pos.order.efn_qr_claim_send([[order_id]])` → bool.

**Odoo → BFF:** `POST /v1/pos/fnb/table-sessions` body `{idempotency_key: "fnb_open:<config>:<table>:<n>",
company_code, config_id, table_id, table_label, reissue}` (L-76: tanpa `order_uuid`); `POST /v1/pos/fnb/events`
(§14.A.4; `submission` hanya untuk `submission_status`; L-80 + K2 r15: bila `reason=paid`, `close.orders =
[{order_uuid, eligible_amount, company_code, pos_member_attached}]`).

**SEC-167 (r15) — nomor cetak `n`:** `MAX(print_no) + 1` per (config, meja), dibaca setelah `touch_row` meja
(dua pelayan serentak terserialisasi). Monoton karena retensi hanya menghapus sesi tertua (nomor terkecil); bila semua
sesi meja terhapus (> 400 hari tak dipakai) nomor mulai lagi dari 1, aman karena BFF menyimpan kunci `fnb_open` 180
hari. Kunci stabil saat transaksi diulang (MAX dibaca dari data ter-commit) → BFF me-replay sesi yang sama. Batas
`n` ≤ 99 999 999 (BFF r15). Respons BFF yang menunjuk `bff_session_id` yang sudah tercatat di Odoo (sesi lama,
tutup, atau meja lain) ditolak (`bff_unavailable` + log ERROR) **sebelum** sesi open saat ini ditutup.

### Poin QR: `close.orders[]` dan refund (K2/K3 r15, SEC-164/165)

- `eligible_amount` = dasar poin **neto**: (Σ dasar baris order − Σ dasar baris semua order refund yang sudah dibayar
  dan menunjuk order itu) × faktor porsi non-Poin-EFN order asal, dibulatkan ke bawah. Dasar = rumus earn POS
  (`_efn_loyalty_eligible_raw`: tanpa pajak, tanpa SC, tanpa kategori dikecualikan) bila `efn_pos_loyalty_ledger`
  terpasang, selain itu `price_subtotal`. Order neto ≤ 0 **tidak dimasukkan**. Order refund sendiri tidak pernah
  dilekatkan ke sesi dan tidak masuk daftar.
- `pos_member_attached` = `true` bila (a) ledger mencatat earn/redeem_commit yang tidak `skipped` untuk order itu,
  atau (b) partner order punya `member_ref` sah (HP Indonesia, `partner_member_ref`) — kondisi yang membuat ledger
  mengantre earn POS ber-member. (b) diperlukan karena event close bisa dibangun di transaksi pembayaran yang sama
  sebelum hook ledger membuat outbox earn. Konservatif: partner member terlampir = attached walau earn POS di config
  mati. BFF tidak meng-earn klaim QR untuk order `true` (SEC-165).
- **Urutan (tanpa jendela hilang):**
  1. Sesi `open`/`expired`: refund **tidak** mengirim reverse (`_efn_qr_refund_mode` → `deferred`) dan mengunci baris
     sesi.
  2. Sesi ditutup (`paid`): outbox `session_closed` dibuat (snapshot awal, hanya untuk tampilan). Refund setelah close
     tetapi sebelum pembekuan → tetap `deferred` (masuk neto saat pembekuan).
  3. **Pembekuan (L-83):** pass outbox pertama mengunci baris sesi (savepoint; bentrok → ditunda `session_busy`),
     menghitung `close.orders` neto, menyimpan `orders_snapshot` + `netted_refund_ids`, set `snapshot_frozen`, lalu
     berhenti; cron meng-commit, dan POST terjadi di pass berikutnya. Jadi body yang pernah keluar selalu body beku yang
     tersimpan. **Semua retry mengirim body yang sama persis** (kunci idempotensi sama; BFF menjawab replay 200).
     Kunci sesi TIDAK dipegang selama POST.
  4. Refund setelah pembekuan (sebelum 2xx) → `awaiting`: **tidak** dinetokan; refund ditandai
     `efn_qr_refund_awaiting` (sambil mengunci baris sesi).
  5. Saat 2xx: outbox mengunci baris sesi (bentrok → event tetap pending, retry → replay 200), lalu dari snapshot beku:
     `netted_refund_ids` → `efn_qr_refund_netted`; order `pos_member_attached=false` → `efn_qr_points_eligible` +
     `efn_qr_eligible_amount` = `eligible_amount` snapshot; setiap refund `awaiting` → flag dilepas dan ledger
     meng-enqueue reverse `earn_channel:"qr"` dengan rasio terhadap `eligible_amount` snapshot.
  6. Refund setelah 2xx: reverse `earn_channel:"qr"` (ledger 19.0.2.4.1), rasio terhadap `efn_qr_eligible_amount`
     dikurangi rasio reverse QR sebelumnya.
  7. Event close gagal permanen (4xx final / umur maks): tidak ada flag poin; refund `awaiting` **tetap bertanda**
     untuk review + alert `EFN_QR_REFUND_UNRESOLVED` (logger `odoo.addons.efn_pos_bff_order.alert`, id saja).
  Penutupan, pembekuan, pemrosesan 2xx, dan refund `deferred`/`awaiting` mengunci baris sesi yang sama
  (`touch_row`); transaksi yang kalah gagal serialisasi dan diulang dengan snapshot baru, sehingga setiap refund
  tercermin tepat sekali: di neto (snapshot) ATAU sebagai reverse.

### Pelekatan order ke sesi (SEC-166, r15)

Order tertaut ke sesi hanya lewat: order pelayan saat QR dibuka/dicetak ulang (anchor + carried), order kiriman QR
saat dibuat, dan — saat dibayar — order yang belum tertaut **hanya bila** sesi `open` (bukan `expired`),
`date_order >= opened_at`, bukan refund, dan order membawa baris kiriman QR sesi itu (hasil gabung order meja). Order
tamu berikutnya di meja yang sama tidak pernah diserap walau kiriman QR lama masih pending; sesi `expired` tetap
ditutup (event close) saat meja kosong tanpa menyerap order baru.

### Cetak ulang QR (SEC-174, r15 re-review)

- Yang dibawa (*carried*) ke sesi baru **hanya order `draft`** sesi lama (plus order jangkar cetak ulang). Order yang
  sudah dibayar (`paid`/`done`/`invoiced`) dan order batal **tetap** di sesi lama.
- Sesi lama **punya order dibayar** (bukan refund) → ditutup `paid` lewat `_efn_close("paid")`: outbox
  `session_closed reason=paid` biasa, snapshot dibekukan di putaran outbox pertama (L-83), `close.orders[]` = order
  dibayar milik sesi lama saja. BFF sudah mencabut sesi lama (`reissued`) saat `POST /table-sessions`, tetapi tetap
  memproses `session_closed` untuk sesi tertutup (`applyOrder` + `markCloseProcessed`, status tidak diubah), sehingga
  earn jatuh ke pengklaim **lama**. Log `EFN_QR_SESSION_REISSUE_PAID session=<id>` (tanpa PII). Refund atas order itu
  mengikuti urutan K3 biasa (sesi tertutup + close pending → `deferred`/`awaiting`; setelah 2xx → reverse QR).
- Sesi lama **tanpa order dibayar** → perilaku lama: `closed reissued`, tanpa event (BFF mencabut sendiri).
- Penutupan dilakukan **setelah** BFF menjawab 201 (BFF gagal → sesi lama tetap open, tidak ada event).
- Akibat operasional (L-83 b): mencetak ulang QR dari order pelayan kedua hanya memindahkan draft; tagihan yang sudah
  dibayar tetap menjadi poin pengklaim sesi lama.

## Konfigurasi & secret

- `EFN_BFF_URL` (https), `EFN_CA_FILE`, `EFN_BFF_API_KEY_FILE` (efn_core); **`EFN_FNB_EVENT_HMAC_FILE`** (default
  `/run/secrets/odoo_fnb_event_hmac`, ≥ 32 karakter). Tanpa secret → event tidak dikirim (tidak ada fallback tanpa
  tanda tangan). Parameter `efn_pos_bff_order.outbox_max_hours` (48).
- Token QR: hanya ada di respons BFF dalam memori request; QR dirender dengan reportlab langsung (BUKAN
  `ir.actions.report.barcode`, yang menyimpan nilai di cache LRU). Tidak disimpan/di-log.

## Uji manual

1. Settings POS restoran PB: aktifkan "QR ponsel", pilih preset dine-in. Buka sesi POS, pilih meja, tambah item
   (order tersimpan), menu tombol lain → **QR Meja** → QR tampil → Cetak. Offline → tombol nonaktif.
2. Panggil `efn_qr_submit` lewat gateway (key `efn_qr_pb`) → toast "Pesanan QR baru" di POS; order baru di meja.
3. Pelayan buka order → *Order* → tiket KDS; bump di KDS → `efn_qr_status` berubah accepted → preparing → ready.
4. Bayar order QR + selesaikan order lain di meja → sesi `closed paid` → event `session_closed` di menu
   *Event QR ke BFF*.

## Test

`tests/test_qr_submit.py`, `tests/test_qr_session.py` (`TestPoSCommon`, tag `efn`; BFF di-mock `requests.post`;
termasuk refund → reverse QR bila `efn_pos_loyalty_ledger` terpasang). r15: `test_10b_note_pii_filter_vectors`,
`test_refund_before_close_is_netted_without_reverse`, `test_refund_after_close_before_first_attempt_is_netted`,
`test_frozen_snapshot_reused_and_awaiting_refund_reversed_on_2xx`, `test_awaiting_refund_kept_and_alerted_when_close_fails`,
`test_full_refund_before_close_excluded`, `test_refund_after_close_ratio_against_net`,
`test_pos_member_attached_not_points_eligible`, `test_sec166_pending_submission_next_guest_not_absorbed`,
`test_sec167_print_no_monotonic_after_retention`, `test_sec167_bff_pointing_to_existing_session_rejected`; K1 di
test 01/08/11. r15 re-review: `test_sec174_reissue_keeps_paid_orders_on_old_session`,
`test_sec174_reissue_without_paid_orders_sends_no_close` (tanpa ketergantungan `efn_pos_loyalty_ledger`). Tour POS (§13.1b no. 17) **belum** ditulis.

## Batasan / belum

- **[B] belum diverifikasi runtime**: render gambar QR di printer struk Epson (P-18); pemilihan order di layar denah
  bila > 1 order per meja; `loadServerOrders` + `sendOrderInPreparationUpdateLastChange` dari notifikasi (mode
  auto); penggabungan order meja membawa `efn_qr_submission_ref` (`mergeTableOrders` menyalin `raw` baris).
- Poin QR default mati (`FNB_QR_POINTS_ENABLED=false`). r15 menutup SEC-164 (neto + urutan K3), SEC-165
  (`pos_member_attached`), SEC-166 (pelekatan ketat) di sisi Odoo; aktivasi tetap menunggu verifikasi BFF r15 dan
  review security.
- Residu retry close (BFF memproses tetapi Odoo tidak menerima 2xx) ditutup oleh pembekuan snapshot (L-83). Pembekuan
  menambah satu putaran cron (± 1 menit) sebelum POST pertama event close.
- SEC-166 (disetujui lead, L-83): order pelayan KEDUA yang dibuat terpisah di meja (bukan gabungan) setelah QR dibuka
  tidak ikut poin sesi; pelayan dapat mencetak ulang QR dari order itu bila satu rombongan (hanya order **draft**
  lama yang ikut pindah; yang sudah dibayar dilaporkan di close sesi lama, SEC-174).
- SEC-174 sisa (tidak ditangani di perbaikan ini): order jangkar/carried yang sudah tertaut tetap dihitung walau dibayar
  jauh setelah sesi `expired` (rekomendasi r15 re-review no. 3, "bekukan anggota sesi saat expired") — terbuka.
  `pos_member_attached` konservatif (partner member terlampir = attached walau earn POS mati) juga disetujui (L-83).
- Bentrok kunci di outbox (savepoint) dapat menghasilkan baris log ERROR "bad query" dari kursor Odoo pada kejadian
  serentak (jarang); event ditunda, bukan hilang.
- Tour POS (buka QR → order masuk → kirim ke dapur → bayar) belum ditulis.
- Menu delta: produk yang dihapus permanen / keluar menu karena perubahan pengaturan tidak muncul di `deleted`;
  BFF perlu sinkron penuh (`since` kosong) berkala.
- Combo tidak ditawarkan ke QR (D-22-9).
- Sesi `expired` yang order tertautnya dibayar setelahnya tetap mengirim `session_closed paid` (BFF memutuskan earn);
  order baru tidak diserap (SEC-166).

## Referensi desain

Pola internal `efn_online_order` (pemanggil teknis, idempotensi, outbox HMAC) & `efn_pos_kds`. Tidak ada kode
OCA/Cybrosys yang dirujuk/disalin (pola vendor self-order memakai route publik Odoo — tidak sesuai ADR-023).
