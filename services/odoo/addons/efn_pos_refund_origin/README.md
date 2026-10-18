# efn_pos_refund_origin (E-31, E-41) — refund POS ke metode bayar asal; retur tanpa struk dengan persetujuan

> **Ringkasan manajemen.** Uang refund di kasir hanya bisa dikembalikan lewat cara bayar yang dipakai pelanggan
> saat membeli (mis. dibayar kartu -> dikembalikan ke kartu), maksimal sebesar yang dibayar dengan cara itu. Retur
> tanpa struk hanya tunai **dan** (sejak 19.0.1.3.0) wajib disetujui Store Manager lain dengan PIN yang diperiksa
> server; saat POS offline retur tanpa struk tidak bisa dilakukan. Setiap persetujuan dan PIN salah tercatat.

## Review r13 (SEC-147, SEC-151, versi 19.0.1.4.0)
- **SEC-147: order campuran ditolak.** Order dengan baris ber-struk (`refunded_orderline_id`) **dan** barang qty < 0
  tanpa asal dulu dinilai "refund ber-struk", sehingga persetujuan E-41 dan aturan tunai-saja tidak berlaku untuk
  barang tanpa struk (dan barang itu masuk stok atas nama refund ber-struk). Dipilih **tolak** (bukan memecah nominal
  per porsi): paling sederhana, tidak ada alokasi uang keluar per baris yang bisa salah. Berlaku di semua mode
  (`manager_approval`/`cash`/`deny`), juga bila sudah ada persetujuan untuk uuid itu (tidak dikonsumsi). Kasir
  memisahkan: refund dari struk dulu, lalu retur tanpa struk sebagai order sendiri (persetujuan + tunai). Frontend
  memblokir lebih awal dengan pesan yang sama (`efnIsMixedReturn`). Log: `mixed_return`.
- **SEC-151: teks PIN tidak pernah disimpan di tabel transient.** Klik *Simpan PIN* didahului `web_save` (transaksi
  sendiri, di-commit), jadi mengosongkan PIN di `action_apply` tidak cukup (rollback saat gagal tidak menyentuh baris
  yang sudah di-commit). Kini `create`/`write` wizard membuang `pin`/`pin_confirm` dari vals dan langsung menulis
  `pin_hash` (pbkdf2, hanya bila format benar + tidak lemah + konfirmasi cocok) atau `pin_error`
  (`format`/`weak`/`mismatch`). Nilai `pin_hash`/`pin_error` dari klien selalu dihitung ulang (tidak bisa menyuntik
  hash PIN lemah). `action_apply` memindahkan hash ke user lalu mengosongkan `pin_hash` wizard. Form menampilkan field
  kosong setelah gagal → PIN diketik ulang. Migrasi `19.0.1.4.0` mengosongkan teks PIN di baris wizard lama.
- **Uji:** `test_mixed_return_rejected_in_every_mode`, `test_split_after_mixed_rejection_works`,
  `test_pin_never_stored_after_failed_apply` (konfirmasi beda, lemah, format, identity kedaluwarsa, bukan Store
  Manager — baris mentah dibaca SQL), `test_pin_never_stored_after_successful_apply_and_retry`,
  `test_pin_hash_injection_ignored`.
- **Batasan:** salinan teks PIN dari versi ≤ 19.0.1.3.0 tetap ada di WAL/backup sampai retensi backup berputar. Bila
  identity check kedaluwarsa setelah PIN sah disimpan form, **hash** kandidat (bukan teks) tetap di baris transient
  sampai vacuum (±1 jam), hanya terbaca pembuatnya dan admin. Pemblokiran order campuran di frontend belum diuji tour.

## E-41 — persetujuan Store Manager untuk retur tanpa struk (versi 19.0.1.3.0, G20)
- **Dasar:** keputusan pemilik 18-15, fit-gap `docs/08 §11.6` (E-41), kontrak `docs/04 §12.4`, keputusan lead
  **L-51** (PIN diverifikasi server, bukan `pos_hr` di browser; offline diblokir). Meralat Q-F18-5/L-23 ("cukup tunai").
  ADR-031 (tanpa modul pihak ketiga). ADR-017 opsi (a) (fitur yang butuh server nonaktif saat offline).
- **Risiko dimitigasi:** R-B06 (fraud kasir: retur fiktif tanpa struk dicairkan tunai), R-O05 (retur/refund),
  SEC-63 (hash PIN `pos_hr` dimuat ke browser -> bisa di-brute-force offline; di sini hash tidak pernah ke klien).
