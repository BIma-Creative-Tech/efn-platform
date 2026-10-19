# efn_online_kds_bridge (E-29a) — pesanan online F&B ke layar dapur (KDS)

> **Ringkasan manajemen.** Pesanan Paris Baguette / Bacha Coffee F&B yang dipesan di Super-App dan sudah dibayar
> langsung muncul di layar dapur outlet, dipecah per stasiun (dapur, bar) seperti pesanan kasir. Saat semua stasiun
> menekan "siap", pesanan otomatis berstatus *siap diambil* dan pelanggan diberi tahu. Tiket dapur hanya memuat nomor
> pesanan dan menu, tanpa nama, nomor HP, atau kode ambil pelanggan. Staf tetap bisa menandai "siap" secara manual.

- Versi `19.0.1.0.0`, LGPL-3, `depends`: `efn_online_order`, `efn_pos_kds`; `auto_install` (ikut terpasang bila keduanya
  ada). Tanpa modul pihak ketiga (ADR-031). Tidak ada RPC publik baru (`RPC_ALLOWLIST` tidak berubah).
- Kontrak: `docs/04_SERVICE_CONTRACTS.md` §14.B (B-6, B-7) + §14.7 L-72 (D-22-8 kanal F&B `confirm`, D-22-9 combo
  ditunda online). Spesifikasi: `docs/08` §10.6 + §13.2 (#1–#6).
- ADR: ADR-013 (KDS custom, *Proposed*), ADR-034 (kanal online lewat BFF, *Proposed*), ADR-027 (pajak dari Odoo),
  ADR-031. Risiko: R-T10 (saga online — status `ready` dari dapur), R-L01 (PDP: tiket tanpa data pelanggan), R-S03.
- Referensi desain: hanya kode internal `efn_pos_kds` (E-26) dan `efn_online_pos_counter` (pemetaan outlet → gudang,
  E-45). Tidak ada kode OCA/Cybrosys yang dirujuk/disalin.

## Perilaku
| Hal | Ketentuan |
|---|---|
| Skema tiket | `efn.kds.ticket.order_id` menjadi opsional; field baru `efn_online_order_id`. CHECK tepat satu sumber; `UNIQUE (efn_online_order_id, station_id, sequence_no)` (unik POS bawaan tetap). Migrasi = otomatis saat install (Odoo melepas `NOT NULL`); `uninstall_hook` menghapus tiket online sebelum `NOT NULL` dipasang kembali. |
| Pemicu | Pesanan `confirmed` (setelah bayar: QRIS `mark_paid` atau pelunasan kasir E-45) lewat hook `efn.online.order._efn_after_confirmed`. Sekali per pesanan (idempoten). |
| Stasiun | Stasiun mode `kds`/`kds_printer` company pesanan yang terhubung ke POS outlet: `efn.online.outlet.kds_config_id` (override, opsional) atau semua `pos.config` dengan `picking_type_id.warehouse_id` = gudang outlet. Rute baris = `pos.order._efn_kds_route` (kategori POS produk + induk). |
| Baris | Baris SO produk barang (bukan jasa/combo) qty > 0; reward produk gratis ikut, reward diskon tidak. Modifier = nama nilai atribut `no_variant`. Catatan pelanggan tidak ada. |
| Label | `tracking_number` = nomor SO (12 karakter terakhir), `preset_name` "Online - ambil". **Tanpa** kode ambil, kode bayar, nama/HP/alamat. Payload layar KDS tidak berubah (diuji). |
| Mulai dimasak | Tiket pertama punya `started_at` → `efn.online.order.kitchen_started_at` + event opsional `preparing` (sekali). |
| Siap | Semua tiket aktif (bukan batal) `ready`/`served` → `ready` dengan `ready_source = "kds"` → event `ready` ke BFF. Tombol "Siap diambil" staf tetap ada (`ready_source = "manual"`). |
| Batal / refund | Batal pesanan atau refund penuh → tiket aktif `cancelled`. Refund per baris saat masih `confirmed` → tiket pengurangan (qty negatif) di stasiun yang pernah menerima produk itu. |
| Gagal | Galat pembuatan tiket tidak membatalkan pembayaran: savepoint, log alert `EFN_KDS_TICKET_FAILED` (logger `odoo.addons.efn_pos_kds.alert`, JSON id saja) + aktivitas staf gudang. |
| Akses | `efn_online_order_id` hanya terbaca grup yang punya ACL baca pesanan online (Sales/Inventory/Billing); akun perangkat dapur tidak (SEC-74). |
| Pajak (E-29a-6) | Kanal `require_outlet_fiscal_position`: FP outlet tidak boleh FP preset POS `service_at='table'` (cegah SC + PB1 dine-in di pesanan online). |

## Uji manual
1. Konfigurasi POS outlet (gudang = gudang outlet kanal PB/BCF) + stasiun KDS Dapur/Bar per kategori POS.
2. Super-App: pesan 1 menu dapur + 1 minuman bermodifier → bayar (QRIS mock atau kode bayar di kasir).
3. Layar KDS: tiket muncul di masing-masing stasiun dengan label nomor SO, tanpa nama pelanggan.
4. Bump bar *Dimasak* → BFF menerima `preparing`; bump semua *Siap* → pesanan "Siap diambil", pelanggan dapat WA.
5. Batalkan pesanan lain yang sudah dibayar dari CS → tiketnya hilang dari layar (status batal).

## Test (`tests/test_online_kds_bridge.py`, tag `efn`)
Tiket setelah bayar per stasiun + tanpa PII + payload tetap + idempoten; alur `preparing` → `ready` (`ready_source`
kds, recall setelah siap tidak berefek); siap manual lalu bump tidak menggandakan event; batal → tiket batal; refund
per baris → tiket pengurangan; gudang lain tanpa stasiun + override `kds_config_id`; kegagalan tiket tidak memblok
bayar (alert tanpa ref); CHECK satu sumber + unik per sumber; perangkat tidak bisa membaca pesanan lewat tiket; FP
dine-in ditolak (di-skip bila `pos_self_order` tidak ada); `uninstall_hook`.

## Batasan / belum
- Test **belum dijalankan** oleh penulis (tanpa shell); lead menjalankan di container Odoo 19.
- Tidak ada event `served` ke BFF (tidak ada di kontrak). Tidak ada auto-batal bila tidak diambil (Q-ON13, B-9).
- Pesanan yang jatuh `review` setelah `confirmed` tidak membatalkan tiket (dapur tetap jalan; CS memutuskan).
- Notifikasi kasir POS (`EFN_KDS_READY`) hanya untuk tiket POS; pesanan online tidak muncul di layar kasir.
- Pemetaan beberapa POS di gudang yang sama: tiket dikirim ke stasiun semua POS itu (override `kds_config_id` bila
  perlu satu POS). Exclusion atribut (`exclude_for`) tidak divalidasi.
- `pos.preset` dibuat belakangan dengan FP outlet yang sudah dipakai tidak memicu constraint E-29a-6 (hanya saat outlet
  diubah).
