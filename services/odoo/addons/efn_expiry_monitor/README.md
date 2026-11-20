# efn_expiry_monitor — Near-expiry, markdown per lot, FEFO & blokir kedaluwarsa di POS

**Tujuan.** G45 Supermarket (`docs/04` §28.5a + §28.10, fit-gap `docs/fit-gap/G45_SUPERMARKET.md` S-05..S-10, S-14,
§3 opsi D). Odoo CE 19 tidak punya: ambang kedaluwarsa per kategori/outlet, harga per lot, FEFO otomatis di POS, blokir
lot kedaluwarsa di POS, cek sisa umur minimum saat terima. Modul ini menutup gap itu tanpa modul pihak ketiga.

ADR terkait (status *Proposed*): **ADR-045** (DC satu PT, assortment), **ADR-046** (forecast & replenishment, cap umur
simpan). ADR-031 (tanpa dependency pihak ketiga). Semua perilaku di belakang parameter
**`efn_expiry_monitor.enabled`** (default `false` di semua env; nyalakan setelah ambang per kategori diisi, Q-SM28/Q-SM9).

## Kontrak nama (dipakai efn-odoo-functional / efn_setup_supermarket)
| Item | Nama |
|---|---|
| Field kategori (Integer, hari, 0 = kosong) | `product.category.efn_near_expiry_days`, `efn_markdown_days`, `efn_min_receipt_days` |
| Grup | `efn_expiry_monitor.group_markdown_manager`, `efn_expiry_monitor.group_expiry_override` |
| Parameter | `efn_expiry_monitor.enabled` (false), `efn_expiry_monitor.max_discount_store_head` (50), `efn_expiry_monitor.max_discount_markdown_manager` (90), `efn_expiry_monitor.pos_lot_limit` (20000), `efn_expiry_monitor.override_ttl_seconds` (1800) |

**Pewarisan ambang:** kategori yang nilainya 0 memakai **induk terdekat yang terisi** (per field,
`product.category._efn_effective_days`). `efn_replenishment._efn_min_remaining_days` memakai helper yang sama. Nilai
sama (mis. 1/1/1) valid; constraint hanya menolak `efn_markdown_days > efn_near_expiry_days` pada kategori yang mengisi
keduanya, dan nilai di luar 0..3650.

## Model
| Model | Isi |
|---|---|
| `efn.expiry.alert` | company, outlet (`warehouse_id`), lot, produk, qty, `days_left`, `stage` near/markdown/expired, `state` open/actioned/closed, `date` (deteksi pertama), `last_run_date`. Unik (company, outlet, lot, date). mail.activity.mixin |
| `efn.lot.markdown` | §28.5a: `lot_id`, `warehouse_id`, `price` XOR `discount_pct`, `date_from/to`, `qty_cap` (0 = tanpa batas), `qty_sold`, `state` draft/active/expired/cancelled, `notify_members` + `notify_state` (`pending`), `activated_by/at`, `alert_id` |
| `efn.expiry.event` | jejak append-only: `receipt_override`, `pos_expired_override`, `pos_expired_no_override`, `markdown_cap_exceeded`, `markdown_invalid`, `markdown_price_mismatch` |
| `efn.expiry.pos.approval` | persetujuan supervisor (PIN) jual lot kedaluwarsa, terikat config/sesi/uuid order/produk/lot/kasir, TTL 30 menit, kunci 5 PIN salah/15 menit |
| `pos.order.line` | klaim POS: `efn_lot_source` (gs1/manual/fefo/auto), `efn_markdown_ref`, `efn_expiry_override`; server: `efn_markdown_id`, `efn_expiry_checked` |
| `stock.picking` | `efn_short_life_override_id` (hanya server), `efn_short_life_warning` (compute) |

Semua model baru: `company_id` wajib, `_check_company_auto`, rule multi-company global, ACL lengkap, teraudit
`efn_audit` (markdown, event, approval, + 3 field ambang `product.category`).