- **Mode** `efn.pos.refund.unlinked_return_mode` = `manager_approval` (**default baru**; nilai tidak dikenal juga
  diperlakukan `manager_approval`) | `cash` (tunai tanpa persetujuan, perilaku lama) | `deny`. Semua mode tetap
  **hanya tunai**. Migrasi `19.0.1.3.0`: DB yang masih `cash` (default lama) -> `manager_approval`.
- **PIN** (`res.users.efn_return_pin_hash`, `groups=base.group_system`): pbkdf2-sha512 passlib (putaran
  `efn.pos.return_pin_kdf_rounds`, default 600.000 = minimum hash password Odoo 19), 6–8 digit, deret/angka sama
  ditolak. Diatur lewat **Preferensi -> Atur PIN** (Store Manager sendiri) atau **Settings -> Users -> Atur PIN
  persetujuan retur** (hanya `base.group_system`). Keduanya `@check_identity` Odoo (konfirmasi kata sandi; wizard juga
  mensyaratkan identity check sesi < 10 menit sehingga tidak bisa dipanggil langsung lewat RPC). Target wajib grup
  Manajer POS. Hash tidak terbaca POS manager/kasir dan tidak ada di data POS.
- **Approver sah** (sementara sampai W-31 jabatan `hr.job`): user internal aktif, grup
  `point_of_sale.group_pos_manager`, company config ∈ company user, PIN diatur, **≠ user kasir yang login**.
- **RPC** (semua di `RPC_ALLOWLIST`, diawali `_efn_refund_check_caller`):
  - `pos.config.efn_return_approval_approvers()` -> `{ok, approvers: [{id, name}]}` (nama staf internal plaintext,
    18-40; tanpa hash/PII pelanggan) | `{ok: false, error_code: "mode_disabled"}`.
  - `pos.config.efn_return_approval_request(order_ref, approver_id, pin, amount, reason)` -> `{ok: true,
    expires_in: 300, max_amount}` | `{ok: false, error_code, message}` dengan `error_code` ∈ `mode_disabled`,
    `invalid_input`, `approver_invalid`, `invalid_pin`, `locked`. Kunci baris approver (`touch_row res_users`,
    percobaan paralel diserialkan), **kunci 15 menit setelah 5 PIN salah per approver** (dihitung sejak persetujuan
    sukses terakhir approver itu), PIN tidak disimpan/di-log. Sukses -> persetujuan baru untuk uuid yang sama
    menggantikan yang lama (`superseded`).
  - `res.users.efn_action_return_pin_wizard()` dan `efn.pos.return.pin.wizard.action_apply()` (atur PIN).
- **Token sekali pakai = record `efn.pos.return.approval` server-side** (pola `efn.pos.otp.challenge`: tidak ada
  token di browser yang bisa dicuri/diputar ulang). Terikat config + sesi + `pos.order.uuid` + kasir (user yang
  sync = user yang meminta) + company + nominal maks; TTL 5 menit. Saat sync (`_process_saved_order`, final) retur
  tanpa struk mode `manager_approval` -> persetujuan harus ada & sah (uang keluar tunai ≤ `amount_max`, belum
  kedaluwarsa, belum dipakai order lain) -> `consumed` + `consumed_order_id` + `amount_used`; selain itu `UserError`
  (sync ditolak). Tukar barang tanpa struk tanpa uang keluar juga butuh persetujuan (nominal 0). Sync ulang order yang
  sama idempoten.
- **Audit:** `efn.pos.return.approval` append-only (create/write/unlink hanya kode server ber-sudo; POS manager & admin
  baca saja; kasir tanpa ACL), `company_id` + `_check_company_auto` + record rule multi-company. Menu *Point of Sale ->
  Persetujuan Retur Tanpa Struk* (Manajer POS; filter PIN salah/terkunci, group by Store Manager/kasir/POS). Retensi
  `efn.pos.return_approval_retention_days` (default 730, minimum 365) via autovacuum. Isi: id user staf, nominal, alasan
  (UI meminta tanpa data pelanggan) — tanpa PIN, tanpa data pelanggan.
- **Frontend (OWL):** `PaymentScreen.validateOrder` (patch) -> bila config `efn_unlinked_return_mode` =
  `manager_approval` dan order retur tanpa struk: **offline -> diblokir dengan pesan**; online -> dialog
  `EfnReturnApprovalDialog` (pilih Store Manager, PIN diketik Store Manager, alasan) -> RPC -> validasi lanjut.
  PIN dihapus dari state dialog segera setelah dikirim. Persetujuan disimpan sebagai properti klien order (UI saja,
  server tetap penentu).

