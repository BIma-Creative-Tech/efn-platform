# Security Review G45 Supermarket (expiry & markdown, SEC-209/214/215) — 19 Nov 2026

Reviewer: efn-security-reviewer (read-only; hanya membaca kode). Worktree `E:\Projects\efn-platform-g44`, branch
`feat/wave-45-supermarket-expiry`. Cakupan = modul baru `efn_expiry_monitor`, perubahan `efn_assortment` (SEC-209),
`efn_replenishment` (constraint overlap), `efn_setup_supermarket` tahap B, dan infra SEC-214/215 (forecast-worker lock,
clickhouse-init, Loki/Prometheus rules, `efn-forecast-key-status`, gerbang deploy `FORECAST_ENABLED`).
Acuan: CLAUDE.md, docs/04 §28.5a/§28.10, `SECURITY_REVIEW_2026-11-18_g44.md`. Nomor temuan lanjut: **SEC-216..224**.

Catatan metode: reviewer tidak punya akses shell, jadi `git diff` tidak dijalankan. File di daftar cakupan dibaca langsung
dari worktree. Tidak ada test yang dijalankan (review statis).

## Ringkasan eksekutif

- Tidak ada temuan **Critical/High**. **Tidak ada yang memblokir PR**, karena `efn_expiry_monitor.enabled=false` secara
  default di semua env (termasuk demo) dan `FORECAST_ENABLED` di production butuh izin Superadmin.
- Dua temuan **Medium** wajib ditutup sebelum flag expiry dinyalakan di pilot SMD. (1) PIN supervisor E-41 punya dua
  penghitung lockout terpisah (retur dan override kedaluwarsa), sehingga jatah tebakan berlipat dua. Tidak ada alert
  untuk brute force. (2) Validasi saat sync memakai `date_order` dan `user_id` kiriman klien. Kasir yang memundurkan jam
  perangkat bisa menjual lot kedaluwarsa **tanpa satu pun event audit**.
- Ada lima temuan Low dan dua Info: cakupan rule "semua outlet" terlalu lebar untuk grup override, field alert bisa
  ditulis lewat RPC, celah TOCTOU di override penerimaan, sisa bypass SEC-209, akuntansi kuota markdown, serta hardening
  infra/retensi.
- SEC-214 (lock + hash, fail closed) dan SEC-215 (HOST IP CIDR, pagar REVOKE, alert run/key) **sudah ditutup dengan
  benar**. SEC-207 (audit parameter `efn_%`) juga sudah tertutup, jadi parameter G45 ikut teraudit.

## Tabel temuan