**Cakupan outlet:** stock user (kepala toko) hanya alert/markdown outlet di `res.users.efn_outlet_warehouse_ids`;
Markdown Manager, supervisor override, dan Inventory Administrator melihat **semua outlet di company user** (tanpa perlu
mengisi `efn_outlet_warehouse_ids`).

## Alur
1. **Cron harian** "EFN expiry": stok internal ber-lot di gudang non-DC untuk kategori terkelola → tahap
   (expired bila `expiration_date`/`removal_date` lewat atau sisa < 0; markdown ≤ ambang markdown; near ≤ ambang
   near). Satu alert terbuka per (outlet, lot), diperbarui tiap run; tahap naik → dibuka ulang + notifikasi; stok habis
   → `closed`. Idempoten per lot × outlet × hari. Notifikasi hanya untuk alert baru/naik tahap: 1 To-Do per outlet ke
   tiap kepala toko + 1 ringkasan per PT ke Markdown Manager (+ `efn_replenishment.group_replenishment_buyer` bila
   terpasang). Markdown aktif yang lewat `date_to` → `expired`.
2. **Tanpa duplikasi aktivitas CE `product_expiry`:** CE `stock.lot._alert_date_exceeded` membuat 1 To-Do per lot ke
   `responsible_id`. Bila flag menyala, override menandai lot di kategori terkelola EFN `product_expiry_reminded = True`
   **sebelum** `super()` (tanpa aktivitas), sehingga loop CE melewatinya. Kategori tanpa ambang EFN tetap memakai CE.
3. **Markdown** (dari alert atau menu): draft → *Aktifkan* (kepala toko outlet itu ≤ 50 %, Markdown Manager ≤ 90 %
   diskon efektif; `price` dihitung `1 − price/harga jual`). Setelah draft, harga/periode/lot/kuota terkunci (batalkan
   lalu buat baru). Tidak tumpang-tindih per lot × outlet. `notify_members` → `notify_state = pending` saja:
   **outbox `efn.campaign.outbox` + pengiriman ke member dibangun G46** (docs/04 §28.7).
4. **Penerimaan outlet dari DC** (`stock.picking._action_done`): move line ber-lot ke gudang non-DC dari lokasi transit
   (resupply 2 langkah) atau dari gudang DC PT yang sama dengan sisa umur < `efn_min_receipt_days` (efektif) →
   `UserError efn_expiry:short_life` berisi daftar. Override: wizard (Markdown Manager / Inventory Administrator,
   alasan ≥ 10) → `efn.expiry.event receipt_override`. Form picking menampilkan peringatan sebelum validasi.
5. **Transfer antar outlet** (alert near/markdown → wizard): tujuan = outlet non-DC PT sama dengan laju jual harian
   tertinggi (> asal): `efn.forecast.daily` run daily aktif (rata-rata 7 hari) bila ada, fallback move ke customer 28
   hari. Wajib lolos `_efn_assortment_allowed` (`dsd_only` dianggap boleh). Hasil: picking internal **draft**
   A/Stock → B/Stock dengan `restrict_lot_id` (bila field ada).
6. **POS** (aset OWL, `point_of_sale._assets_pos`):
   - Data lot (nama, kedaluwarsa, removal, qty di lokasi POS) + markdown aktif outlet dimuat saat sesi dibuka
     (`pos.config.efn_expiry_pos_data`) → berlaku offline; online diperbarui per produk (`efn_expiry_lot_candidates`).
   - **FEFO otomatis** (patch `PosStore.editLots`): produk ber-lot dipindai tanpa lot → lot kedaluwarsa terdekat yang
     belum kedaluwarsa, bukan lot markdown aktif, masih ber-stok (qty server − pemakaian order lokal belum sync), tanpa
     popup. Tanpa kandidat → popup CE.
   - **Blokir** lot kedaluwarsa/lewat removal (GS1 atau pilihan manual): kasir ber-grup override → konfirmasi (juga
     offline); selain itu supervisor + PIN (hash `efn_return_pin_hash` E-41, diverifikasi server; **online saja**).
     Mengganti lot baris yang sudah ada ke lot kedaluwarsa atau mengganti lot baris markdown ditolak.
   - **Markdown** (patch `addLineToOrder`): hanya bila lot dari GS1 `(01)(10)` (`opts.code.type === "lot"`) atau dipilih
     eksplisit; harga (`setUnitPrice` + `price_type = "manual"`) atau diskon (`setDiscount`). `qty_cap`: qty baris
     dibatasi sisa kuota (patch `PosOrderline.setQuantity`); kuota habis → harga normal.
   - **Server saat sync** (`pos.order._process_saved_order`, order lunas TIDAK ditolak): lot kedaluwarsa tanpa override
     sah → event `pos_expired_no_override`; dengan override sah (kasir ber-grup atau persetujuan PIN) →
     `pos_expired_override` (persetujuan `consumed`); markdown divalidasi ulang (PT, outlet, produk, lot, periode tanggal
     lokal, sumber gs1/manual) → `qty_sold` (baris dikunci) / `markdown_cap_exceeded` / `markdown_invalid` /
     `markdown_price_mismatch`.
   - Kanal online: markdown per lot **tidak berlaku** (fase 1, Q-SM12). Kiosk: perilaku lot belum diuji (Q-SM8).