### Cara uji manual (E-41)
1. Login sebagai Store Manager (grup Manajer POS) -> Preferensi -> *Atur PIN* -> konfirmasi kata sandi -> PIN 6 digit.
2. Login POS sebagai kasir (bukan Store Manager) -> produk -> `+/-` (qty -1) -> Bayar -> Cash -> Validate -> dialog.
3. PIN salah -> "PIN salah"; 5x salah -> "dikunci 15 menit" (Store Manager lain tetap bisa).
4. PIN benar + alasan -> struk; *Point of Sale -> Persetujuan Retur Tanpa Struk* menampilkan `Dipakai` + order.
5. Matikan jaringan (devtools offline) -> ulangi -> pesan "POS sedang offline", tidak bisa validasi.
6. Ubah nominal setelah disetujui (lebih besar) -> sync ditolak; minta persetujuan ulang.

### Referensi (ADR-031)
Cybrosys `pos_refund_password@19.0` (AGPL-3): satu kata sandi global di tombol refund, diperiksa di browser — hanya
ide "gerbang refund", **ditolak** sebagai desain (verifikasi harus di server, per approver, berlockout). OCA/pos 19.0
tidak punya modul setara. Tidak ada kode yang disalin.

### Batasan / belum (E-41)
- Kasir = **user Odoo yang login** di POS. Bila terminal login sebagai user Store Manager (karyawan berganti lewat
  `pos_hr`), Store Manager itu tidak bisa menyetujui retur di terminalnya sendiri (by design: approver ≠ kasir);
  `employee_id` `pos_hr` diisi klien sehingga tidak dipakai. SOP: terminal login sebagai user kasir. W-31 (jabatan
  `hr.job` Store Manager) akan mengganti cek grup `group_pos_manager`.
- Kunci per approver bisa disalahgunakan kasir untuk mengunci Store Manager 15 menit (DoS); semua percobaan tercatat
  dengan id kasir. Store Manager lain tetap bisa menyetujui.
- PIN 6–8 digit (ruang kecil): hash pbkdf2 600k putaran hanya di DB (`base.group_system`); dump DB tetap memungkinkan
  brute force luring dalam hitungan jam — sama dengan kelas risiko kata sandi pendek; mitigasi = enkripsi backup &
  least privilege DB (CLAUDE.md).
- ~~Teks PIN wizard tersimpan sementara di tabel transient~~ — ditutup SEC-151 (19.0.1.4.0, lihat atas).
- Sync tertunda > 5 menit setelah persetujuan (mis. jaringan putus tepat setelah Validate) -> sync ditolak dan order
  tertahan di antrean sync POS; kasir menghubungi Store Manager untuk persetujuan ulang lewat order baru (belum ada
  alur "minta ulang" untuk order yang sudah tervalidasi di klien).
- Retur tanpa struk lewat backend (order POS manual + wizard Payment) dalam mode `manager_approval` selalu ditolak
  (tidak ada jalur persetujuan backend).
- Aturan `efn_audit` (log nilai lama/baru) untuk `efn.pos.return.approval` tidak ditambahkan (milik
  `efn_setup_audit`); record sendiri sudah append-only.
- Tour `EfnPosReturnApprovalTour` dan semua test E-41 **belum dijalankan** penulis (tanpa shell).

- **Fit-gap:** W-24 / E-31 (`docs/08 §9.3`). **Kontrak/keputusan:** `docs/04 §9.9` L-23 (Q-F18-4 proporsional,
  Q-F18-5 retur tanpa struk tunai saja, Q-F18-6 QRIS statis dicatat atas metode asal + transfer manual SOP Finance).
  ADR-031 (tanpa modul pihak ketiga; ide dari OCA/pos modul retur — tidak disalin, 19.0 belum diverifikasi).
- **Risiko dimitigasi:** R-O05 (retur/refund), R-B06 (fraud kasir: refund kartu dicairkan tunai).
- **Depends:** `point_of_sale`, `efn_core` (CE + efn_*). LGPL-3. **`auto_install: True`** (SEC-122, versi 19.0.1.1.0).