| ID | Severity | Area | File:baris | Ringkas | Risiko |
|---|---|---|---|---|---|
| SEC-216 | Medium | efn_expiry_monitor POS | `efn_expiry_monitor/models/efn_expiry_event.py:120-140`; `models/pos_config.py:218-243,300-306`; `efn_pos_refund_origin/models/efn_pos_return_approval.py:94-115` | Lockout PIN E-41 dihitung terpisah per model (expiry vs retur), jadi 10 tebakan/15 menit per supervisor. RPC daftar approver memudahkan rotasi target. Tidak ada alert | R-S (fraud POS), L-51 |
| SEC-217 | Medium (PLAUSIBLE, runtime belum diuji) | efn_expiry_monitor POS | `efn_expiry_monitor/models/pos_order.py:60,85,100-103,120`; `models/efn_expiry_event.py:161-164` | Keputusan server memakai `order.date_order` dan `order.user_id` dari payload klien. Jam perangkat dimundurkan = lot kedaluwarsa terjual tanpa event. `user_id` dipalsukan = event "override sah" atas nama supervisor | R-L07, R-S |
| SEC-218 | Low | efn_expiry_monitor record rule | `security/efn_expiry_monitor_security.xml:60-71`; `security/ir.model.access.csv:2,4`; `efn_setup_supermarket/demo/res_users_demo.xml:86-97` | Rule "semua outlet" untuk `group_expiry_override` berlaku untuk semua perm. Digabung dengan `stock.group_stock_user` (implied Assortment Manager), user bisa create/write/unlink markdown draft dan menutup alert outlet lain | Q-SM4 |
| SEC-219 | Low | efn_expiry_monitor alert | `models/efn_expiry_alert.py:39-61`; `security/ir.model.access.csv:2` | `efn.expiry.alert` writable oleh stock user tanpa guard field server. `stage/state/qty/days_left/warehouse_id` bisa diubah lewat RPC | R-L07 |
| SEC-220 | Low | efn_expiry_monitor penerimaan | `models/stock_picking.py:91-107`; `wizard/efn_expiry_receipt_override_wizard.py:35-50` | Setelah override dicatat, `_action_done` melewati cek seluruh picking. Lot lain yang ditambahkan sesudahnya tidak dicek | Q-SM9 |
| SEC-221 | Low (PLAUSIBLE) | efn_assortment SEC-209 | `efn_assortment/models/stock.py:93-112,119-127,149-151` | Langkah transit hanya dicek bila lokasi transit punya `company_id`. Lewat transit antar-PT (company kosong) atau lokasi internal tanpa gudang, transfer dua langkah tetap lolos. Tujuan yang diubah setelah confirm tidak dicek ulang | Q-SM22, ADR-045 |
| SEC-222 | Low | efn_expiry_monitor markdown | `models/pos_order.py:134-139,150-164` | Retur mengurangi `qty_sold` berdasarkan klaim `efn_markdown_ref` di baris retur, bukan baris asal (beda dengan docs/04 §28.5a). Cek harga markdown persen mengabaikan `price_unit` | Q-SM4 |
| SEC-223 | Info | infra forecast | `scripts/host/efn-forecast-key-status:88-98`; `infra/clickhouse/clickhouse-init.sh:302-310`; `scripts/lib/deploy-validate.sh:554-558` | Key tanpa kedaluwarsa hanya tercatat di journald (tanpa metrik/alert). Pagar `efn_forecast` tidak memeriksa definisi/DEFINER view. CIDR boleh /16 | R-T15 |
| SEC-224 | Info | efn_expiry_monitor data | `models/efn_expiry_event.py:80-168`; `models/pos_config.py:54-73` | `efn.expiry.pos.approval` tanpa autovacuum/retensi (E-41 punya). Kolom `reason` berupa teks bebas. Field preload dihitung via sudo setiap kali `pos.config` dibaca | PDP (minimisasi), R-T |

## Detail

### SEC-216 (Medium) — Lockout PIN supervisor terpecah dua, tanpa alert

- **Bukti.** PIN yang sama (`res.users.efn_return_pin_hash`, 6–8 digit, `return_pin.py:12`) diverifikasi di dua jalur:
  - `efn_pos_refund_origin`: lockout `efn.pos.return.approval._efn_recent_failures`, 5 gagal dalam 15 menit.
  - `efn_expiry_monitor`: lockout `efn.expiry.pos.approval._efn_recent_failures` (`efn_expiry_event.py:120-140`), dengan
    ambang yang sama tetapi **di tabel lain**.

  Kegagalan di satu jalur tidak dihitung di jalur lain. `efn_expiry_override_approvers` (`pos_config.py:218-243`) juga
  mengembalikan semua supervisor ber-PIN di PT itu (maks. 100) kepada kasir mana pun. Log kegagalan berupa
  `_logger.warning(...)` (`:305`), tetapi tidak ada aturan Loki/Prometheus untuknya (grep `infra/observability` = 0 hasil).
- **Skenario.** Kasir dengan sesi POS terbuka memanggil `efn_expiry_override_request` dan RPC persetujuan retur
  bergantian. Untuk setiap supervisor ia bisa mencoba 2 × 5 PIN per 15 menit, yaitu ±960 per hari. Dengan 10 supervisor
  dan PIN 6 digit, peluang menebak satu PIN ±1% per hari (±25% per bulan) tanpa alarm. PIN yang tertebak membuka retur
  tanpa struk (uang keluar, E-41) dan penjualan lot kedaluwarsa atas nama supervisor itu.
- **Rekomendasi.**
  1. Satukan penghitung kegagalan PIN di helper bersama `efn_pos_refund_origin`, misalnya
     `return_pin.recent_failures(env, approver)` yang menjumlahkan kedua model, atau tabel/kolom kegagalan per user.
     Kedua jalur memeriksa dan mencatat ke penghitung yang sama.
  2. Tambahkan juga batas per kasir/sesi (mis. 10 gagal per jam per `cashier_user_id` lintas approver).
  3. Tambahkan alert Loki untuk `EFN expiry override: PIN salah` dan pesan sejenis di E-41 (≥ 5 per 15 menit per config).
  4. Pertimbangkan agar daftar approver hanya memuat supervisor yang `efn_outlet_warehouse_ids`-nya mencakup gudang config.