## Risiko yang dimitigasi
R-T01 tidak tersentuh (tanpa BoM). R-O14/R-L07 (keamanan pangan: jual barang kedaluwarsa, terima barang hampir
kedaluwarsa), kebocoran diskon markdown ke barang segar (FEFO melewati lot markdown, markdown hanya lot eksplisit),
penyalahgunaan diskon (batas per peran + audit), replay persetujuan (terikat order+lot+kasir, sekali pakai), SEC-58
(semua RPC baru di `RPC_ALLOWLIST` efn_core dengan cek eksplisit). SEC-209 ditutup di `efn_assortment` 19.0.1.2.0.

## Pertanyaan terbuka
- **Q-SM3** model timbangan/pemindai GS1-128/DataBar Expanded (lot di label) — belum ada perangkat.
- **Q-SM4** batas diskon per peran & approval di atas X % — default 50/90 (parameter).
- **Q-SM9** sisa umur minimum dalam hari (dipakai) vs % umur simpan; siapa override (sementara Markdown Manager /
  Inventory Administrator).
- **Q-SM12** markdown di kanal online — fase 1 tidak.
- **Q-SM7** transfer antar outlet satu langkah (dipakai) vs lewat transit (catatan: penerimaan transit → outlet akan
  dikenai cek sisa umur).

## Cara uji manual
1. Pasang modul; Settings → parameter `efn_expiry_monitor.enabled = true`. Kategori induk: near 10, markdown 3,
   min terima 5; kategori anak dibiarkan 0.
2. Produk ber-lot + tanggal kedaluwarsa; stok 4 lot di outlet (−2, 2, 3, 20 hari). Jalankan cron "EFN expiry" → 3 alert
   (expired/markdown/near); jalankan lagi → tidak ada aktivitas baru.
3. Dari alert markdown: *Buat markdown* 30 % → *Aktifkan* sebagai kepala toko (≤ 50 % berhasil; 60 % ditolak, lalu
   aktifkan sebagai Markdown Manager).
4. Transfer DC → outlet dengan lot sisa 3 hari → *Validate* ditolak; *Override sisa umur* (Markdown Manager) → validasi.
5. POS outlet (picking type `use_existing_lots`): pindai EAN produk → lot "2 hari" dilewati (markdown), lot "3 hari"
   terpilih otomatis. Pindai GS1 `(01)GTIN(10)<lot markdown>` → diskon 30 %. Pindai GS1 lot kedaluwarsa → diblokir;
   supervisor + PIN → boleh. Matikan jaringan → FEFO & markdown tetap; override PIN diblokir.
6. Sync order → Inventory → Kedaluwarsa EFN → Jejak Audit memuat event; markdown `qty_sold` bertambah.