## Re-review G18 (SEC-125/126/127/129, versi 19.0.1.2.0)
Modul ini menjadi lapis **integritas order & pembayaran POS** untuk semua modul efn POS (dependency
`efn_pos_loyalty_ledger` dan `efn_pos_otp_gate`).
- **SEC-125: lunas hanya lewat `_process_saved_order`.** Sync POS dan wizard Payment backend (`pos.make.payment`,
  ACL CE: POS manager) memanggilnya. Di sana seluruh validasi berjalan: E-30 Poin EFN, E-31 metode asal, SEC-116
  `is_change`, dan gerbang OTP promo. Order draft/cancel yang berpindah ke `paid`/`done`/`invoiced` di luar penanda
  `POS_ORDER_SAVE_SCOPE` ditolak (`UserError`), juga untuk POS manager. Ini menutup `action_pos_order_paid` lewat
  RPC dan `write({'state': ...})`. Hal yang sama berlaku untuk `create` order ber-state paid di luar sync. `amount_paid`
  dihitung ulang dari pembayaran nyata sebelum cek lunas, sehingga `amount_paid` yang ditulis lewat RPC tidak
  melunasi order. Superuser/sudo dikecualikan.
- **SEC-126: penanda sync bertoken.** `_process_order` hanya mengizinkan order yang sedang disinkron (id order lama
  / uuid order baru). Perintah `payment_ids`/`lines` payload yang menunjuk record order lain
  (`[1|2|3|4, id]`, `[6, 0, ids]`) ditolak sebelum diproses core. Guard `pos.payment` juga memeriksa kepemilikan record
  yang disentuh di dalam sync, termasuk lewat `relations_uuid_mapping` core.
- **SEC-127:** kunci order asal = `UPDATE pos_order SET write_date = write_date WHERE id = …` (`efn_core.scope.touch_row`).
  Refund paralel kedua gagal `SerializationFailure` lalu Odoo me-retry RPC dengan snapshot baru. Diuji dengan dua
  koneksi PostgreSQL nyata (`efn_core/tests/test_scope.py`).
- **SEC-129: DB lama.** `auto_install` saja tidak memasang modul ini saat `-u` (dikonfirmasi: DB dengan addons
  8291cfc + `-u efn_pos_otp_gate,efn_pos_loyalty_ledger`). Karena kini menjadi dependency kedua modul itu, `-u`
  ikut memasangnya (terverifikasi: state `installed`, 19.0.1.2.0). Cek pra-go-live/runbook deploy:
  `SELECT state FROM ir_module_module WHERE name = 'efn_pos_refund_origin'` harus `installed`.

## Review r11 (L-25a/d/e/j, versi 19.0.1.1.0)
- **SEC-122: terpasang otomatis.** Dipilih `auto_install` (bukan dependency `efn_setup_pos`): modul ikut terpasang di
  setiap DB yang punya `point_of_sale` + `efn_core` (semua DB POS EFN), tidak bergantung pada modul konfigurasi
  functional atau daftar instal. Kontrol E-31/SEC-113/116/117 tidak bisa absen diam-diam; menambah dependency di modul
  setup tetap mungkin kemudian tanpa konflik.
- **SEC-113: pembayaran order POS terkunci di luar sync** (`models/pos_payment.py`). ACL CE memberi kasir CRUD penuh
  `pos.payment` (terverifikasi `call_kw` kasir di Odoo 19 nyata: write metode/nominal, create, unlink pada order paid
  semuanya DITERIMA sebelum perbaikan). Kini `create`/`unlink`/`write` (`amount`, `payment_method_id`, `pos_order_id`,
  `is_change`) untuk order **bukan draft** ditolak (`UserError`), kecuali di jalur sync resmi
  (`pos.order._process_order`, penanda per cursor `efn_core/scope.py`; tidak bisa diisi context RPC) atau kode server
  ber-`sudo` (penutupan sesi, pembayaran online core). Order draft mengikuti alur standar (wizard pembayaran backend).
  Pembayaran Poin EFN lebih ketat lagi (juga draft) di `efn_pos_loyalty_ledger`.