### SEC-217 (Medium, PLAUSIBLE) — Validasi sync mempercayai jam dan identitas kasir dari klien

- **Bukti.**
  - `_efn_expiry_after_sync` menentukan lot kedaluwarsa dengan `lot_blocked(lot, self.date_order)` (`pos_order.py:85`).
    Periode markdown dicek dengan `order_day` hasil turunan `date_order` (`:60,120`).
  - Override sah bila `self.user_id` punya grup override (`:100-102`). `_efn_find_valid` membandingkan
    `approval.cashier_user_id != order.user_id` (`efn_expiry_event.py:163`).
  - Di POS CE 19, `date_order` dan `user_id` ikut dalam payload order dari browser. Bukti tidak langsung:
    `efn_pos_bff_order/models/efn_pos_qr_order.py:520` sengaja mengisi `user_id` di payload server. Sebaliknya, E-41
    (`efn_pos_return_approval.py:134`) dengan benar memakai `self.env.uid`.
- **Skenario.**
  1. Kasir memundurkan jam tablet POS ke sebelum tanggal kedaluwarsa, lalu memindai lot yang sudah kedaluwarsa. Blokir
     klien (`Date.now()`) lolos. Server juga menilai lot belum kedaluwarsa pada `date_order`, sehingga **tidak ada
     `pos_expired_no_override`**. Satu-satunya kontrol deteksi (order lunas tidak ditolak) hilang. Ini terkait keamanan
     pangan (R-L07).
  2. Kasir memajukan jam ke periode markdown yang mulai ≤ 7 hari lagi (data preload memuatnya, `pos_config.py:146-152`).
     Server menganggap markdown sah karena `order_day` ikut maju.
  3. Payload sync dengan `user_id` = id supervisor dan `efn_expiry_override=true` tercatat sebagai
     `pos_expired_override` dengan `approver_id` supervisor yang tidak pernah menyetujui.
- **Rekomendasi.**
  - Nilai juga terhadap waktu server: lot dianggap terblokir bila kedaluwarsa pada `date_order` **atau** pada waktu
    terima (`fields.Datetime.now()`). Untuk order online selisihnya kecil.
  - Untuk order offline, catat event baru `clock_skew` bila `date_order` di luar
    `[session.start_at, now + toleransi]` (`start_at` ditulis server). Pakai jendela yang sama untuk validasi periode
    markdown.
  - Untuk identitas, gunakan `self.env.uid` saat sync (pola E-41) atau tolak klaim override bila
    `order.user_id != env.uid`. Catat `employee_id` (`pos_hr`) di event.
  - Catatan terkait: flag `override` di preload dihitung dari `env.user` yang membuka POS (`pos_config.py:69`). Pada
    perangkat `pos_hr` dengan akun perangkat bersama, semua karyawan di perangkat itu mewarisi override lokal tanpa PIN.
    Dokumentasikan bahwa akun perangkat **tidak boleh** diberi `group_expiry_override`, atau tegakkan lewat `efn.sod.rule`.
  - Tambahkan test sync dengan `date_order` mundur dan `user_id` asing.

### SEC-218 (Low) — Rule "semua outlet" untuk grup override berlaku untuk tulis/hapus

- **Bukti.** `efn_lot_markdown_rule_all_outlets` dan `efn_expiry_alert_rule_all_outlets` (`security.xml:60-71`) tidak
  mengatur `perm_*`, jadi berlaku untuk read/write/create/unlink. Grup override hanya diberi ACL read (`csv:3,5`). Namun
  user yang sekaligus stock user mendapat ACL tulis dari `access_efn_lot_markdown_stock_user` (`csv:4`) dan
  `access_efn_expiry_alert_stock_user` (`csv:2`). Rule grup di-OR, sehingga cakupan outlet hilang. Persona demo
  "Supervisor Ops" (`res_users_demo.xml:86-97`) = override + Assortment Manager (implied `stock.group_stock_user`)
  persis kasus ini.
