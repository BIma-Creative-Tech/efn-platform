# efn_pos_kds — Kitchen Display System (KDS) untuk POS restoran CE 19

**Tujuan:** layar dapur per stasiun (Dapur Panas, Bar, Expo/Pass) untuk POS restoran Odoo 19
Community: tiket per stasiun, bump/recall, waktu tunggu & SLA, sinkron realtime lewat bus dengan
kanal bertoken acak, serta cadangan printer dapur standar saat KDS mati / POS offline.
Fit-gap F-52..F-54, spesifikasi E-26 (`docs/08_ODOO_FIT_GAP.md` §7.3).

| | |
|---|---|
| Versi | 19.0.1.0.0, LGPL-3 |
| Depends | `pos_restaurant`, `bus`, `efn_core` (tanpa `pos_self_order`, `pos_mrp`, tanpa modul pihak ketiga) |
| ADR | ADR-013 opsi A (KDS custom; *Proposed*), ADR-023 (perangkat = peer WireGuard; *Proposed*), ADR-031 (tanpa dependency vendor) |
| Risiko | R-T03 (KDS Enterprise-only), R-B01, UU PDP 27/2022 (minimisasi data pelanggan di dapur) |
| Keputusan klien | Q-19 (stasiun, kategori, target menit), Q-24/Q-56 (layar, printer, kebijakan cetak cadangan) — **default di modul = PLACEHOLDER** |

## Cara kerja

- **Stasiun** (`efn.kds.station`, Point of Sale → Configuration → Stasiun KDS): company, Point of
  Sale (`config_ids`), kategori POS (routing; sub-kategori ikut induk; **kosong = semua**, maks.
  satu per POS = expo/pass), mode `kds` / `kds_printer` (default pilot) / `printer`, printer
  cadangan (`pos.printer` standar yang terpasang di POS), batas peringatan/terlambat (menit),
  user perangkat, heartbeat (`last_seen`, online bila < 60 dtk).
- **Tiket** (`efn.kds.ticket` + baris) dibuat **server** di satu tempat (`pos.order._process_order`,
  dilalui `sync_from_ui` POS & kiosk) saat kasir menekan *Order* — pemicu = isi
  `last_order_preparation_change` standar berubah (tanpa `metadata`) — atau saat course di-*fire*.
  `delta = qty baris DB − qty yang sudah ditiket` per (baris, stasiun); baris dihapus → tiket qty
  negatif; order dibatalkan → tiket `cancelled`. Induk combo tidak ditiket, anak combo dirutekan
  per kategori (menu lintas stasiun = `type='combo'`, bukan Kit BoM). Course belum di-fire tidak
  dikirim. Idempoten: sinkron ulang tanpa perubahan tidak membuat tiket; kunci unik
  (order, stasiun, `sequence_no`). Kegagalan KDS **tidak** menggagalkan sinkron order, tetapi
  memicu alert + cetak cadangan (SEC-76, lihat Gelombang 15).
- **Status:** `new → in_progress → ready → served` (boleh melompat maju), recall
  `ready → in_progress` dan `served → ready` (≤ `efn.kds.recall_minutes`, default 10).
  Cap waktu per status; `wait_seconds` = terkirim → siap (KPI F-54 lewat ETL, bukan laporan Odoo).
- **Realtime:** `pos.bus.mixin` → kanal `"<uuid4>-EFN_KDS"`; payload hanya `type`, `ticket_ids`,
  `ts` — isi tiket diambil lewat RPC `efn_kds_load`. Badge "Siap" di denah meja POS lewat
  `EFN_KDS_READY` pada kanal partner kasir (SEC-77; id order/meja/tiket/config + status saja).
  Heartbeat 30 dtk juga menjadi polling cadangan bila websocket terputus.
- **Cadangan printer (F-53):** routing & format printer dapur standar tidak diubah. POS patch
  `printChanges`: printer cadangan stasiun mode `kds` dilewati hanya bila POS online **dan** layar
  stasiun hidup (dicek `pos.config.efn_kds_station_status` tepat sebelum mencetak; gagal cek =
  cetak). `kds_printer`/`printer` = standar. POS offline: tombol *Order* tetap mencetak (standar CE
  menolak saat offline) dan order ditandai `efn_kds_printed_offline` → tiket stasiun `kds` dibuat
  saat sinkron langsung `in_progress` berlabel **"Sudah dicetak"** (dapur tidak memasak ganda).
  Saat offline, fitur server lain (OTP/poin) tetap nonaktif (ADR-017).
- **Layar KDS:** `/efn_kds/<id>` (`auth='user'`), bundle `efn_pos_kds.assets_kds` (tanpa seluruh POS).
  Kolom Baru/Dimasak/Siap, umur tiket (jam server), warna peringatan/terlambat, baris + atribut +
  catatan, ketuk baris = selesai, tombol bump besar, filter course, recall, suara tiket baru, mode
  gelap. Tanpa harga, pelanggan, pembayaran. Rotasi token → layar memuat ulang otomatis.

## Keamanan & data pribadi