## Perbaikan security G45 (19.0.1.1.0, `docs/reviews/SECURITY_REVIEW_2026-11-19_g45.md`)
- **SEC-216** PIN: lockout **bersama** dengan retur tanpa struk E-41 (`efn_pos_refund_origin.return_pin.pin_locked`;
  model ini meng-inherit `efn.pos.return.approval._efn_pin_failures_total`/`_efn_cashier_pin_failures_total`):
  approver 5 gagal/15 menit lintas jalur + kasir 10 gagal/60 menit lintas approver (`cashier_locked`). Log WARNING
  `EFN_POS_PIN_FAIL path=expiry_override config=… cashier=… approver=… reason=wrong_pin|locked|cashier_locked` (tanpa
  PIN/hash). `efn_expiry_override_approvers` hanya supervisor dengan gudang config di `efn_outlet_warehouse_ids` atau
  tanpa outlet terdaftar (PT-wide), maks. 20, `{id, name}`.
- **SEC-217** Sync: waktu penilaian = `date_order` bila di `[session.start_at − 5 mnt, now + 5 mnt]`, selain itu waktu
  server + event `clock_skew` (berlaku juga untuk periode markdown). Identitas override = `env.uid` yang menyinkronkan
  (grup override atau persetujuan PIN untuk kasir itu); `order.user_id` payload diabaikan; event mencatat `env.uid`.
  **Akun perangkat bersama / `pos_hr` tidak boleh diberi `group_expiry_override`** (flag `override` preload dihitung dari
  user yang membuka POS, sehingga semua karyawan di perangkat itu mewarisinya) — belum ditegakkan kode (kandidat
  `efn.sod.rule`); `employee_id` pos_hr belum dicatat di event.
- **SEC-218** Supervisor override: rule baca-saja semua outlet (`*_rule_override_read`); tulis/buat/hapus semua outlet
  hanya Markdown Manager & Inventory Administrator. Migrasi `19.0.1.1.0` mencabut grup dari rule lama (noupdate).
- **SEC-219** `efn.expiry.alert.write`: non-sudo hanya field `message_*`/`activity_*`; transisi lewat aksi resmi.
- **SEC-220** Override penerimaan menyimpan `efn.expiry.event.lot_ids` (lot yang dievaluasi); `_action_done` tetap
  memblokir lot lain (ganti/tambah lot setelah override). Override ulang mencakup himpunan lot saat itu.
- **SEC-222** Retur memulihkan `qty_sold` hanya dari `refunded_orderline_id.efn_markdown_id` (dibatasi qty baris asal);
  klaim `efn_markdown_ref` di baris retur diabaikan (sesuai docs/04 §28.5a "dari baris asal"). Markdown persen:
  `markdown_price_mismatch` juga bila harga efektif < harga jual normal × (1 − persen) (pricelist di bawah harga normal
  ikut tercatat — detektif).
- **SEC-224** Autovacuum `efn.expiry.pos.approval`: `efn_expiry_monitor.pos_approval_retention_days` (default 730,
  min 365; isi: id staf + alasan pendek — perlu dicatat di `docs/compliance/PDP_DATA_INVENTORY.md` oleh DPO/architect).
  Preload `efn_expiry_pos_data` (grup POS user) hanya dihitung di dalam `pos.session.load_data` (penanda `cr.cache`);
  pembacaan pos.config lain mendapat `{"enabled", "deferred": true}`. Belum diverifikasi: jalur muat ulang data POS
  lain (bila ada) di luar `load_data` akan menerima payload `deferred` (POS lalu tanpa data lot offline → FEFO jatuh
  ke data online per produk).

## Batasan / belum
- **Belum dijalankan di Odoo 19 nyata oleh penulis** (tanpa runtime di mesin penulis); lead menjalankan test container.
- **Tour POS (G48)** `tests/test_expiry_tour.py` + `static/tests/tours/efn_expiry_tour.js` (tag `efn_tour`, job CI
  "Odoo POS UI tours"): (1) FEFO dari kartu produk tanpa popup, lot markdown aktif dilewati; (2) lot markdown dipilih
  lewat popup lot CE -> harga markdown -> bayar -> server `efn_markdown_id` + `qty_sold`; (3) lot kedaluwarsa -> dialog
  supervisor -> Batal (baris tidak masuk) -> PIN salah -> PIN benar -> baris override -> event `pos_expired_override`
  + persetujuan `consumed`. **Belum dicakup tour**: pindaian GS1 `(01)(10)` (butuh nomenklatur GS1 + helper scan),
  batas `qty_cap` lewat `setQuantity`, override lokal kasir ber-grup (`ask`), dan perilaku offline. Test server
  JS-less tetap di `tests/test_pos_expiry.py`.