- **Skenario.** Supervisor Ops mengubah harga/diskon markdown **draft** yang disiapkan kepala toko SMD02 (sebelum
  diaktifkan, masih dalam batas 50%), menghapus draft outlet lain, atau menutup alert near-expiry outlet lain lewat
  `action_efn_close`. Aktivasi tetap tertahan `_efn_role_limit`.
- **Rekomendasi.** Pisahkan rule untuk `group_expiry_override` dengan `perm_read=True` dan perm lain `False`. Rule tulis
  "semua outlet" hanya untuk Markdown Manager dan `stock.group_stock_manager`. Tambahkan test: supervisor override yang
  juga stock user tidak bisa menulis markdown outlet lain.

### SEC-219 (Low) — Field alert dapat ditulis langsung lewat RPC

- **Bukti.** ACL stock user `perm_write=1` pada `efn.expiry.alert` (`csv:2`). Field `stage`, `state`, `qty`,
  `days_left`, `warehouse_id`, dan `company_id` hanya `readonly=True` di UI. Tidak ada override `write` (berbeda dengan
  `efn.lot.markdown`, yang punya `SERVER_FIELDS`).
- **Skenario.** Kepala toko memanggil `write({'stage': 'expired', 'state': 'actioned'})` pada alert outletnya. Cron hanya
  menaikkan tahap (`efn_expiry_alert.py:133`), jadi alert tidak memicu notifikasi lagi dan ringkasan buyer kehilangan lot
  itu. Mengubah `warehouse_id` memindahkan alert ke outlet lain.
- **Rekomendasi.** Tambahkan `write` guard: selain `env.su`, hanya field aktivitas/chatter yang boleh ditulis, karena
  semua transisi sudah lewat `action_*` ber-sudo. Alternatifnya, hapus `perm_write` dari ACL lalu jalankan aksi lewat
  `check_access` + sudo, seperti yang sudah dilakukan.

### SEC-220 (Low) — Override sisa umur penerimaan tidak terikat ke lot yang di-override

- **Bukti.** `_action_done` melewati picking yang punya `efn_short_life_override_id` (`stock_picking.py:94-95`). Event
  hanya menyimpan ringkasan teks (`wizard:39-49`).
- **Skenario.** Markdown Manager meng-override lot X (sisa 5 hari, minimum 7). Staf penerima lalu mengganti move line
  menjadi lot Y (sisa 1 hari) dan memvalidasi. Tidak ada blokir, dan event menyebut lot X.
- **Rekomendasi.** Simpan himpunan `(move_line/lot_id, qty)` yang di-override (Many2many/JSON di event). Saat
  `_action_done`, tetap hitung pelanggaran dan tolak yang tidak tercakup override. Alternatifnya, kosongkan override bila
  `move_line_ids` berubah.

### SEC-221 (Low, PLAUSIBLE) — Sisa bypass SEC-209

- **Bukti.** `_efn_transit_leg_target` keluar bila `not source.company_id` (`stock.py:104`). Lokasi transit antar-PT CE
  (company kosong) tetap bisa dipilih manual: DC → transit antar-PT → outlet PT sama tidak dicek. Lokasi internal tanpa
  `warehouse_id` (bukan transit) juga tidak tertangkap di kedua cabang (`:119-127`). Penegakan hanya di `_action_confirm`,
  sehingga mengubah `location_dest_id` setelah confirm tidak dicek ulang.
- **Skenario.** Staf DC mengirim produk berizin alkohol ke outlet tanpa `alcohol_license` lewat dua picking manual dengan
  lokasi antara bertipe internal tanpa gudang. Tidak ada override, sehingga tidak ada jejak efn.assortment.override.
- **Rekomendasi.** Untuk PT ber-DC, nilai setiap move yang **tujuannya** gudang outlet non-DC dan sumbernya bukan
  supplier/customer/inventory, tanpa melihat jenis lokasi sumber. Atau ulangi cek di `_action_done`. Pastikan laporan
  harian G44 mencakup pola ini. Uji `warehouse_id` lokasi transit CE 19 di runtime (catatan README).

### SEC-222 (Low) — Akuntansi kuota dan cek harga markdown