- **Kanal bus tidak bisa ditebak** (temuan G14 #31): token uuid4 per stasiun, field `access_token`
  hanya terbaca manager POS, keluar hanya lewat halaman layar setelah cek akses (header
  `Cache-Control: no-store`), tidak dimuat ke data POS. Pola Cybrosys `pos_order_created_{config_id}`
  sengaja tidak ditiru. Rotasi: tombol *Rotasi token kanal* (manager) + otomatis (SEC-77).
- **User perangkat:** satu user internal per layar, grup **Perangkat dapur (layar KDS)** saja
  (allowlist SEC-74: grup lain apa pun ditolak, dari form User maupun Grup). Record rule: hanya stasiun & tiket
  miliknya; **tanpa akses kontak** (rule `res.partner` = dirinya + partner company); field
  `order_id`/`config_id`/`session_id` tiket ber-`groups` POS user. Menu backend tidak terlihat.
  **Wajib** 2FA (`auth_totp`, kebijakan *employee_required* di Settings → Permissions) + perangkat
  dipercaya, perangkat = peer WireGuard (ADR-023), browser mode kiosk.
- **Tiket tanpa data pelanggan:** tidak ada partner, nama/HP/alamat, `floating_order_name`,
  `general_customer_note`, `customer_note`, harga. `internal_note` order & `note` baris (catatan
  dapur kasir) disalin — SOP kasir: jangan menulis data pelanggan di catatan dapur.
- **RPC (SEC-58):** `efn_kds_load`, `efn_kds_heartbeat`, `action_open_screen`,
  `action_rotate_token`, `efn_kds_bump`, `efn_kds_line_done`, `pos.config.efn_kds_station_status`
  — semuanya cek grup + record rule + company, terdaftar di `efn_core` `RPC_ALLOWLIST`.
- **Retensi:** tiket > `efn.kds.retention_days` (default 90, PLACEHOLDER) dihapus autovacuum.
- Multi-company: `company_id` wajib + record rule `company_id in company_ids` di tiga model.


## Gelombang 15 (review r8: SEC-74, SEC-76, SEC-77) — versi 19.0.1.1.0

- **SEC-74 allowlist grup:** grup efektif user perangkat harus ⊆ implikasi `base.group_user` +
  `group_kds_device` (grup lain apa pun ditolak, termasuk yang di-imply). Ditegakkan di
  `res.users` (constraint `group_ids`) **dan** `res.groups` (create/write `user_ids`/`implied_ids`/
  `implied_by_ids` → cek ulang semua user perangkat). Membuat user perangkat dari UI: hapus grup
  bawaan "Default Access Rights" selain Internal User.
- **SEC-74 sandbox model:** `ir.rule._compute_domain` (models/ir_rule.py) — untuk user perangkat
  (non-sudo) hanya model allowlist (`efn.kds.*`, `res.users` = diri, `res.partner` = diri + partner
  company, `res.company/lang/currency/country*`, `res.groups*`, `res.users.settings`,
  `decimal.precision`, awalan `ir.`/`bus.`/`base.`/`web_tour.`) yang terbaca; model lain = domain
  FALSE (produk & `standard_price`, `hr.employee(.public)`, `resource.resource`, Discuss, loyalty,
  dll.), termasuk model dari modul yang dipasang belakangan — tanpa migrasi/noupdate. Diuji runtime
  terhadap SEMUA model terpasang (`test_kds_security.py::test_device_runtime_surface_no_customer_name`).
  Rule perangkat XML kini `noupdate=0` (DB lama: `migrations/19.0.1.1.0/pre-migrate.py`).
- **SEC-76 kegagalan tiket:** tidak lagi ditelan diam-diam. Log satu baris
  `EFN_KDS_TICKET_FAILED {json}` di logger `odoo.addons.efn_pos_kds.alert` (id order/config/sesi/
  company, stasiun+mode, `kds_only`, nama tipe exception — tanpa PII/pesan exception; traceback hanya
  DEBUG) + `pos.order.efn_kds_ticket_failed=True` (milik server; nilai klien dibuang). POS membaca
  tanda itu di respons sinkron → mencetak perubahan yang ditahan untuk stasiun mode `kds` ke printer
  cadangan + notifikasi kasir *sticky*. `SerializationFailure`/lock/deadlock naik (Odoo me-retry
  request); bentrok unik nomor tiket → `ConcurrencyError` (retry). **Rule Loki** (devsecops):
  Alert sudah dipasang di Loki (stream `efn_kds`, rule `EfnKdsTicketFailed` / `EfnKdsTicketFailedSpike` / `EfnKdsAlertFormatUnrecognized` di `infra/observability/loki-rules/efn-kds.yml`); runbook `docs/runbooks/kds-ticket-failed.md`.
- **SEC-77 token & kanal:** token kanal stasiun dirotasi otomatis saat `device_user_ids`/`active`/
  `company_id` stasiun berubah, saat user perangkatnya dinonaktifkan/dihapus/diubah grup, login,
  password, atau company, saat dikeluarkan dari grup perangkat lewat form Grup, dan **berkala**
  (cron harian, token > `efn.kds.token_max_age_days` = 7 hari PLACEHOLDER, min 1). `EFN_KDS_READY`
  dan `EFN_KDS_STATUS` dikirim di **kanal partner kasir** (penerima: pembuka sesi + user yang memuat
  POS sesi terbuka, dicatat di override `pos.session.load_data`), bukan kanal token `pos.config`
  yang ikut ke QR self-order/kiosk. `device_user_ids`, `last_seen`, `token_rotated_at` ber-`groups`
  manager POS (kasir tidak dapat membaca daftar akun tablet).

## Cara uji manual

1. Install modul; Point of Sale → Configuration → Stasiun KDS: buat "Dapur Panas" (kategori Dapur
   Panas) dan "Bar" (kategori Bar Minuman), pilih POS restoran outlet.
2. Buat user perangkat (Settings → Users): user internal, hanya grup *Perangkat dapur (layar KDS)*;
   aktifkan 2FA; tambahkan ke *User perangkat* stasiun.
3. Login perangkat di tablet → buka `/efn_kds/<id stasiun>` (tombol *Buka layar KDS* di form).
4. POS: meja → nasi goreng + es kopi → *Order* → tiket muncul di layar dapur & bar masing-masing.
   Bump Mulai → Siap (badge "Siap" + notifikasi di POS) → Disajikan; recall dari footer.
5. Ubah stasiun Bar ke mode `kds` + printer cadangan; matikan layar Bar > 60 dtk → *Order* dari POS
   mencetak ke printer Bar; tiket bar berlabel "Sudah dicetak".

## Test

- `tests/test_kds.py` (tag `efn`): routing kategori → stasiun (sub-kategori, stasiun semua, baris
  tanpa stasiun), delta qty (+2, +1, hapus −3), sinkron ulang, baris belum dikirim, course, combo
  lintas stasiun, batal order, cetak offline, mode printer, constraint, autovacuum, bump/recall,
  akses perangkat (stasiun lain, PT lain, tanpa grup, kasir), kontak/order tidak terbaca, payload &
  bus tanpa data pelanggan, token acak & rotasi, heartbeat/status, constraint grup perangkat.
- `tests/test_kds_tour.py`: controller (tag `efn`) dan tour (tag `efn_tour`): order POS → tiket di
  stasiun benar; `kds_printer` → printer dipanggil; `kds` layar mati → cetak cadangan & tiket
  "sudah dicetak"; `kds` layar hidup → printer dilewati; `kds` layar hidup + tiket gagal di server
  → cetak cadangan + notifikasi (SEC-76); layar KDS bump sampai `served`.
- `tests/test_kds_security.py` (tag `efn`, G15): allowlist grup dua sisi, sandbox model perangkat
  (runtime semua model), alert/tanda kegagalan tiket + retry konkurensi, rotasi token otomatis &
  berkala, kanal kasir, field akun tablet tertutup bagi kasir.

## Batasan / belum

- [B] Badge "Siap" di POS bergantung websocket bus (kanal partner kasir); tidak diuji di tour
  (payload & penerima diuji unit).
- [B] Akun perangkat sebagai user *share* (opsi lebih kuat SEC-74) butuh ADR kecil (2FA portal).
- [B] Kasir yang membuka POS tanpa memanggil `load_data` (mis. cache) dan bukan pembuka sesi tidak
  menerima badge "Siap" sampai memuat ulang POS.
- [B] Self-order kiosk: diasumsikan menulis `last_order_preparation_change` (frontend
  `self_order_service` memanggil `updateLastOrderChange`) — belum diuji end-to-end.
- [B] Printer Epson ePOS HTTPS/DNS sertifikat (Q-56) dan perangkat KDS nyata (Q-24).
- Atribut kustom (`custom_attribute_value_ids`, teks bebas klien) tidak ditampilkan di KDS.
- ETL `efn_kds_ticket` → ClickHouse (KPI waktu tunggu) belum dibuat (efn-data-ai-engineer).
- Status online di POS untuk mode `kds` dicek per pengiriman (RPC), bukan dari bus.
- Target menit, retensi, jendela recall = PLACEHOLDER sampai Q-19 dijawab.

## Referensi (ADR-031 — dipelajari, tidak disalin)

- Cybrosys `pos_kitchen_screen_odoo` 19.0.1.0.0 (CybroOdoo/CybroAddons@19.0, LGPL-3): konsep layar
  dapur per `pos.config` + kategori. **Tidak ditiru:** status di `pos.order`/`pos.order.line`
  (di sini tiket per stasiun), kanal bus `pos_order_created_{config_id}` yang mudah ditebak,
  `customer_id` di baris tiket, `sudo()` tanpa cek akses. Tidak ada kode yang disalin.
- Odoo CE 19 (`point_of_sale`, `pos_restaurant`, `bus`): titik integrasi `pos.bus.mixin`,
  `pos.load.mixin`, `last_order_preparation_change`, `printChanges`, pola customer display
  (`/pos_customer_display`, bundle aset sendiri).
- OCA/pos 19.0: tidak ada modul KDS.
