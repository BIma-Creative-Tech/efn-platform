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
  (order, stasiun, `sequence_no`). Kegagalan KDS dicatat dan **tidak** menggagalkan sinkron order.
- **Status:** `new → in_progress → ready → served` (boleh melompat maju), recall
  `ready → in_progress` dan `served → ready` (≤ `efn.kds.recall_minutes`, default 10).
  Cap waktu per status; `wait_seconds` = terkirim → siap (KPI F-54 lewat ETL, bukan laporan Odoo).
- **Realtime:** `pos.bus.mixin` → kanal `"<uuid4>-EFN_KDS"`; payload hanya `type`, `ticket_ids`,
  `ts` — isi tiket diambil lewat RPC `efn_kds_load`. Badge "Siap" di denah meja POS lewat
  `EFN_KDS_READY` pada kanal token `pos.config` standar (id order/meja/tiket + status saja).
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
  sengaja tidak ditiru. Rotasi: tombol *Rotasi token kanal* (manager).
- **User perangkat:** satu user internal per layar, grup **Perangkat dapur (layar KDS)** saja
  (constraint menolak kombinasi dengan user POS / admin). Record rule: hanya stasiun & tiket
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
  "sudah dicetak"; `kds` layar hidup → printer dilewati; layar KDS bump sampai `served`.

## Batasan / belum

- [B] Badge "Siap" di POS bergantung websocket bus; tidak diuji di tour (payload diuji unit).
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