- **Bukti.**
  - Baris dengan `qty < 0` yang membawa `efn_markdown_ref` sah mengurangi `qty_sold` (`pos_order.py:136-139`) tanpa
    memeriksa `refunded_orderline_id.efn_markdown_id`. docs/04 §28.5a menyebut pengurangan berasal "dari baris asal".
  - Untuk markdown persen, `too_low` hanya membandingkan `line.discount` (`:155`). `price_unit` yang diturunkan manual
    tidak terdeteksi.
- **Skenario.** Retur baris non-markdown (dengan persetujuan retur) diberi klaim markdown, sehingga kuota terbuka lagi
  dan lebih banyak unit terjual dengan harga markdown. Kasir menurunkan `price_unit` lalu memakai diskon 30% yang sah,
  dan tidak ada `markdown_price_mismatch`.
- **Rekomendasi.** Kurangi `qty_sold` hanya bila `line.refunded_orderline_id.efn_markdown_id == markdown`, dengan qty
  dibatasi qty asal. Untuk markdown persen, bandingkan `effective_unit` dengan `lst_price × (1 − pct)` per basis pajak
  yang sama, seperti cabang `price`.

### SEC-223 (Info) — Hardening infra forecast

- `efn-forecast-key-status` menghitung key tanpa kedaluwarsa sebagai aktif. Peringatannya hanya masuk journald (`:93`),
  tanpa metrik, jadi pelanggaran kebijakan "≤ 35 hari" tidak memicu alert. Tambahkan
  `efn_forecast_api_key_no_expiry_keys{company}` dan alert `> 0`.
- Pagar `efn_forecast` (`clickhouse-init.sh:302-310`) hanya memeriksa nama dan engine. View dengan nama allowlist yang
  di-`CREATE OR REPLACE` menjadi SELECT atas tabel lain tetap lolos. Tambahkan cek `create_table_query` memuat
  `DEFINER = efn_forecast_definer` dan hanya `efn_dwh.v_fc_*`. Butuh hak DDL, jadi risikonya rendah.
- `CH_FORECAST_READER_HOST_CIDR` boleh /16 (`deploy-validate.sh:555`). `172.30.0.0/16` mencakup semua network EFN dan
  meniadakan SEC-215. `check-encryption.sh:425` hanya membandingkan dengan ipam bila overlay ter-render. Saran: batasi
  menjadi /24../32 di jalur satu host.

### SEC-224 (Info) — Retensi dan minimisasi

- `efn.expiry.pos.approval` tidak punya `@api.autovacuum`, padahal `efn.pos.return.approval` punya
  (`efn_pos_return_approval.py:143-152`). Isinya id staf dan alasan. Tetapkan retensi dan catat di
  `docs/compliance/PDP_DATA_INVENTORY.md`.
- `reason` (dialog override) dan `note` markdown berupa teks bebas. Placeholder sudah memperingatkan "tanpa data
  pelanggan". Pertahankan dan sebutkan di inventaris PDP.
- `efn_expiry_pos_data` adalah compute non-stored ber-sudo (`read_group` quant sampai 20.000 baris). Setiap pembacaan
  `pos.config` oleh user yang berhak membacanya memicu query berat dan menampilkan stok per lot. Tidak ada PII, tetapi
  pertimbangkan `groups="point_of_sale.group_pos_user"` pada field itu.

## Yang sudah benar (kontrol yang ada)

- **Multi-company & cakupan outlet.** Ada rule global `company_id in company_ids` di keempat model baru dan
  `_check_company_auto`. Aksi memeriksa `check_access` + company sebelum sudo. Sudo hanya untuk transisi state setelah
  cek. Batas diskon per peran (50/90%, param dengan rentang + fallback default) ditegakkan saat aktivasi. Field server
  (`state`, `qty_sold`, `activated_*`) dijaga di `create/write`. Markdown terkunci setelah draft, dan hanya draft yang
  boleh dihapus.
- **PIN override.** Hash tidak pernah ke browser, verifikasi passlib waktu-konstan, input dibatasi (`PIN_MAX_INPUT`,
  regex `order_ref`, panjang lot/alasan). Persetujuan terikat config + sesi + uuid order + produk + lot + kasir + TTL dan
  ditandai `consumed` (idempoten untuk order yang sama). Supervisor tidak bisa menyetujui dirinya sendiri. PIN dihapus
  dari state dialog segera setelah dikirim, input `type=password`. Override offline hanya untuk pemegang grup (tanpa PIN
  offline).