- **SEC-116: `is_change` dihitung ulang server** (`_efn_recompute_change_flags`, sync draft & final, sebelum E-31).
  Sah hanya untuk pembayaran **tunai** negatif dengan kumulatif |kembalian| <= min(jumlah tender tunai positif, jumlah
  tender positif - total baris order), ditambah satu satuan pembulatan tunai bila config memakai cash rounding.
  Selebihnya `is_change = False` sehingga dinilai sebagai uang keluar (refund kartu yang "dikembalikan tunai bertanda
  kembalian" ditolak). Berlaku juga untuk baris kembalian yang dibuat core dari `amount_return` payload.
- **SEC-117: refund mengunci order asal** (`_efn_lock_origin`: `SELECT ... FOR UPDATE`) sebelum menghitung sisa per
  metode; refund paralel atas satu struk dari dua terminal diserialkan.

## Aturan server (saat sync order, `pos.order._process_saved_order`)
- Hanya pembayaran **negatif** (uang keluar, bukan baris kembalian `is_change`) yang dinilai.
- **Refund ber-struk** (baris `refunded_orderline_id`): setiap metode harus ada di order asal; nominal per metode ≤
  dibayar bersih di order asal (kembalian = pembayaran tunai negatif core) − refund tersinkron sebelumnya.
- **Order campuran** (ber-struk + barang qty < 0 tanpa asal): ditolak (SEC-147).
- **Retur tanpa struk** (barang qty < 0 tanpa asal): `efn.pos.refund.unlinked_return_mode` = `manager_approval`
  (default E-41) | `cash` (tunai saja) | `deny` (ditolak). Selalu tunai saja.
- Pelanggaran -> `UserError` (sync ditolak). Penjualan & tukar barang tanpa uang keluar tidak terpengaruh.
- QRIS statis / gateway tanpa reversal: dicatat atas metode asal; pengembalian dana fisik lewat transfer manual (SOP).
- Bersama E-30 (Poin EFN): metode Poin EFN diperlakukan sama (subset + batas); nilai poinnya divalidasi E-30.

## RPC & frontend
- `pos.config.efn_refund_origin_allocation(original_order_id, refund_amount)` (di `RPC_ALLOWLIST`): grup POS user +
  akses config + company + sesi terbuka; order asal dibaca dengan record rule. Mengembalikan alokasi proporsional
  kumulatif per metode (`amount`, `max_amount`).
- Layar bayar refund: hanya metode asal (retur tanpa struk: hanya tunai), nominal terisi otomatis; offline: disaring
  dari pembayaran order asal yang termuat, tanpa isi otomatis.

## Uji
- Unit (`tests/test_refund_origin.py`, tag `efn`): metode asal OK, metode lain ditolak, split + batas + kumulatif,
  kembalian tunai, retur tanpa struk (cash/deny), RPC & akses.
- Unit r11 (`tests/test_payment_integrity.py`, tag `efn`): SEC-113 (kasir & manajer POS ditolak ubah/buat/hapus
  pembayaran order paid, context tidak membuka jalur, draft & sudo tetap jalan), SEC-116 (is_change / amount_return
  palsu pada refund kartu ditolak, kembalian sah tetap), SEC-117 (kunci sebelum hitung sisa), SEC-122.
- RPC nyata r11 (`tests/test_payment_integrity_rpc.py`, tag `efn_tour`, `call_kw` sebagai kasir): SEC-113, SEC-116,
  dan probe ACL `res.partner.bank` untuk SEC-121 (milik `efn_setup_pos`, hanya dicatat di log).
- Tour (`EfnPosRefundOriginTour`, tag `efn_tour`): order dibayar Bank -> refund -> hanya Bank ditawarkan, terisi otomatis.
- E-41 (`tests/test_return_approval.py`, tag `efn`): default/mode, sync tanpa persetujuan ditolak, PIN benar ->
  sync memakai sekali, dipakai ulang/order lain ditolak, persetujuan baru menggantikan, kedaluwarsa, nominal lebih,
  kasir lain, tetap tunai, `deny`, PIN salah -> kunci setelah 5 + buka setelah 15 menit, sukses mereset hitungan,
  approver = kasir, approver tanpa grup/company lain/tanpa PIN, input tidak sah, akses RPC, PIN tidak di-log, record
  append-only + record rule, retensi minimum, wizard PIN (hash pbkdf2, hanya diri sendiri/admin, aturan PIN, identity
  check, hash tidak terbaca). Tour `EfnPosReturnApprovalTour` (`tests/test_return_approval_tour.py`, `efn_tour`):
  qty -1 -> Cash -> Validate -> PIN salah (pesan) -> PIN benar -> struk tersinkron; server: 1 `failed` + 1 `consumed`.
- Manual: jual split tunai+kartu -> Orders -> refund sebagian -> cek alokasi & coba ganti metode (ditolak saat sync).

## Belum / batasan
- Exchange (refund + barang baru) dinilai pada pembayaran negatifnya saja.
- Order asal dari sesi/POS lain yang belum termuat saat offline: tanpa saringan klien; server tetap menolak.
- SEC-117: kunci baris menyerialkan sync paralel; terverifikasi lewat spy (urutan kunci -> hitung sisa), belum lewat
  uji dua koneksi nyata (data uji tidak ter-commit sehingga tidak terlihat koneksi lain).
- Persetujuan supervisor untuk retur tanpa struk: **selesai di E-41** (bagian atas).