- **Perbaikan G48 (ditemukan saat menulis tour)**: kartu produk CE 19 (`ProductScreen.addProductToOrder`) memanggil
  `addLineToCurrentOrder({ product_tmpl_id })` tanpa `product_id`, sehingga patch `addLineToOrder` dulu dilewati: lot
  tetap dipilih FEFO/override oleh `editLots`, tetapi `efn_lot_source`, harga markdown, dan `efn_expiry_override`
  tidak pernah dipasang dari kartu produk (hanya dari pindaian barcode). Sekarang produk diturunkan dari template
  (varian pertama, seperti core) dan pilihan lot dicocokkan dengan `product_id` baris aktual.
- API POS 19.0 diverifikasi dari source GitHub 19.0 (WebFetch, ringkasan): `PosStore.editLots(product,
  packLotLinesToEdit)` (auto-pilih 1 lot / fifo / lifo, tanpa fefo; popup mengembalikan `{modifiedPackLotLines,
  newPackLotLines}`), `addLineToOrder(vals, order, opts, configure)` dengan `opts.code.type === "lot"` → `lot_name =
  code.code`, `_barcodeGS1Action` memanggil `addLineToCurrentOrder(vals, { code: lotBarcode }, …)`, `getOrder()`,
  `data.call`, `ask`/`makeAwaitable` (`@point_of_sale/app/utils/make_awaitable_dialog`), `PosOrderline.setUnitPrice/
  setDiscount/setQuantity`, `price_type` (`"manual"` mencegah hitung ulang pricelist), `canBeMergedWith` menolak baris
  ber-lot (baris markdown tidak tergabung). **Belum terverifikasi**: bentuk persis `modifiedPackLotLines` (array vs
  objek; ditangani keduanya), apakah `setUnitPrice` sendiri mengubah `price_type`, perilaku `getSelectedOrderline()` di
  jalur edit lot, field `tracking` termuat di `product.template` POS (dipakai `isTracked()` core → diasumsikan ada).
- `pos_hr`: cek grup override memakai user yang membuka POS (`res.users`), bukan karyawan kasir `pos_hr`.
- Snapshot lot offline tidak berkurang oleh order yang sudah tersinkron di perangkat lain; stok lokal hanya dikurangi
  order lokal yang belum sync (heuristik id/draft).
- Refund baris markdown mengurangi `qty_sold` hanya bila baris refund membawa `efn_markdown_ref` (belum diverifikasi
  apakah refund core menyalin field ini).
- Server tidak memilih lot FEFO untuk order tersinkron **tanpa** lot (fit-gap §2.3 butir 4); belum dibangun.
- Filter reservasi di sisi DC (lewati lot yang pasti ditolak outlet, fit-gap S-10) belum dibangun.
- Override penerimaan berlaku per picking (bukan per lot); perubahan baris setelah override tidak dicek ulang.
- Default umur produk per kategori (S-05 "template mewarisi saat dibuat") belum dibangun.
- Perubahan parameter `efn_expiry_monitor.*` teraudit hanya bila aturan audit parameter `efn_*` (SEC-207,
  efn_setup_audit) terpasang.
- Stiker GS1 `(01)(10)` untuk lot markdown: laporan label belum dibangun (opsi GS1 label lot CE 19 belum diverifikasi).
- Ukuran data preload: dibatasi `pos_lot_limit` baris (urut kedaluwarsa); produk di luar batas tetap diambil online.

## Referensi desain (ADR-031)
Cybrosys `odoo_product_expiry_dashboard` (19.0, AGPL-3) — hanya ide dashboard; OCA `pos_lot_barcode` /
`pos_product_expiry` (18.0) — hanya ide pemindaian lot. Tidak ada kode yang disalin. Lisensi LGPL-3.