- **Jejak audit.** `efn.expiry.event` dan `efn.expiry.pos.approval` append-only (`_immutable`) dan teraudit `efn_audit`,
  begitu juga markdown dan ambang kategori. Parameter `efn_expiry_monitor.*` ikut teraudit karena SEC-207 sudah ditutup
  (`efn_setup_audit` domain `efn\_%` + migrasi 19.0.2.1.0).
- **Klaim klien bukan keputusan.** `efn_lot_source`/`efn_markdown_ref`/`efn_expiry_override` divalidasi ulang. Markdown
  hanya sah untuk sumber `gs1/manual`, outlet/PT/produk/lot cocok, dan periode berlaku. Kuota dikunci baris
  (`touch_row`). Order lunas tidak ditolak, tetapi selisih dicatat.
- **Permukaan RPC.** 11 entri baru tercatat di `test_rpc_surface.py:413-459` dengan guard yang sesuai kode. Tidak ada
  RPC expiry yang diteruskan gateway/BFF (grep `services/` di luar Odoo = 0), jadi hanya sesi POS/back-office internal.
  Data POS tanpa PII (nama lot, tanggal, qty, markdown). Daftar approver hanya `{id, name}`.
- **Transfer antar outlet.** Tujuan divalidasi di server (PT sama, bukan DC, bukan asal, qty ≤ alert, assortment
  `ok/dsd_only`). Picking dibuat dengan hak user, bukan sudo.
- **efn_setup_supermarket tahap B.** User demo tanpa password/email/HP, satu company, dan `noupdate`. Migrasi hanya
  menerapkan demo bila `module.demo`. Ambang tidak menimpa nilai pengguna. `efn_expiry_monitor.enabled` tidak dinyalakan
  di demo. Constraint overlap jadwal `efn_replenishment` benar (parent_path berakhiran `/`, jadi tidak ada salah prefix).
- **SEC-214 tertutup.** `requirements.lock` nyata (pip-compile Py 3.12, hash sha256). Dockerfile fail closed pada
  placeholder, `--require-hashes --no-deps` + `pip check`, user 65534, base tag@digest. `check-py-lock.sh` menolak
  opsi indeks/URL dan lock basi. `lock-python-deps.sh` memvalidasi nama service dan FROM digest, parameter lewat env
  (tanpa injeksi), dan menulis secara atomik.
- **SEC-215 tertutup.** `HOST IP` dengan validasi CIDR privat kanonik (charset `[0-9./]` sebelum disisipkan ke SQL) di
  init dan di deploy-validate. Ada pagar REVOKE fail-closed untuk objek non-allowlist. Alert run gagal/stale (regex
  berjangkar, dead-man bersyarat worker aktif) dan alert masa berlaku key ada. `efn-forecast-key-status` hanya membaca
  id + kedaluwarsa (nilai key tidak pernah dibaca), wajib root, memeriksa owner/mode `.env` tanpa `source`, dan menulis
  textfile secara atomik.
- **Gerbang deploy.** `FORECAST_ENABLED` masuk `EFN_GATED_FEATURES` (production butuh `EFN_ALLOW_FEATURE_ON`). Deploy
  menolak `true` tanpa profil data atau dengan API key placeholder. Cron di `.env` dikutip setelah validasi regex.
  `IMAGE_FORECAST_WORKER` lewat manifest + cosign.

## Belum terverifikasi

- Perilaku runtime CE 19: apakah `date_order`/`user_id` payload POS dipakai apa adanya (SEC-217), dan `user_id` order
  saat `pos_hr` login karyawan.
- Aset POS OWL belum diuji di layar (tidak ada tour; README modul mengakuinya). FEFO/blokir/markdown di klien hanya UX.
  Penentu akhir tetap server.
- `warehouse_id` lokasi transit CE 19 (SEC-221) dan efek `restrict_lot_id` di wizard transfer.

## Status PR

**Tidak memblokir merge.** Syarat sebelum `efn_expiry_monitor.enabled=true` di pilot/production: SEC-216 dan SEC-217
ditutup (dengan test), SEC-218 diperbaiki karena persona demo terdampak langsung, dan SEC-224 (retensi) dicatat di
inventaris PDP.
