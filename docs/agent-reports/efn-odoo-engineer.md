# Laporan efn-odoo-engineer — PoC Fase 0 "Kitting & OTP"

Tanggal: 2026-10-05. **Status: kode ditulis, BELUM PERNAH di-install atau dijalankan** (mesin penulis tidak punya Odoo/Python/Docker). Validasi pertama akan terjadi di CI (`odoo -i efn_core,efn_kitting,efn_pos_otp_gate --test-tags /efn_core,/efn_kitting,/efn_pos_otp_gate`).

## 1. Modul & file

### efn_core (direvisi, 19.0.1.1.0)
- `__manifest__.py` — lisensi AGPL-3 → LGPL-3; depends dipangkas ke `base`, `base_setup`; tambah view.
- `models/res_company.py` — `efn_brand_code` jadi Selection `EFN/RTL/FNB/FARM` + `models.Constraint UNIQUE`; cek konsistensi dengan `efn_business_type`.
- `models/efn_bff_client.py` — **baru**: AbstractModel `efn.bff.client`, `EfnBffError`, `BffResponse`, `mask_phone`, `normalize_member_ref`.
- `data/ir_config_parameter.xml` — tambah `efn.pos.otp_timeout_seconds = 5`.
- `views/res_company_views.xml` — tab "EFN" di form company.
- `tests/test_bff_client.py`, `tests/test_res_company.py`, `README.md`.

### efn_pos_otp_gate (baru)
- `models/loyalty_program.py` — `efn_requires_otp` + dimuat ke POS.
- `models/pos_config.py` — RPC `efn_otp_request`, `efn_otp_verify`; flag compute `efn_otp_required_for_promo`, `efn_promo_disabled_when_offline` untuk frontend.
- `models/efn_pos_otp_challenge.py` — model baru (company_id, `_check_company_auto`, record rule, ACL, autovacuum retensi 30 hari).
- `models/pos_order.py` — override `_process_saved_order`: strip (draft), tolak (berbayar tanpa OTP), consume / tandai `review` (berbayar dengan OTP).
- `static/src/app/models/pos_order_patch.js`, `utils/efn_otp_flow.js`, `screens/product_screen/control_buttons_patch.js`, `screens/payment_screen/payment_screen_patch.js`.
- `security/ir.model.access.csv`, `security/efn_pos_otp_security.xml`, `views/loyalty_program_views.xml`, `views/pos_order_views.xml` (filter + menu "Review OTP Promo").
- `tests/test_otp_gate.py` (16 test), `README.md`.

### efn_kitting (baru)
- `models/product_template.py` (`efn_is_kitting_package` + constraint), `models/mrp_bom.py` (`efn_kitting_role` + constraint anti-Phantom).
- `data/uom_data.xml` (UoM Porsi), `demo/efn_kitting_demo.xml`, `views/*.xml`, `tests/test_kitting.py` (6 test), `README.md`.

### Lain
- `services/odoo/oca/README.md` — dikoreksi sesuai audit `docs/06`: `stock_picking_batch` = CE; OCA/l10n-indonesia 19.0 kosong; `purchase_sale_stock_inter_company` tidak ada di 19.0; tambah `report_xlsx` & `date_range` sebagai dependensi `mis_builder`. Tidak ada modul EFN yang bergantung pada modul yang tidak tersedia (`pos_preparation_display` tidak dipakai).

## 2. Keputusan desain
1. **Token & challenge tidak pernah ke browser.** `challenge_id` dan `discount_token` disimpan di `efn.pos.otp.challenge` (field `groups=base.group_system`); browser hanya menerima `ok` + nomor ter-mask. Server mencari token berdasarkan (`config_id`, `order uuid`) saat sync → token tidak bisa dipalsukan/dipindah ke order lain dari browser.
2. **RPC lewat ORM (`pos.data.call("pos.config", ...)`)**, bukan controller publik; dicek grup `point_of_sale.group_pos_user` + sesi POS terbuka.
3. **`order_ref` = `pos.order.uuid`**, bukan `pos_reference`: di 19.0 `pos_reference` dibuat server (`_complete_values_from_session`) saat sync pertama, jadi belum ada ketika kasir meminta OTP. → **Kontrak `docs/04 §1` perlu diubah** (milik architect/BFF).
4. **Hook server = `_process_saved_order(draft)`** (diverifikasi di source 19.0: `sync_from_ui → _process_order → _process_saved_order`). Consume dipanggil setelah super (picking/invoice) agar token tidak terbakar bila langkah standar gagal.
5. **Kegagalan consume → order ditandai `review`, tidak ditolak** — mengikuti ADR-017 #2 ("ditandai untuk review supervisor, tidak dibatalkan otomatis"). Ini **menyimpang dari brief** ("reward dibuang") karena pada order berbayar pembayaran sudah dihitung dari total setelah diskon; membuang reward membuat order tidak lunas, dan menolak sync membuat order terjebak di antrean POS. Order berbayar yang **tidak punya OTP terverifikasi sama sekali** tetap ditolak (UserError). Order **draft** tanpa OTP: reward dihapus.
6. Saklar: requirement efektif = `loyalty.program.efn_requires_otp` **AND** `efn.pos.otp_required_for_promo`.
7. Offline (ADR-017 opsi a): reward ber-OTP disembunyikan dari tombol Reward, alur OTP menolak, validasi pembayaran diblokir bila belum terverifikasi atau offline + `promo_disabled_when_offline`.
8. Guard frontend di `PosOrder._applyReward` (level model) menutup semua jalur penerapan reward (tombol, auto-apply, kode promo); alur OTP async di `ControlButtons._applyReward`.
9. `efn_core` dibuat ramping; `efn_kitting` tidak bergantung `point_of_sale` (penjualan kit di POS ditangani modul CE `pos_mrp`).
10. MO otomatis dari replenishment **tidak dikodekan** — cukup route Manufacture + reorder rule CE (didokumentasikan di README efn_kitting).

## 3. API Odoo 19.0 yang DIVERIFIKASI via source GitHub 19.0
- `pos.order.sync_from_ui`, `_process_order(order, existing_order)`, `_process_saved_order(draft)`, `_get_open_order` (berdasarkan `uuid`), `_complete_values_from_session` (pembuatan `pos_reference`).
- `pos.load.mixin._load_pos_data_fields(config)`; daftar kosong = semua field (juga di `_load_pos_data_relations`).
- `loyalty.program._load_pos_data_fields` (pos_loyalty) daftar eksplisit → field baru harus ditambahkan.
- `pos.order.line`: `is_reward_line`, `reward_id`, `coupon_id`, `refunded_orderline_id`.
- Frontend: `ControlButtons._applyReward(reward, coupon_id, potentialQty)`, `getPotentialRewards()`, `PosOrder._applyReward(reward, coupon_id, args)` (string = error), `PosStore.config`, `getOrder()`, `pos.data.call(model, method, args)`, `pos.data.network.offline`, `PaymentScreen.validateOrder`, `TextInputPopup`/`NumberPopup` props, `makeAwaitable`, bundle `point_of_sale._assets_pos`.
- `uom.uom` 19.0: tanpa kategori; `relative_factor` + `relative_uom_id`; xmlid `uom.product_uom_kgm`, `uom.product_uom_unit`.
- `mrp.bom.type` (`normal`/`phantom`), field BoM/BoM line; `stock.move` 19.0 tanpa field `name`; `models.Constraint`; `is_storable` di produk.
- `TestPoSCommon` (`create_ui_order_data`, `open_new_session`, `create_product`, `basic_config`, `categ_basic`).
- View xmlid: `pos_loyalty.loyalty_program_view_form_inherit_pos_loyalty` (field `pos_config_ids`), `point_of_sale.view_pos_order_filter` (filter `invoiced`), `point_of_sale.menu_point_of_sale`, `mrp.mrp_bom_form_view`, `mrp.mrp_bom_tree_view`, `product.product_template_form_view` (field `categ_id`).

## 4. Asumsi yang BELUM terverifikasi
1. `base.view_company_form` punya `<notebook>` (target xpath tab EFN).
2. `read([])` / `_load_pos_data_read` untuk `pos.config` memuat field compute non-stored (`efn_otp_required_for_promo`) ke frontend; dan field baru `pos.order` ikut diserialisasi frontend saat sync.
3. Frontend: `order.config_id`, `order.lines`, `order.priceIncl`, `order.uuid`, `reward.program_id.efn_requires_otp` tersedia sebagai record/properti; assignment langsung `order.efn_otp_verified = true` ditandai dirty dan ikut sync.
4. `makeAwaitable` mengembalikan `undefined` saat popup ditutup; `NumberPopup.isValid` menonaktifkan tombol konfirmasi.
5. `assertRaises` Odoo membungkus savepoint sehingga rollback `sync_from_ui` dapat diverifikasi (`search` uuid kosong).
6. `TestPoSCommon` dengan program loyalty `promotion` + baris reward manual (`reward.discount_line_product_id`, `price_unit` negatif tanpa pajak) lolos `action_pos_order_paid` & pembuatan picking.
7. `patch.object(self.env.registry["efn.bff.client"], "post", ...)` mengganti method untuk semua recordset di test.
8. `@api.autovacuum` masih tersedia di 19.0.
9. MRP: `button_mark_done()` setelah `action_assign()` dengan stok penuh langsung `done` tanpa wizard; explode Kit terjadi pada `picking.action_confirm()`; `stock.quant._get_available_quantity/_update_available_quantity` tidak berubah signature.
10. Produk storable dengan BoM Kit boleh dibuat (dipakai di test constraint flag paket).
11. Demo: `base.IDR` boleh dipakai walau nonaktif; xmlid otomatis `<id>_product_template` untuk `product.product` dari XML; warehouse company demo dibuat otomatis oleh `stock`.
12. `menu_point_of_sale` adalah menu "Orders" (parent menu Review OTP Promo).

## 5. Tindak lanjut untuk agent/tim lain
- **Architect/BFF:** ubah kontrak `order_ref` → `pos.order.uuid` (string opaque ≤ 64). Pertimbangkan consume idempotent untuk (`jti`, `order_ref`) sama (mitigasi commit Odoo gagal setelah consume sukses).
- **DevSecOps:** compose Odoo perlu secret `odoo_bff_api_key` + env `EFN_BFF_API_KEY_FILE=/run/secrets/odoo_bff_api_key`; `fetch-oca.sh` perlu OCA/reporting-engine & OCA/server-ux.
- **QA:** tour POS (HttpCase) untuk alur OTP frontend belum ada; uji `pos_mrp` explode menu Kit dari sesi POS.

---

## Gelombang 3 — Enkripsi in transit, SEC-07, SEC-12, SEC-15 (2026-10-05)

Instruksi user: **enkripsi di setiap transaksi data**. Kontrak acuan: `docs/04` v2 §3.1, §6, §7. Status tetap: **belum pernah dijalankan** (tidak ada Odoo/Python/Docker lokal).

### File
| File | Perubahan |
|---|---|
| `services/odoo/render_odoo_conf.py` (**baru**) | Render `odoo.conf` dengan `configparser.RawConfigParser` (parser yang sama dengan Odoo 19): `db_sslmode=verify-full`, kredensial DB dari `PASSWORD_FILE`, `admin_passwd` hash pbkdf2_sha512, `http_interface` (default 127.0.0.1), `proxy_mode=True`; validasi nilai; tulis atomik 0600; tunggu Postgres via psycopg2 dengan sslmode yang sama. |
| `services/odoo/entrypoint-efn.sh` | Tidak lagi mendelegasikan ke `/entrypoint.sh` resmi; set `EFN_CA_FILE`/`PGSSLROOTCERT`; tolak `ODOO_DEV_MODE` di produksi; `exec odoo`. |
| `services/odoo/odoo.conf.tpl` | Hanya opsi non-rahasia; placeholder `sed` dihapus; `email_from = False` dihapus (Odoo 19 men-skip `False` untuk opsi non-boolean). |
| `services/odoo/Dockerfile` | Template/skrip milik root (`COPY --chmod`), hapus `odoo.conf` bawaan image, healthcheck ke `127.0.0.1`. |
| `efn_core/models/efn_bff_client.py` | HTTPS wajib, `verify=<EFN_CA_FILE>`, `bff_insecure_url`/`bff_ca_missing`/`bff_tls_error`, env `EFN_BFF_URL`, `plaintext_allowed()`, `parse_member_ref()`, `partner_member_ref()`. |
| `efn_core/data/ir_config_parameter.xml`, `migrations/19.0.1.2.0/post-migrate.py`, `__manifest__.py` (19.0.1.2.0) | Default `efn.bff_url = https://api-gateway:3000` + migrasi nilai lama. |
| `efn_pos_otp_gate/models/pos_config.py` | `efn_otp_request(order_ref, amount, partner_id)` — nomor dari `res.partner`, bukan input kasir; error `partner_missing`/`no_phone`/`foreign_phone`/`invalid_phone`; map BFF `400 invalid_phone`. |
| `efn_pos_otp_gate/models/efn_pos_otp_challenge.py` | Field `partner_id`, `amount` (audit). |
| `efn_pos_otp_gate/models/pos_order.py` | SEC-12 deteksi server (reward program / produk diskon program ber-OTP, termasuk arsip); consume membawa `member_ref` partner final; `member_mismatch` → review; partner final tanpa HP → review `member_missing:<kode>` tanpa consume. |
| `efn_pos_otp_gate/static/src/app/utils/efn_otp_flow.js`, `models/pos_order_patch.js` | Alur OTP meminta pilih pelanggan (`pos.selectPartner`) — popup nomor HP dihapus; ganti pelanggan mereset penanda UI. |
| `efn_pos_otp_gate/__manifest__.py` | 19.0.1.1.0 (field baru). |
| Test: `efn_core/tests/test_bff_client.py` (18), `efn_pos_otp_gate/tests/test_otp_gate.py` (29) | Lihat README masing-masing. |
| README `efn_core`, `efn_pos_otp_gate` | Tabel env container, TLS, SEC-07/12, batasan. |

### Perubahan perilaku
1. **Odoo gagal start** bila: `PASSWORD_FILE`/`ODOO_ADMIN_PASSWORD_FILE` tidak ada/kosong, master password < 16 karakter, CA (`PGSSLROOTCERT`) tidak terbaca/bukan PEM, env `PASSWORD`/`PGPASSWORD`/`ODOO_ADMIN_PASSWORD` diisi, user DB `postgres`, `PGSSLMODE` bertentangan, `http_interface` non-loopback tanpa mode plaintext dev, `EFN_ALLOW_PLAINTEXT=true` dengan `APP_ENV` produksi/kosong, nilai secret berisi baris baru/karakter kontrol/spasi tepi, atau Postgres tidak terjangkau dengan TLS dalam 30 s.
2. Password DB tidak lagi muncul di argumen proses (`ps`).
3. Default user DB `efn_odoo` (bukan `odoo`/`efn`).
4. Odoo HTTP & websocket hanya listen di `127.0.0.1` secara default → **wajib sidecar ghostunnel** (`network_mode: service:odoo`) agar Traefik/POS bisa menjangkau.
5. BFF via `http://` → promo ber-OTP gagal (`bff_unavailable` ke kasir) kecuali dev; sertifikat BFF tidak cocok CA → `bff_tls_error` (consume → order `review`).
6. Kasir tidak bisa lagi mengetik nomor HP; order harus punya pelanggan ber-HP Indonesia.
7. Baris diskon program ber-OTP dengan flag reward dikosongkan klien tetap di-gate (ditolak / dihapus / di-consume).

### Asumsi Odoo 19.0
Diverifikasi via source GitHub 19.0: `db_sslmode` (choices, `env_name='PGSSLMODE'`), `http_interface` (default `0.0.0.0`), `proxy_mode`, `gevent_port`, `ODOO_RC`, `RawConfigParser`, presedensi **env > file** (ChainMap), `PGPASSWORD`/`PGUSER`/`PGHOST` sebagai env_name, nilai `'False'` di-skip untuk opsi non-boolean, `crypt_context` = pbkdf2_sha512 600 000 rounds; entrypoint resmi 19.0 (`--db_password` di CLI, fallback `odoo`); `res.partner` hanya punya `phone` (tanpa `mobile`); `PosOrder.getPartner()/setPartner()`, `PosStore.selectPartner(order)`, `priceIncl` (mixin `PosOrderAccounting`); `create_ui_order_data(..., customer=...)`.

**Belum terverifikasi:**
1. `passlib` dan `psycopg2` importable oleh `python3` sistem di image `odoo:19.0` (keduanya dependensi Odoo; ada fallback peringatan).
2. Odoo menerima hash pbkdf2 yang dibuat passlib di luar Odoo untuk `admin_passwd` (format standar `$pbkdf2-sha512$…`).
3. `RecordSet.check_access("read")` (API 18+) di 19.0 dan perilakunya untuk partner lintas company.
4. libpq memakai `PGSSLROOTCERT` dari env saat Odoo hanya mengoper `sslmode` di DSN.
5. User `efn_odoo` punya hak `CONNECT` ke DB `postgres` (dipakai tunggu-DB; atur `ODOO_DB_WAIT_DBNAME` bila tidak).
6. Sync POS menerima baris dengan produk diskon tanpa `reward_id` dan `is_reward_line=False` tanpa error pos_loyalty (dasar test SEC-12).
7. `pos.selectPartner(order)` mengembalikan setelah dialog ditutup dan partner sudah terpasang; properti non-field `order.efnOtpPartnerId` bertahan selama sesi.
8. `COPY --chmod` butuh BuildKit (default Docker ≥ 23).
9. `loyalty.program.action_archive()` tersedia (dipakai test).

### Tindak lanjut
- **DevSecOps:** env `HOST=postgres` (cocok SAN), `USER=efn_odoo`, `PASSWORD_FILE=/run/secrets/pg_password_odoo`, secret `efn_ca_crt`, `APP_ENV=production` di prod; sidecar ghostunnel `--target 127.0.0.1:8069` (+ `8072` untuk websocket); hapus `EFN_BFF_URL: http://…` di compose atau ganti `https://api-gateway:3000` (env mengalahkan parameter; `http` akan ditolak); healthcheck container tetap lewat loopback. Master password ≥ 16 karakter di `gen-secrets.sh`.
- **BFF:** implementasikan `member_ref` di consume + `409 member_mismatch` (Odoo sudah mengirim); pertimbangkan klaim `amount` di token (SEC-07 sisa).
- **efn-odoo-functional:** batasi edit `res.partner.phone` oleh kasir, "Restrict price modification" di `pos.config`, laporan harian baris diskon tanpa `reward_id`.
- **QA:** start container dengan secret hilang / CA hilang / `EFN_ALLOW_PLAINTEXT` di produksi → harus exit 1; tour POS pilih-pelanggan → OTP.

---

## Gelombang 3b — Tindak lanjut fit-gap (`docs/08`, laporan efn-odoo-functional E-1..E-9)

Status: **belum dijalankan** (sama seperti sebelumnya).

### Perubahan
| # | Modul / file | Perubahan |
|---|---|---|
| 1 (E-7, blocker) | `efn_kitting/demo/efn_kitting_demo.xml` | Record `company_fnb_ck` dan penambahan company ke admin **dihapus**. Setiap `company_id` demo = `obj().search([('efn_brand_code','=','FNB')], limit=1).id or ref('base.main_company')` (atribut `model="res.company"`). Tidak ada dependensi ke `efn_setup_company`. |
| 2 (E-4) | `efn_kitting/models/mrp_bom.py`, `views/mrp_bom_views.xml` (**dihapus**), `__manifest__.py` (19.0.1.1.0), `tests/test_kitting.py` | Field `efn_kitting_role` dihapus; constraint anti-Phantom tetap. Tanpa skrip migrasi: Odoo menghapus `ir.model.fields` + kolom dan view yang tidak lagi ada saat upgrade. Test: field tidak ada + ubah BoM paket ke phantom ditolak. |
| 3 | `efn_kitting/README.md` | Route Manufacture 19.0 = route gudang (`product_selectable=False`); langkah "pasang route di produk" dihapus. |
| 4 (E-8, E-9) | `efn_core/models/res_company.py`, view, `migrations/19.0.1.2.0/post-migrate.py`, `tests/test_res_company.py` | `efn_business_type` → compute stored readonly dari `efn_brand_code` (constraint konsistensi + onchange dihapus; migrasi menyinkronkan baris lama). `efn_is_merchant_of_record` maksimal satu company (`@api.constrains`, sudo + `active_test=False`). |
| 5 (E-1..E-3) | `efn_pos_otp_gate/models/loyalty_program.py`, view, README, test, `__manifest__.py` (19.0.1.2.0) | `efn_requires_otp` hanya untuk `promotion`/`buy_x_get_y`/`promo_code`/`coupons`. Program ber-OTP di POS wajib `pos_config_ids` eksplisit dan tidak boleh ke config `self_ordering_mode='kiosk'` (dicek hanya bila `pos_self_order` terpasang). Gate server generik dipertahankan sebagai defense in depth — memang tidak pernah ada logika khusus kiosk. README: parameter global didokumentasikan sebagai kill switch beserta peringatan fraud + audit. |

**Pilihan `efn_business_type` (compute stored vs hapus):** compute stored readonly dipilih karena paling sedikit risiko. Field yang sama tetap ada untuk pembaca yang sudah ada (view, data `efn_setup_company` yang masih mengisi nilainya — nilainya konsisten sehingga diterima ORM, BFF/ETL bila membaca), tetapi field itu tidak mungkin lagi menyimpang dari kode. Menghapus field akan memecahkan data XML `efn_setup_company`, yang bukan milik saya.

### Dampak ke modul lain (perlu tindakan pemilik)
- **`efn_setup_mrp/demo/mrp_demo.xml` (milik efn-odoo-functional) masih `ref="efn_kitting.company_fnb_ck"` di 2 orderpoint → demo `efn_setup_mrp` akan GAGAL install** sampai diganti ekspresi yang sama (`model="res.company" eval="obj().search([('efn_brand_code','=','FNB')], limit=1).id or ref('base.main_company')"`) atau `ref="efn_setup_company.company_fnb"` (orderpoint mengikuti company produk).
- `efn_setup_company` boleh berhenti mengisi `efn_business_type` (diabaikan/ditimpa compute). Bila ada lebih dari satu company dengan `efn_is_merchant_of_record` di demo, install akan gagal — saat ini hanya Holding.
- Program loyalty ber-OTP di data/demo pihak lain wajib mengisi `pos_config_ids`.
- DB demo lama yang sudah memasang `efn_kitting` 19.0.1.0.0: saat upgrade, Odoo akan mencoba menghapus company `efn_kitting.company_fnb_ck` yang sudah tidak ada di data. Penghapusan kemungkinan gagal (FK produk/BoM) dan hanya di-log. Disarankan membuat ulang DB demo.

### Asumsi belum terverifikasi (3b)
1. Konteks eval `<field model=... eval=...>` di `convert.py` 19.0 menyediakan `obj` = `env[model].browse` dan `ref` (diverifikasi untuk `_get_eval_context`; perilaku `obj()` tanpa argumen = recordset kosong belum diuji), dan `safe_eval` mengizinkan `.search(...)`/`.id`.
2. ORM 19.0 menerima nilai eksplisit untuk field compute stored tanpa inverse di `create` (data `efn_setup_company`).
3. Upgrade menghapus kolom `mrp_bom.efn_kitting_role` dan view lama tanpa error.
4. `pos.config.copy()` + `self_ordering_mode='kiosk'` bisa dibuat di test (dilewati bila `pos_self_order` tidak terpasang).
5. Selection `loyalty.program.program_type` 19.0 memuat `buy_x_get_y`, `promo_code`, `coupons`, `loyalty` (sesuai docs/08).

---

## Gelombang 5 — Payment gateway POS, erasure UU PDP, consume `amount_total` (2026-10-06)

Kontrak: `docs/04` v3 §1, §8.3, §8.4, §8.8. Spesifikasi gap: `docs/08` §4.1 (E-15), §4.2 (E-16), §4.3 (E-17 — dipatuhi: tidak ada logika/field pajak). Status tetap: **belum pernah di-install/dijalankan**.

### File
| Modul | File | Perubahan |
|---|---|---|
| `efn_core` 19.0.1.3.0 | `models/efn_bff_client.py` | **`get(path, params, log_context)`**; `post`/`get` berbagi `_efn_request` (HTTPS + CA, service key, timeout, `allow_redirects=False`, tanpa log payload/params). Path dengan `?`/`#`/`..`/`//`/`\`/spasi ditolak (`ValueError`). `BffResponse.body` = JSON mentah (list untuk endpoint array). |
| | `tests/test_bff_client.py`, `README.md` | 5 test GET; test yang memang memicu log ERROR dibungkus `mute_logger` (langkah CI menggagalkan job bila ada baris ERROR). |
| `efn_pos_otp_gate` 19.0.1.3.0 | `models/pos_order.py`, test, README | Consume membawa `amount_total` (int Rupiah, total final server); `409 amount_mismatch` (reason/objek error) → `review`. +2 test, test sukses memeriksa payload. |
| `efn_pos_payment_gateway` 19.0.1.0.0 (**baru**) | `models/pos_payment_method.py` | Terminal `efn_gateway`; constraint (Terminal, brand code, jurnal bank, tanpa split, mata uang); RPC `efn_gateway_create/status/cancel` (pola `pos_adyen`: `AccessDenied` non-POS user, metode milik config, sesi terbuka). |
| | `models/efn_pos_payment_intent.py` | Model intent (multi-company, `mail.activity.mixin`), sinkron status dari BFF (`paid` final, nominal beda → `failed`), cron rekonsiliasi harian (pending kedaluwarsa → refresh/cancel; paid yatim → WARNING + aktivitas akuntan, sekali). |
| | `models/pos_order.py` | Override `_process_payment_lines` → validasi intent saat sync (non-draft); gagal → `UserError`. |
| | `static/src/app/utils/payment/payment_efn_gateway.js` | `PaymentInterface` + `register_payment_method("efn_gateway")`: offline → false, polling 2 s, `QRPopup` kasir + `order.qrPaymentData`, cancel, tanpa reversal. |
| | security (ACL read POS user/manager/akuntan, rule company), data (param batas nominal, cron), view + menu, `tests/test_payment_gateway.py` (18), README | |
| `efn_privacy` 19.0.1.0.0 (**baru**) | `models/member_hash.py` | Rumus §1, baca `EFN_MEMBER_HASH_PEPPER_FILE` (validasi 64 hex, cache path+mtime+size). Vektor uji BFF diverifikasi independen (SHA-256 PowerShell) = `8930b421…6653`. |
| | `models/res_partner.py` | `efn_member_hash` (compute stored indexed, `groups=base.group_system`, tidak dimuat POS), `efn_erasure_request_id`, `_efn_recompute_member_hash` (admin). |
| | `models/efn_privacy_erasure.py`, `data/ir_cron.xml` (15 menit), `data/ir_actions_server.xml` | Pull → anonimisasi (partner aktif/arsip lintas company, `pos.order.email/mobile`, chatter+tracking, auditlog, `privacy.log` placeholder) → commit → complete (jumlah dari penanda request → idempoten). |
| | `tests/test_member_hash.py` (8), `tests/test_erasure.py` (8), README | |
| CI | `.github/workflows/ci.yml` | `EFN_TEST_MODULES` + `efn_pos_payment_gateway efn_privacy`. `privacy_lookup` **tidak** didaftarkan karena `pick()` hanya menerima modul di `services/odoo/addons` (akan dilewati) — modul itu ter-install otomatis lewat `depends`. |

Dockerfile/entrypoint **tidak diubah**: pepper dibaca langsung dari file yang ditunjuk env (default `/run/secrets/member_hash_pepper`); devsecops cukup me-mount secret + env.

### Keputusan / penyimpangan dari spesifikasi
1. **Pepper hilang saat compute → WARNING (bukan ERROR)**, sekali per proses. Alasan: install di CI/DB baru tanpa secret akan menghasilkan baris ERROR dan menggagalkan job; partner tetap tersimpan. Cron erasure tetap **ERROR + gagal keras** tanpa memanggil BFF.
2. **Idempotency key percobaan ulang**: kontrak `pos:<order_uuid>:<payment_uuid>`; setelah intent dibatalkan/kedaluwarsa, baris yang sama memakai `…:<n>` (kalau tidak, BFF yang idempoten akan mengembalikan intent lama yang sudah final). Intent `pending`/`paid` untuk baris sama dengan nominal sama dikembalikan tanpa memanggil BFF; nominal beda → `pending_exists` (kasir batalkan dulu).
3. **Mata uang**: payload `currency` = nama mata uang company yang wajib sama dengan parameter `efn.pos.gateway_currency` (default `IDR`). Parameter ada karena DB uji `TestPoSCommon` tidak ber-IDR.
4. **QR kasir** memakai `QRPopup` standar (tidak dikustom) dengan gambar PNG dari `ir.actions.report.barcode`; tombol konfirmasi hanya menutup popup (lunas hanya dari server).
5. **Erasure**: seluruh chatter partner dihapus (superset dari "pesan yang memuat nilai lama"); partner milik `res.users` dianonimkan tetapi tidak diarsipkan (constraint base). Commit sebelum `complete` (dilewati saat test) agar BFF tidak pernah menganggap selesai sementara anonimisasi masih bisa rollback.
6. **Sync ditolak** (bukan `review`) untuk pembayaran QRIS tak terverifikasi — sesuai spesifikasi; konsekuensi: satu batch `sync_from_ui` ikut ditolak.
7. Test "offline" di server = order ber-metode `efn_gateway` tanpa intent / dengan `transaction_id` palsu ditolak; guard offline JS sendiri **tidak punya test otomatis**.

### API 19.0 diverifikasi via source GitHub 19.0 (Gelombang 5)
`pos_adyen`: `_get_payment_terminal_selection`, `proxy_adyen_request` (cek `env.su`/`group_pos_user` → `AccessDenied`, `ensure_one`), JS (`PaymentInterface` dari `@point_of_sale/app/utils/payment/payment_interface`, `register_payment_method` dari `@point_of_sale/app/services/pos_store`, `pos.data.silentCall`, `line.setPaymentStatus("waitingCard")`, `line.transaction_id`, polling `setInterval`, `getPendingPaymentLine`); `PaymentInterface` 19.0 (`setup`, `fastPayments`, `sendPaymentRequest/Cancel/Reversal`, `close`, `supports_reversals`); `pos.payment.method` (`payment_method_type`, `use_payment_terminal`, `split_transactions`, `_get_payment_terminal_selection` → `[]`, `_load_pos_data_fields`); `pos.order._process_order` (urutan create/write → `_process_payment_lines(order, pos_order, pos_session, draft)` → `_process_saved_order`), `pos.order.email/mobile` (compute stored, readonly=False, depends `partner_id`); `QRPopup` props (`line`, `order`, `qrCode` + `ConfirmationDialog`); `res.partner._load_pos_data_fields` (daftar eksplisit, tanpa hash); `privacy.log` fields & anonimisasi; `mail.tracking.value.mail_message_id`; OCA `auditlog.log` (18.0: `model_model`, `res_id`, lines `ondelete=cascade`).

### Asumsi belum terverifikasi (Gelombang 5)
1. `order.qrPaymentData` dibaca layar pelanggan 19.0 — **`customer_display.xml` 19.0 tidak merujuknya**; penulisnya (`pos_store.showQR`) tidak terbaca utuh. QR tetap tampil di popup kasir.
2. `ConfirmationDialog` 19.0 menerima prop `dismiss`; `dialog.add` mengembalikan fungsi tutup; `this.env.services.notification` tersedia di `PaymentInterface` (pos.env).
3. Payment screen 19.0: `sendPaymentRequest` false → baris `retry`, true → `done`; `sendPaymentCancel` true → `retry`.
4. `ir.actions.report.barcode("QR", value, width=, height=)` mengembalikan PNG bytes.
5. `pos.payment.uuid` ada (dicek kondisional); `TestPoSCommon.create_ui_order_data(..., payments=[(method, amount)], uuid=...)` dan atribut `company_data['default_journal_bank']`, `pos_receivable_bank`, `outstanding_bank`, `cash_pm1`.
6. `@api.depends(<callable>)`, `env.add_to_compute` + `flush_recordset`, `models.Constraint`, `res.users.all_group_ids` (fallback `groups_id`), `activity_schedule`, tag `<chatter/>`, atribut `groups` di `<menuitem>`.
7. `threading.current_thread().testing` / `registry.in_test_mode()` di 19.0 (guard commit; test juga memakai context `efn_privacy_no_commit`).
8. Tulis `email`/`mobile` pada `pos.order` berstatus paid tidak diblokir ORM POS; arsip partner yang punya `pos.order` diizinkan.
9. `auditlog.log` 19.0 punya `model_model` (fallback `model_id.model`) dan boleh di-unlink oleh sudo.
10. `mute_logger` membisukan log ERROR yang diharapkan sehingga langkah CI "Periksa log" tidak gagal (test lama efn_core sebelumnya memancarkan ERROR — kemungkinan sudah membuat CI merah).

### Tindak lanjut
- **DevSecOps:** secret `member_hash_pepper` (= pepper_hex turunan `otp_pepper`, dibuat deploy) + env `EFN_MEMBER_HASH_PEPPER_FILE=/run/secrets/member_hash_pepper` di service Odoo; setelah pertama kali dipasang, admin menjalankan server action "EFN: hitung ulang member hash".
- **BFF:** `GET /v1/pos/payments/:id` sebaiknya selalu mengembalikan `payment_id` & `amount` (Odoo memverifikasi keduanya); daftar erasure boleh array atau `{items}`; consume menerima `amount_total` int Rupiah.
- **efn-odoo-functional/DPO:** kontak anak, `bank_ids`, lampiran partner, retensi partner POS-only; exclude `efn_member_hash` dari aturan auditlog `res.partner` (opsional).
- **QA:** tour/Hoot untuk `PaymentEfnGateway` (offline, polling, cancel, QR layar pelanggan).
## Gelombang 7 — runtime (Odoo 19.0-20260926 nyata, 2026-10-06)

Test addon pertama kali dijalankan di image `efn-odoo:ci` + PostgreSQL 16 (harness lead `odoo-test.sh`, `--test-tags efn`, termasuk `efn_setup_audit` dengan OCA auditlog aktif). Awal: `1 failed, 5 error(s) of 41 tests` (3 kelas gagal di setUpClass sehingga ~61 test tidak jalan). Akhir: **`0 failed, 0 error(s) of 102 tests`**, 0 baris `ERROR`/`CRITICAL`.

| Modul | Test berjalan | Lolos |
|---|---|---|
| efn_core | 30 | 30 |
| efn_kitting | 9 | 9 |
| efn_pos_otp_gate | 37 | 37 |
| efn_pos_payment_gateway | 20 | 20 |
| efn_privacy | 20 | 20 |
| efn_setup_* | 0 (data-only, tanpa test; install sukses) | – |

### Akar masalah & perbaikan
1. **setUpClass 3 kelas berbasis `TestPoSCommon`** (`AssertionError: Could not find all values of account.account(N,) to flush them`) — bug kompatibilitas **OCA auditlog 19.0.2.0.0 × ORM 19.0**: `ThrowAwayCache` (mode `full`) menukar `transaction.field_dirty`, tetapi `Environment._field_dirty` (cached_property baru di 19.0) masih menunjuk dict lama. Dipicu aturan `full` pada `product.template` saat create produk. Ini juga bug produksi (create produk bisa gagal). Perbaikan: `efn_setup_audit/patches.py` membuang memo `_field_dirty` saat blok masuk/keluar (tidak mengubah isi log). Versi modul 19.0.1.0.1.
2. **efn_core `test_brand_code_unique` / `test_business_type_derived_from_brand_code`** (`duplicate key ... res_company_efn_brand_code_unique`) — test: `write({'efn_brand_code': False})` pada company data `efn_setup_company` belum di-flush saat `create` berikutnya INSERT kode yang sama. Tambah `flush_model(["efn_brand_code"])`. Pola sama dipasang preventif di setUpClass test OTP gate & payment gateway.
3. **efn_privacy `test_hash_recomputed_when_phone_changes`** — test salah: nomor awal `081234567890` dan `0812-3456-7890` adalah nomor yang sama setelah normalisasi. Nomor awal diganti `081298765432`. Kode benar.
4. **efn_privacy `TestEfnPrivacyErasure`** (muncul setelah #1) — `open_new_session()` butuh `cls.config`; ditambah `cls.config = cls.basic_config`.
5. **efn_pos_payment_gateway `test_sync_reusing_intent_on_other_order_is_rejected`** (muncul setelah #1) — order kedua memakai `pos.payment.uuid` yang sama sehingga constraint inti `pos_payment_unique_uuid` menolak lebih dulu (IntegrityError + log ERROR) sebelum validasi EFN. Test dipecah: (a) `transaction_id` sama + uuid baris baru -> `UserError` dari validasi EFN (`order_mismatch`); (b) uuid sama -> `IntegrityError` dari constraint inti (logger `odoo.sql_db` di-mute). Kontrol keamanan tidak dilemahkan; kedua jalur ditolak dan order tidak tersimpan.

### File diubah
`efn_setup_audit/{__init__.py,patches.py(baru),__manifest__.py,README.md}`, `efn_core/tests/test_res_company.py`, `efn_privacy/tests/{test_member_hash.py,test_erasure.py}`, `efn_pos_otp_gate/tests/test_otp_gate.py`, `efn_pos_payment_gateway/tests/test_payment_gateway.py`. `ruff check` + `ruff format --check` (0.16.10) bersih.

### Belum teruji
- JS/OWL POS (patch frontend OTP gate & payment gateway, tombol offline-disabled): tidak ada tour/Hoot test; hanya jalur server (`sync_from_ui`) yang teruji.
- HTTP nyata ke BFF/TLS mTLS (semua di-mock); controller Odoo tidak diuji lewat `HttpCase`.
- Upgrade (`-u`) modul dan data `efn_setup_*` terhadap DB berisi data; multi-worker/cron nyata.
- Patch auditlog: perlu dipantau tiap kenaikan pin OCA; laporkan ke upstream OCA/server-tools.

## Gelombang 9 — SEC-31, R2, R3, R4, sisa SEC-07 (2026-10-06)

Acuan: `docs/04_SERVICE_CONTRACTS.md` §8.13 (v3.6), `docs/reviews/SECURITY_REVIEW_2026-10-06-r3.md` (SEC-31, SEC-07 partial), `docs/testing/E2E_SMOKE_REPORT.md` (R2, R3, R4). Semua diverifikasi di image `efn-odoo:ci` (Odoo 19.0-20260926) + PostgreSQL 16.

### 1. SEC-31 — erasure tanpa tautan (`efn_privacy` 19.0.1.1.0)
- Field `res.partner.efn_erasure_request_id` **dihapus**; diganti flag boolean `efn_erased` (admin saja). Nama partner teranonim `Pelanggan dihapus` (tanpa potongan id). `efn_member_hash` kosong karena HP dikosongkan, kini **diverifikasi eksplisit** setelah write (gagal → savepoint rollback, request tidak di-complete).
- `privacy.log.execution_details` tanpa request_id (`EFN erasure (UU PDP): n partner dianonimkan …`).
- **Idempotensi `complete` — desain dipilih: penanda sementara** `efn.privacy.erasure.pending` (`request_id`, `records`; tanpa id partner/hash/HP). Dibuat di savepoint yang sama dengan anonimisasi, ditambah bila run berikutnya menemukan partner baru, **dihapus begitu BFF menjawab 2xx**; sisa (request dibatalkan di BFF) dibersihkan autovacuum > 30 hari. Alternatif "nilai turunan di `privacy.log`" ditolak: hash/HMAC deterministik dari request_id tetap bisa ditautkan oleh siapa pun yang tahu request_id. Model tanpa `company_id` (pengecualian konvensi, dicatat di README): data teknis lintas company; ACL baca saja `base.group_system`.
- Migrasi `migrations/19.0.1.1.0/pre-migrate.py`: `efn_erased = TRUE` untuk partner lama, request_id di-NULL-kan, potongan id dihapus dari `name`/`complete_name`/`account_move.invoice_partner_display_name`, request_id dihapus dari `privacy_log`; ORM lalu men-drop kolom lama. **Diuji runtime**: install versi HEAD (git archive) → seed partner + privacy_log gaya lama → `-u efn_core,efn_privacy,efn_pos_otp_gate` → kolom `efn_erasure_request_id` hilang, partner `Pelanggan dihapus`/`efn_erased=t`, log bersih, 0 ERROR.
- Catatan migrasi: request lama yang sudah anonim tetapi complete-nya belum diterima BFF akan di-complete ulang dengan `records: 0` (informatif).
- Test (`tests/test_erasure.py`): scan `row_to_json` tabel `res_partner`, `privacy_log`, penanda, `mail_message`, `pos_order` → tidak ada request_id, potongan 8 karakter, atau member_hash setelah complete; retry complete (gagal lalu sukses) mengirim `records: 2` dua kali tanpa privacy.log ganda dan penanda hilang; complete 409 → penanda tetap; autovacuum; penanda tidak terbaca kasir.

### 2. R2 — tidak ada PII di URL (`efn_core` 19.0.1.4.0)
- Audit: Odoo **tidak pernah** memanggil saldo poin (tidak ada pemakaian `/v1/pos/loyalty/balance` di addons). Pemakaian `efn.bff.client.get` hanya `GET /v1/pos/payments/<uuid>` dan `GET /v1/pos/privacy/erasures?store=odoo` — tanpa PII.
- Pencegahan regresi: `get()` menolak (`ValueError`, tanpa HTTP) kunci query `PII_QUERY_KEYS` (member_ref, member_hash, phone, mobile, msisdn, email, token, discount_token, otp, code, challenge_id, nik, npwp; case-insensitive) dan nilai str/int yang tampak seperti nomor HP (≥ 8 digit); `get()`/`post()` menolak segmen path seperti nomor HP. Test `test_get_rejects_pii_in_query_and_path`, `test_get_allows_non_pii_params`.
- `scripts/smoke/odoo_pos_smoke.py`: catatan R2 di header (saldo hanya lewat `post(... {"member_ref"})`); langkah `partner` kini juga melaporkan `erased_flag`, `legacy_request_field`, `pending_markers` (SEC-31). Probe `GET /v1/pos/loyalty/balance?member_ref=…` di `scripts/smoke/bff_smoke.py:212` **di luar batas file saya** → untuk BFF/QA.

### 3. R3 — tanpa egress internet (`efn_setup_company` 19.0.1.2.0, konfigurasi standar)
Diverifikasi di source 19.0: cron `base_vat.vies_iap_check_update` memanggil `https://vies.api.odoo.com/api/vies/1/check_update` **selalu** (tidak bergantung `vat_check_vies`) dan `_logger.exception` saat gagal; cron `mail.ir_cron_module_update_notification` (publisher warranty, `update_notification(None)`) menaikkan exception tanpa egress dan, bila ada egress, mengirim nama/email/telepon company + jumlah user ke Odoo S.A.
- `data/no_egress.xml` (`noupdate="0"`): kedua cron `active = False`. `vat_check_vies = False` di 4 company (`res_company_data.xml`, saat install; default CE juga False).
- **Diuji runtime**: install ke DB yang sudah ada → kedua cron nonaktif; `-u base_vat` → tetap nonaktif (upgrade dependen menerapkan ulang); cron publisher yang diaktifkan manual admin **tidak** ditimpa `-u mail` (xmlid noupdate di modul asal) — diterima, didokumentasikan.
- Pemicu lain yang hanya dari aksi pengguna (partner_autocomplete, snailmail/sms, unsplash/OLG, gmail/outlook, Apps) didokumentasikan di README; `partner_autocomplete` (auto-install) gagal sebagai WARNING, disarankan uninstall bila log harus bersih — keputusan functional/lead.
- Test `efn_setup_company/tests/test_no_egress.py` (3 test).
- **Bug ditemukan & diperbaiki saat verifikasi**: `efn_setup_company` gagal di-install ke DB yang sudah ada (`AttributeError: 'int' object has no attribute 'name'` di `account.chart.template.try_loading` 19.0, yang memakai `company.name` untuk warning sebelum mengubah int menjadi recordset). Kini company dikirim sebagai recordset (`obj().env['res.company'].browse(ref(...))`). CI tidak menangkapnya karena semua modul di-install sekaligus di DB baru.

### 4. R4 — docutils `(ERROR/3)` saat install
Odoo 19 `ir.module.module._get_desc` merender README sebagai RST dengan docutils **kecuali** ada `static/description/index.html`. Ditambahkan `static/description/index.html` ringkas (judul, ringkasan, ADR/risiko, rujukan ke README.md) di **10 addon `efn_*`**; isi README.md tidak dikurangi (tetap sumber utama). Hasil: 0 baris docutils di log install+test (sebelumnya 27).

### 5. SEC-07 sisa — kunci HP member (`efn_pos_otp_gate` 19.0.1.4.0)
- **Fit-gap standar 19.0 (verifikasi source)**: `res.partner` create/write hanya untuk `base.group_partner_manager`; `base.group_user` baca saja; POS mengedit pelanggan lewat form standar (tunduk ACL). Standar biner (kasir bisa membuat **dan** mengubah HP siapa pun, atau tidak sama sekali) → gap kecil.
- Implementasi minimal `models/res_partner.py`: HP partner yang punya riwayat OTP (`efn.pos.otp.challenge.partner_id`, retensi 30 hari) atau order `efn_otp_gate_state` consumed/review hanya bisa diubah/dikosongkan `point_of_sale.group_pos_manager`/`base.group_system`/OdooBot; lainnya `AccessError`. Perubahan manajer → catatan chatter ter-mask (`62812****890 → 62813****222`) + log tanpa nomor; auditlog mencatat nilai lengkap.
- **Temuan penting (lintas modul)**: OCA auditlog mem-patch `write` di kelas registry dengan `origin` = MRO saat patch. Bila aturan auditlog dibuat di proses yang sama sebelum modul lain dimuat (install/upgrade sekaligus — persis harness CI), override `write` modul yang dimuat belakangan **terlewati sampai restart**. Karena itu ada dua lapis: override `write` (cek awal + chatter) dan `@api.constrains("phone")` sebagai jaring pengaman (constraint dijalankan Odoo dengan sudo → keputusan memakai uid asli; penanda "sudah dicek" di `cr.cache`, bukan context yang bisa diisi klien RPC). Dampak umum untuk override `write/create/unlink` lain pada model ber-aturan auditlog (product.template, pricelist, loyalty.*, stock.quant, res.partner): **restart Odoo setelah install/upgrade** (deploy normal sudah `--stop-after-init` lalu start). Untuk devsecops/QA.
- Test `tests/test_partner_phone_lock.py` (7 test): kasir ditolak ubah/kosongkan HP member (lapis 1 & 2), boleh field lain/HP non-member/buat pelanggan baru, riwayat order consumed mengunci, manajer tercatat (chatter ter-mask atau auditlog), OdooBot tidak dikunci, constraint pakai uid asli & tidak bisa dilewati lewat context.

### 6. Hasil
- `odoo-test.sh` (fetch OCA, build, install + `--test-tags efn`, install `efn_setup_security`): **`0 failed, 0 error(s) of 117 tests`**, 0 baris `ERROR`/`CRITICAL`, 0 baris docutils. Per modul (stats): efn_core 32, efn_kitting 9, efn_pos_otp_gate 46, efn_pos_payment_gateway 20, efn_privacy 23, efn_setup_company 5 (termasuk subtest).
- `ruff check` + `ruff format --check` (0.16.10) untuk `services/odoo` + `scripts/smoke/odoo_pos_smoke.py`: bersih (70 file).

### File diubah/baru
`efn_core/{__manifest__.py,models/efn_bff_client.py,tests/test_bff_client.py,README.md}`; `efn_privacy/{__manifest__.py,models/efn_privacy_erasure.py,models/res_partner.py,security/ir.model.access.csv(baru),migrations/19.0.1.1.0/pre-migrate.py(baru),tests/test_erasure.py,tests/test_member_hash.py,README.md}`; `efn_pos_otp_gate/{__manifest__.py,models/__init__.py,models/res_partner.py(baru),tests/__init__.py,tests/test_partner_phone_lock.py(baru),README.md}`; `efn_setup_company/{__manifest__.py,data/no_egress.xml(baru),data/res_company_data.xml,tests/(baru),README.md}`; `*/static/description/index.html` (10, baru); `scripts/smoke/odoo_pos_smoke.py`.

### Asumsi / belum teruji
- BFF membuang item erasure dari daftar setelah `complete` 2xx; bila item yang sudah complete muncul lagi, Odoo complete ulang dengan `records: 0` (aman, informatif).
- Lapis 1 SEC-07 (chatter) tidak aktif di proses yang terkena patch auditlog lama; diverifikasi di shell proses baru bahwa lapis 1 menolak. Pesan error di UI POS saat menyimpan pelanggan belum diuji lewat tour.
- `mobile` tidak ada di `res.partner` 19.0 base; constraint hanya `phone` (override `write` menangani `mobile` bila modul lain menambahkannya).
- Install dengan data demo (`--without-demo=False`): `try_loading` kini lolos, tetapi install berhenti di `efn_setup_mrp/demo/mrp_demo.xml:34` (INSERT `stock_warehouse_orderpoint` dengan `location_id`/`warehouse_id` NULL). Bug demo yang sudah ada, di luar paket ini — untuk efn-odoo-functional (pemilik `efn_setup_mrp`). Install tanpa demo (CI, produksi) tidak terdampak.
- Smoke E2E (`run-e2e-smoke.sh`) belum dijalankan ulang terhadap stack penuh di gelombang ini.

# Gelombang 10 — `efn_audit` (pengganti OCA auditlog) + OCA dihapus dari build (6 Okt 2026)

## Modul & file
| Modul | Perubahan |
|---|---|
| `efn_audit` 19.0.1.0.0 (**baru**, LGPL-3, `depends: base, efn_core`) | Sesuai `docs/08` §5.1: `efn.audit.rule` / `efn.audit.log` / `efn.audit.log.line` (nama field = kontrak `efn_setup_audit/data/efn_audit_rule_data.xml`); hook `create/write/unlink` di `_inherit="base"` (ormcache data polos, irisan field ∩ vals sebelum read, `filtered_domain`, `read` lama/baru, hanya field berubah, batch create, skip saat `registry.ready=False`); masking HP (`mask_phone` setelah normalisasi 62…)/email/lainnya; denylist global; aturan `efn.audit.rule` selalu diaudit; grup Auditor/Manager (`base.group_system` → Manager); ACL log baca-saja; immutable (write/unlink → `UserError`, purge hanya via `_efn_audit_purge_*` + penanda `cr.cache`); record rule multi-company (log + baris); `@api.autovacuum _gc_efn_audit_logs` (`efn.audit.retention_days`, default 730, 0 = selamanya, per aturan); API `_efn_audit_purge_records(model, ids)`; view/menu *Audit*; README dengan bagian **Referensi** (OCA auditlog AGPL-3 & Cybrosys user_audit LGPL-3 hanya referensi, tidak ada kode disalin) |
| `efn_setup_audit` 19.0.2.0.0 | `installable: True`; `git rm` `patches.py`, `data/auditlog_rule_data.xml`; test baru `tests/test_setup_audit.py`; README (status, migrasi, test) |
| `efn_privacy` 19.0.1.2.0 | `depends += efn_audit`; cabang `auditlog.log` → `efn.audit.log._efn_audit_purge_records("res.partner", ids)`; test `test_audit_logs_purged`; README |
| `efn_pos_otp_gate` 19.0.1.5.0 | `depends += efn_audit` (jejak SEC-07); docstring/README tanpa alasan patch OCA; `test_manager_change_is_recorded` cek chatter **dan** `efn.audit.log` ter-mask + penolakan kasir tidak menambah log |
| Build/CI | `services/odoo/Dockerfile` (tanpa `COPY oca`/`/mnt/oca-addons`), `odoo.conf.tpl` addons_path, `.dockerignore`, `requirements.txt` (komentar), `services/odoo/oca/` (dihapus, termasuk README), `scripts/fetch-oca.sh` (dihapus), `.gitignore`, `ruff.toml` (exclude oca), `ci.yml` (path-filter → `check-odoo-deps.sh`, langkah fetch & cabang auditlog di `pick()` dihapus, addons-path, `efn_audit` di daftar modul, **langkah baru gerbang `check-odoo-deps.sh`** dengan daftar CE dari image CI), `build-push.yml` (langkah fetch dihapus), `e2e-nightly.yml` + runbook (daftar `-i` + `efn_audit`), runbook §3a migrasi |
| README lain | `efn_kitting`, `efn_setup_mrp`, `efn_setup_company`, `efn_core` manifest: rujukan OCA → `efn_intercompany` fase 1 / referensi ADR-031 |

## Verifikasi nyata (container)
- Image `efn-odoo:ci` di-rebuild tanpa OCA (`/mnt/oca-addons` tidak ada). `check-odoo-deps.sh`: **11 modul, 0 pelanggaran**.
- Install + `--test-tags efn` 10 modul, lalu `efn_setup_security` di DB yang sama: **149/149 lulus, 0 failed, 0 error, 0 baris ERROR/CRITICAL** (60 WARNING = log jalur negatif test yang diharapkan, sebelumnya 65). Per modul: efn_core 28, efn_audit 25, efn_pos_otp_gate 42, efn_kitting 7, efn_pos_payment_gateway 18, efn_privacy 19, efn_setup_company 3, efn_setup_audit 7.
- Performa (test): model tanpa aturan 0 query tambahan; write 50 partner ber-aturan +2 query; create 200 `product.template` (semua field) 463 vs 441 query, ±1,2 s vs 0,8 s.
- ruff 0.16.10 check + format bersih (seluruh direktori CI), compileall OK; yamllint (`.yamllint.yml`) 0; actionlint 1.7.7 untuk `ci.yml`/`build-push.yml`/`e2e-nightly.yml` 0 (temuan SC2153 di `deploy.yml` sudah ada sebelumnya, file tidak disentuh).
- **Migrasi volume dev** (`efn_odoo`, auditlog terpasang, 58 log): backup `pg_dump` → `-i efn_audit -u efn_setup_audit,efn_privacy,efn_pos_otp_gate,efn_core` (image lama) → uninstall `auditlog` via shell → rebuild `efn/odoo:dev` tanpa OCA → up. Hasil: exit 0, 0 ERROR, auditlog *uninstalled*, tabel `auditlog_*` 0, 9 aturan efn, Odoo healthy, create/write produk tercatat (`create`,`write`). Runbook §3a.

## Belum / sisa
- Belum PoC beban (100 order POS, 1.000 produk) di staging; tamper-evident (hash berantai/ETL) & enkripsi kolom PII = keputusan klien/DPO.
- Action binding "Lihat log audit" belum dibuat (opsional). `filter_domain` tanpa variabel eval (`uid`, `context_today`).
- Dokumen yang masih menyebut OCA/auditlog sebagai rujukan (bukan pemakaian aktif): `docs/06`, `docs/08` (spesifikasi/referensi), `docs/testing/E2E_SMOKE_REPORT.md` (historis), ADR & laporan agent — wewenang architect/functional.
- CI GitHub belum dijalankan (gerbang akhir). Tidak ada commit (instruksi lead).

# Gelombang 11 — SEC-43, SEC-44, SEC-45 (+ SEC-46 sebagian), review r4 (6 Okt 2026)

## Perubahan
| Modul / file | Perubahan |
|---|---|
| `efn_audit` 19.0.1.1.0 — `models/audit_policy.py` (**baru**) | Kebijakan hard-code: `MIN_RETENTION_DAYS = 365`, validasi/efektif retensi; `CONTROL_SPECS` (model kontrol selalu diaudit: `ir.config_parameter` key `efn.audit.%`, `ir.actions.server` semua field termasuk `code`, `ir.cron` jadwal/user/aktif tanpa `nextcall/lastcall/failure_*`, `ir.module.module.state`, `base.automation`); `PROTECTED_LOG_MODELS`; pola PII (`is_pii_field`) + mask HP/email/NPWP-NIK (`****` + 3 digit akhir)/nama orang (inisial) |
| `models/ir_config_parameter.py` (**baru**) | Constraint `efn.audit.retention_days`: bukan bilangan bulat ≥ 0 atau 1..364 → `ValidationError` dengan pesan jelas |
| `models/efn_audit_rule.py` | `retention_days` 1..364 ditolak; `_efn_get_rule` menggabungkan spesifikasi bawaan (aturan admin hanya menambah: field gabungan, filter OR, flag dipaksa aktif; arsip aturan tidak mematikan bawaan); `RuleData.filters` (tuple domain, OR); aturan ber-xmlid tidak bisa dihapus (hanya diarsipkan, tercatat) kecuali konteks uninstall modul; log WARNING saat aturan dihapus |
| `models/efn_audit_log.py` | GC: retensi efektif ≥ 365 (nilai kecil via SQL diperlakukan minimum), log `purge` + log model kontrol tidak pernah dihapus (juga di `_efn_audit_purge_logs` langsung); `_efn_audit_purge_records` menolak model kontrol; field `prev_company_id` (SEC-46) dan `is_personal` (compute stored; line: related stored); log purge dikirim ke sink |
| `models/base.py` | Masking wajib berbasis pola nama field + nama orang (non-perusahaan); `res_name` model orang = `model,id`; `prev_company_id` hanya bila `company_id` di vals (tanpa query lain); sink `odoo.addons.efn_audit.sink` (`EFN_AUDIT_SINK {json}` via `cr.postcommit`, tanpa nilai/PII) untuk log model kontrol |
| `security/efn_audit_groups.xml`, `security/efn_audit_security.xml`, `migrations/19.0.1.1.0/post-migrate.py` | Grup baru **Auditor Data Pribadi Lintas PT** (`group_auditor_personal`, tidak diberikan ke siapa pun default, admin juga tidak). Rule baru (noupdate=0): auditor = company/prev_company diizinkan + log tanpa company non-pribadi; auditor pribadi = + semua log tanpa company. Migrasi menghapus rule lama noupdate |
| `views/efn_audit_log_views.xml`, `README.md` | Field baru di form; README: klaim "immutable termasuk admin" dikoreksi → "immutable terhadap ORM/RPC, admin sistem tetap dapat memengaruhi lewat kode/uninstall/DB — dimitigasi"; bagian tamper resistance, masking, record rule, test, batasan |
| `efn_pos_otp_gate/models/res_partner.py` (SEC-44) | Syarat `not env.su` dihapus: keputusan selalu dari uid asli; OdooBot (`_is_superuser`) lolos tanpa chatter; penanda `cr.cache` hanya diisi setelah pemeriksaan benar-benar jalan dan lolos. Docstring + README |
| `efn_setup_audit` data (komentar saja) | Catatan SEC-45 (masking di kode) dan minimum retensi SEC-43. Data `noupdate` tidak diubah |
| Test | `efn_audit/tests/test_audit_tamper.py` (9), `test_audit_privacy.py` (5), penyesuaian `test_audit_security.py` (retensi 365/400 hari, parameter tidak valid via SQL), `test_audit_hook.py` (retensi 400); `efn_pos_otp_gate/tests/test_partner_phone_lock.py` +3 (sudo+kasir via `write` sungguhan ditolak di lapis 1 dan lapis 2 `models.Model.write`, penanda tidak tertinggal; manajer via sudo boleh + chatter; OdooBot boleh tanpa chatter) |

## Verifikasi (container, harness tanpa OCA, DB sementara — stack dev tidak disentuh)
- `odoo-test-nooca.sh` (build image + `check-odoo-deps` + install/test 10 modul + `efn_setup_security`): **166/166 lulus, 0 failed, 0 error, 0 baris ERROR/CRITICAL** (65 WARNING = jalur negatif yang diharapkan). efn_audit 47, efn_pos_otp_gate 49, efn_privacy 23, efn_setup_audit 9, efn_core 32, efn_pos_payment_gateway 20, efn_kitting 9, efn_setup_company 5.
- Uji upgrade: install `efn_setup_audit` dari HEAD (efn_audit 19.0.1.0.0) + log partner → `-u efn_audit,efn_setup_audit` dengan kode baru: exit 0, 0 ERROR, rule lama hilang, 4 rule baru, `is_personal` log lama = True, versi 19.0.1.1.0.
- `check-odoo-deps.sh`: 11 modul, 0 pelanggaran. ruff 0.16.10 `check` + `format --check` bersih (`services/odoo/addons`).

## Batas yang diakui (dokumentasi, bukan bug)
- Admin sistem tetap bisa: server action berisi kode yang memanggil metode privat atau SQL (`env.cr.execute`), uninstall `efn_audit`, akses DB. Sekarang setiap pembuatan/perubahan server action/cron/modul **tercatat** (log tidak ikut GC) dan **dikirim ke sink setelah commit**, tetapi kode itu sendiri juga bisa menghapus baris tersebut lewat SQL dalam transaksi yang sama — baris sink Loki dari commit **sebelumnya** (mis. pembuatan server action) tetap ada. Mitigasi kuat = `base.group_system` hanya Superadmin + sink eksternal.

## Spesifikasi untuk lead (di luar batas file saya)
1. **devsecops (Loki/Alertmanager):** Promtail sudah mengirim log Odoo; tambahkan rule Loki `{service="odoo"} |= "EFN_AUDIT_SINK"` → alert (severity warning) bila `"model": "ir.config_parameter"` / `"efn.audit.rule"` / `"ir.actions.server"` / `"ir.cron"` / `"ir.module.module"` atau `"operation": "purge"` dengan note bukan `retensi:`/`erasure:`; juga alert pada `EFN audit: user .* menghapus .* aturan audit`. Pertimbangkan retensi Loki khusus stream ini > 30 hari.
2. **data-ai (ETL ClickHouse):** tabel `efn_audit_log` + `efn_audit_log_line` (append-only, incremental by `id`), user ClickHouse tanpa `ALTER/DELETE`, kolom `row_hash = sha256(prev_hash || payload)` dihitung di ETL; laporan rekonsiliasi harian "id hilang di PostgreSQL vs ClickHouse" (gap id = indikasi penghapusan di luar GC; GC sendiri tercatat sebagai log `purge` berisi jumlah).
3. **architect/DPO:** `PDP_DATA_INVENTORY.md` — tambahkan `efn.audit.log` (data: HP/email/NPWP ter-mask, nama orang ter-mask, uid pelaku; retensi 730 hari default, minimum 365; pembaca: Auditor per PT, Auditor Data Pribadi Lintas PT; dihapus saat erasure). Penunjukan anggota `group_auditor_personal` = keputusan klien. Nilai `MIN_RETENTION_DAYS` (365) menunggu Q-6/Q-49.
4. **SEC-43 butir 5:** batasi `base.group_system` ke Superadmin (functional/devsecops).

## Belum / sisa
- Hash berantai di Odoo tidak dibuat (dipilih di ETL, butir 2). Uji server action nyata yang memanggil `_efn_audit_purge_logs` lewat UI belum dijalankan; test hanya memastikan pembuatan/perubahan kodenya tercatat dan log terlindung tidak terhapus oleh metode itu.
- SEC-46: `prev_company_id` dikerjakan; `with_user(SUPERUSER_ID)` kehilangan uid pemicu dan `_write`/SQL/upgrade tetap di luar cakupan (README). Index untuk `=like` purge erasure belum.
- `res.groups`/`res.users.group_ids` belum masuk model kontrol (bisa ditambah admin lewat aturan).
- Sink baru diverifikasi di test (`assertLogs` setelah `postcommit.run()`); belum dilihat di Loki stack nyata. CI GitHub belum dijalankan. Tidak ada commit.

# Gelombang 12 — Tour UI POS (OWL) nyata, SEC-46 sisa, SEC-30 (7 Okt 2026)

Basis `d61faf0`. Tidak ada commit. Stack dev tidak di-restart (Docker Desktop sempat mati di antara sesi dan dinyalakan ulang; semua uji memakai container/DB sementara `efn-ci-pg`/`efn-tourci-pg`, sudah dibersihkan).

## Perubahan
| Area | File | Isi |
|---|---|---|
| Image test | `services/odoo/Dockerfile` | Stage `runtime` (isi produksi) → `test` (Google Chrome stable dari repo apt Google, kunci diverifikasi fingerprint `EB4C1BFD…4796` + `signed-by`; `python3-websocket`) → `production` (stage terakhir = target default, tanpa browser). Build memverifikasi `import passlib.context` (SEC-30). |
| SEC-30 | `services/odoo/render_odoo_conf.py` | Fallback plaintext dihapus: passlib hilang → `FATAL` exit 1. Diverifikasi: hash `$pbkdf2-sha512$600000…` normal; `passlib.context` disembunyikan → exit 1. |
| Tour OTP | `efn_pos_otp_gate/static/tests/tours/efn_otp_gate_tour.js`, `tests/test_pos_otp_tour.py`, manifest `web.assets_tests` | 3 tour (lihat bawah), `HttpCase`/`TestPointOfSaleHttpCommon.start_pos_tour`, tag `efn_tour`, BFF di-mock server-side (`patch.object(registry["efn.bff.client"], "post", autospec)`). |
| Tour QRIS | `efn_pos_payment_gateway/static/tests/tours/efn_gateway_tour.js`, `tests/test_gateway_tour.py`, manifest | 2 tour: paid, cancel. Mock `post`+`get`. |
| SEC-46 | `efn_audit/models/base.py`, `models/efn_audit_log.py`, views, manifest `19.0.1.2.0` | Field `origin_user_id` (User Pemicu) dari sumber tepercaya saja: `request.session.uid` atau helper `elevate_as_superuser(env)` (penanda `cr.cache`); context RPC diabaikan. Ikut di log purge + sink Loki (`origin_uid`). Index parsial `text_pattern_ops` pada `old_ref`/`new_ref` untuk purge erasure. Test `tests/test_audit_origin.py` (6). |
| CI | `.github/workflows/ci.yml` | Job baru `odoo-tour` (build `--target test`, DB terpisah modul `efn_pos_otp_gate,efn_pos_payment_gateway`, `--test-tags efn_tour`, `--init --no-healthcheck --shm-size=1g`; gagal bila ada ERROR, bila browser di-skip, atau < 5 test berjalan) + `odoo-tour` ditambahkan ke `needs` `ci-gate` (satu baris di luar job odoo-test, diperlukan agar gerbang menahan merge). Job `odoo-test` tidak diubah. |
| Dok | README `efn_audit`, `efn_pos_otp_gate`, `efn_pos_payment_gateway` | Test, batasan baru. |

## Tour yang lolos (Chrome 154 headless, Odoo 19 nyata)
1. `EfnPosOtpGateTour`: produk → Reward ber-OTP tanpa pelanggan → notifikasi + daftar pelanggan → pilih member → popup OTP (nomor `62812****890`) → kode salah (notifikasi, reward tidak terpasang) → kode benar → reward terpasang → bayar Bank → order `paid`, `efn_otp_gate_state = consumed`, payload request (member dari partner, company_code) & consume (order_ref, amount_total, token) diperiksa.
2. `EfnPosOtpNoCustomerTour`: tanpa pelanggan, daftar pelanggan ditutup → tidak ada panggilan BFF, tanpa reward, order tetap bisa dibayar.
3. `EfnPosOtpOfflineTour`: offline → tombol Reward tidak menawarkan reward ber-OTP; online → OTP → reward; offline → Validate diblok dialog "POS sedang offline"; online → validate → `consumed`.
4. `EfnPosQrisPaidTour`: metode QRIS EFN → Send → QR PNG di `QRPopup` → polling `paid` → "Payment Successful" → validate → order lunas, `transaction_id` = payment_id BFF.
5. `EfnPosQrisCancelTour`: batal dari popup → intent `cancelled` → baris `Retry` → hapus → bayar Bank.

Temuan saat menulis tour: kode frontend POS yang ada (OTP gate & gateway) berjalan tanpa perubahan. Catatan util offline Odoo: event `offline` dipicu sebelum fetch/XHR diblok, sehingga tour memanggil ulang `posmodel.data.checkConnectivity()`.

## Validasi
| Gerbang | Hasil |
|---|---|
| Harness unit (`odoo-test-nooca.sh`, image produksi, 10 modul + efn_setup_security) | **0 failed, 0 error(s) of 172 tests**, 0 baris ERROR (efn_audit 57 termasuk 6 baru) |
| Harness tour (salinan job `odoo-tour`, image `test`) | **0 failed, 0 error(s) of 5 tests**, 5× `tour succeeded`, 0 ERROR, 0 skip browser |
| Image produksi tanpa Chrome | `command -v google-chrome chromium` → tidak ada |
| check-odoo-deps | 11 modul, 0 dependensi terlarang |
| ruff 0.16.10 check + format --check (`services/odoo`) | bersih |
| hadolint v2.12.0 (versi hadolint-action CI) | bersih. `hadolint:latest` melaporkan DL3025 pada `HEALTHCHECK CMD` (sudah ada di HEAD, bukan dari perubahan ini) |
| yamllint (`.yamllint.yml`) + actionlint 1.7.12 (+shellcheck) `ci.yml` | bersih |

## Belum diverifikasi / sisa
- Tour belum dijalankan di GitHub Actions (hanya salinan lokal job). Image `test` tidak reprodusibel (Chrome stable saat build); tidak pernah di-deploy.
- Tour berjalan di DB tanpa `efn_setup_*` (fixture `TestPointOfSaleHttpCommon` gagal di `setUpClass` dengan 4 company `efn_setup_company`: user `accountman` tidak bisa membaca pajak company lain). Konfigurasi POS nyata efn_setup_pos belum diuji lewat UI.
- Mode mobile (`isActive: ["mobile"]`), layar pelanggan QRIS, kedaluwarsa QR & offline di tengah polling, BFF/WhatsApp/provider nyata: belum.
- SEC-46: cron/consumer yang memakai `with_user(SUPERUSER_ID)` langsung (tanpa helper, tanpa sesi HTTP) tetap tanpa uid pemicu; `_write`/SQL langsung & `registry.ready=False` tetap di luar cakupan (by design). Upgrade `efn_audit` 19.0.1.1.0 → 1.2.0 pada DB dev/staging belum dijalankan (kolom & index dibuat ORM); efektivitas index belum diukur pada volume besar.

---

# Gelombang 12b — SEC-22 sisa: traceback di respons error JSON-RPC / `/json/2` (7 Okt 2026)

## Perubahan
- `services/odoo/addons/efn_core/models/ir_http.py` (baru): override `ir.http._handle_error` (classmethod resmi, dipanggil `Request._update_served_exception` untuk setiap exception pada request ber-DB). Hanya dispatcher `jsonrpc`/`json2` dengan respons `application/json` yang di-post-process; tidak ada monkeypatch `odoo.http.serialize_exception` (dipakai juga oleh `ir_cron`, export, report).
  - Non-`base.group_system` (termasuk tanpa sesi/publik): `debug` → `""`. Exception bisnis (`UserError`+turunan, `RedirectWarning`, `SessionExpiredException`, `HTTPException`) mempertahankan `name`/`message`/`arguments`/`context` → dialog POS (`handleRPCError` memakai `exceptionName` + `data.message`) dan web client tidak berubah. Exception tak terduga → `name`=`Exception`, pesan generik, `arguments`=[pesan], `context`={}.
  - Admin: uid dari **sesi** (bukan `request.env.uid` yang bisa dinaikkan controller ke superuser); `/json/2` tanpa sesi memakai uid API key. Cek gagal → non-admin (fail closed). Parse/redaksi gagal → respons generik + log exception.
  - Log server tidak berubah (traceback penuh tetap dicatat `odoo.http`).
- `efn_core/tests/test_error_redaction.py` (HttpCase, tag `efn`): method pemicu ditambahkan sementara ke kelas registry `res.partner` (`patch.object(..., create=True)`), tanpa route produksi baru. Kasus: kasir UserError (pesan utuh, `debug` kosong), kasir exception tak terduga (generik, tanpa `Traceback`/`.py`/argumen), tanpa sesi, admin (traceback ada), `/json/2` API key kasir vs admin.
- `efn_core` 19.0.1.4.0 → 19.0.1.5.0 (tanpa migrasi data); README, `docs/runbooks/odoo-access-changes.md` §2 diperbarui.

## Validasi (container, Odoo 19 nyata)
- `odoo-test-nooca.sh`: build OK, check-odoo-deps 11 modul 0 terlarang, **0 failed / 0 error dari 177 test**, 0 baris ERROR (efn_core 39 test termasuk 5 baru).
- `odoo-tour.sh`: **5/5 tour lolos**, 0 ERROR, skip browser 0.
- ruff 0.16.10 `check` + `format --check` `services/odoo`: bersih.

## Belum / sisa
- Request tanpa database (`_serve_nodb`, mis. tanpa `dbfilter`) memakai `dispatcher.handle_error` langsung, tidak lewat `ir.http` → tidak teredaksi. Produksi: satu DB + dbfilter (perlu dikonfirmasi devsecops).
- Admin sistem pada error DB (transaksi aborted) bisa kehilangan `debug` karena cek group gagal → fail closed; diagnosis lewat Loki.
- Bug upstream POS `hooks.js` (`error.data.debug.status.message_body` pada string) tidak berubah perilakunya (sudah melempar sebelum perubahan ini).
- Belum diuji manual di stack dev (tidak di-restart, QA sedang memakai); belum dijalankan di GitHub Actions.

# Gelombang 13 — SEC-49, SEC-51, SEC-56 (review r5), 7 Okt 2026

Batas file: `efn_audit/**`, `efn_setup_audit/README.md`, `docs/runbooks/odoo-access-changes.md`. Addon lain tidak disentuh; stack dev tidak di-restart; tanpa commit.

## Perubahan (`efn_audit` 19.0.1.2.0 -> 19.0.1.3.0, tanpa perubahan skema)
- **SEC-49** (`models/audit_policy.py` `CONTROL_SPECS`): model kontrol baru, selalu diaudit, tidak ikut GC, ditolak `_efn_audit_purge_records`, masuk sink: `res.users` (`group_ids`, `active`, `login`, `company_ids`, `company_id`, `share`), `res.groups` (semua field: `user_ids`, `implied_ids`, `implied_by_ids`, `rule_groups`, ...), `ir.rule`, `ir.model.access` (semua field), `res.users.apikeys` (`name`, `user_id`, `scope`, `expiration_date`). Nama field diverifikasi di image `odoo:19.0` yang dipin (`group_ids`/`user_ids`, bukan `groups_id`/`users`).
  - API key: Odoo 19 membuatnya lewat SQL (`_generate`), jadi `models/res_users_apikeys.py` meng-override `_generate` untuk mencatat create; unlink lewat hook biasa. `key`/`index` di denylist eksplisit.
  - Create model kontrol mencatat semua field dilacak yang bernilai (grup default user baru ikut tercatat).
  - Nilai: x2many pada write = selisih (`dihapus:` / `ditambah:`), ref = daftar lengkap; grup ditampilkan sebagai xmlid.
  - Sink: `audit_op` (`access_grant`, `access_revoke`, `access_rule_change`, `apikey_create`, `apikey_revoke`, `user_change`, `group_change`, `control_change`, `purge`, `purge_manual`), `critical` (grant langsung/implied ke `base.group_system`, `base.group_erp_manager`, `efn_audit.group_auditor_personal`, `efn_audit.group_manager`; semua perubahan ir.rule/ACL; purge manual), `groups_added`/`groups_removed` (xmlid), `users_added`/`users_removed` (uid), `key_owner_uid`. Tanpa nama/login.
  - Test menemukan perilaku nyata Odoo: menambah company ke user otomatis memberi `base.group_multi_company`. Ini kini tercatat sebagai `access_grant` (non-critical).
- **SEC-51**: sink v2. Baris `EFN_AUDIT_SINK {json}`; JSON `ensure_ascii` + `sort_keys` sehingga newline/teks pengguna selalu di-escape. `mac` = HMAC-SHA256 atas bentuk kanonik (`sink_canonical`), dengan kunci dari file `EFN_AUDIT_SINK_KEY_FILE` (default `/run/secrets/efn_audit_sink_key`, minimal 32 byte). Kunci dibaca proses saja, tidak ada di DB/parameter, dan tidak terjangkau `safe_eval`. Bila kunci tidak ada: `mac: null` + WARNING sekali (tidak diam-diam). Butir 4 juga dikerjakan: `_efn_audit_purge_logs` menolak log yang belum melewati retensi efektifnya. Log purge di luar jalur GC/erasure (penanda `cr.cache`) diberi awalan `manual:` + `purge_manual` critical.
- **SEC-56**: relasi ke `PERSONAL_MODELS` disimpan `model,id` tanpa nama (M2o & M2m, termasuk `stock.quant.user_id` di aturan bawaan). Pola PII ditambah token `street`/`street2`/`street3`/`zip`/`address`/`login`, serta `birth` untuk tipe date/datetime/char (`****-**-**`). `login` di-mask email/HP-aware. `city` sengaja tidak di-mask.
- Test baru: `tests/test_audit_access.py` (9), `tests/test_audit_sink.py` (8). `test_audit_hook.test_relational_format_and_refs` disesuaikan (selisih + `res.partner,id`). README `efn_audit`/`efn_setup_audit` + runbook `odoo-access-changes.md` (kalimat "bila aturan res.users aktif" diganti, ditambah §3 sink untuk devsecops).

## Validasi (container, DB sementara)
- Harness unit (salinan `odoo-test-nooca.sh` dengan nama container/image `-aud` agar tidak bentrok): **193 test, 0 failed, 0 error, 0 baris ERROR**. efn_audit 77 test. `efn_setup_security` ter-install OK. WARNING 67 vs baseline 64: +1 WARNING kunci HMAC tidak ada (sesuai desain), sisanya log hapus aturan uji.
- `check-odoo-deps`: 11 modul, 0 dependensi terlarang.
- Tour `odoo-tour.sh`: **5/5**, 0 ERROR, Chrome tidak ada di image produksi. Tour dijalankan sebelum perbaikan terakhir yang hanya menyentuh file test; kode model tidak berubah sesudahnya.
- ruff check + ruff format --check `efn_audit`, `efn_setup_audit`: lolos.

## Untuk lead (di luar batas file saya)
- **devsecops**: (1) Promtail: ganti selector substring dengan regex ber-anchor `^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \d+ INFO \S+ odoo\.addons\.efn_audit\.sink: EFN_AUDIT_SINK (\{[^\n]*\})$` + stage json yang mewajibkan `log_id`. Ambil label dari kunci JSON `audit_op` (kardinalitas ~10), bukan `operation`, agar tidak bentrok `audit_op_extracted`. (2) Alert Loki critical untuk `critical=true` dan `audit_op=~"access_rule_change|purge_manual"`; warning untuk `apikey_create`. Aturan purge yang ada (`note !~ retensi|erasure`) tetap bekerja karena purge manual berawalan `manual:`. (3) Docker secret `efn_audit_sink_key` (32+ byte acak) di-mount ke odoo + etl.
- **data-ai**: ETL memverifikasi `mac` (`hmac.compare_digest(mac, HMAC-SHA256(key, json.dumps(event tanpa mac, sort_keys=True, separators=(",",":"), ensure_ascii=True)))`). Cocokkan `log_id` sink dengan `efn_audit_log`; tandai baris tanpa/mac salah.
- **efn_core** (bukan batas saya): `logging.Filter` global yang meng-escape `\r\n` di argumen log (SEC-51 butir 2) masih disarankan untuk stream lain.
- SEC-43 butir 5 (`base.group_system` hanya Superadmin) tetap keputusan klien. HMAC membuktikan asal baris, bukan kebenaran isinya, terhadap admin yang menulis kode server action.

## Belum / sisa
- Tidak tercatat: ACL/rule/grup dari data XML/CSV saat install/upgrade, penghapusan API key kedaluwarsa oleh autovacuum (SQL), SQL langsung.
- Upgrade 19.0.1.2.0 -> 19.0.1.3.0 di volume dev belum diuji. Tidak ada migrasi karena tidak ada perubahan skema. Log M2m lama tetap berformat daftar penuh.
- Overhead pada beban nyata (POS/ubah user massal) belum diukur. Penulisan `res.users` tanpa field dilacak tetap 0 read tambahan.

# Gelombang 13b — Poin EFN di POS (E-22, E-23, E-24), 2026-10-07

Batas file: `efn_pos_loyalty_ledger/**` (baru), `efn_pos_otp_gate/**` (E-23), `efn_setup_pos` (hanya komentar filter audit + README, E-24), `.github/workflows/ci.yml` (daftar modul odoo-test/odoo-tour + `EFN_TOUR_MIN_TESTS` 5→9), `scripts/smoke/odoo_pos_smoke.py` (langkah `loy_*`). Tanpa commit; stack dev tidak di-restart. Tidak ada kode vendor yang dipelajari/dipakai.

## Perubahan
- **E-23 `efn_pos_otp_gate` 19.0.1.5.0 → 19.0.1.6.0:** `efn.pos.otp.challenge.purpose` (`pos_promo` default | `pos_redeem`, required, index); `efn_otp_request(..., purpose)` mengirim `purpose` ke BFF; `efn_otp_verify(..., purpose)`; `_efn_find_latest/_efn_find_valid_verified` default `pos_promo` → gate promo tidak menerima challenge tukar poin dan sebaliknya; `efn_otp_flow.js` opsi `{purpose, amount}`. +6 test lintas-purpose.
- **E-22 `efn_pos_loyalty_ledger` 19.0.1.0.0 (baru):** produk "Poin EFN" (`default_code=EFN-POINTS`, `_get_special_products`), `product.category.efn_loyalty_earn_excluded` (K-2, juga untuk ETL), `pos.config.efn_loyalty_earn_enabled/redeem_enabled` (+ settings, constraint kiosk), model `efn.pos.loyalty.reservation` & `efn.pos.loyalty.outbox` (idempotency key unik, payload tidak disimpan, backoff 1 m→1 j, `FOR UPDATE SKIP LOCKED`, `_trigger`, reverse menunggu earn/commit, aktivitas POS manager), RPC `efn_loyalty_balance/redeem_reserve/redeem_release/refund_preview`, validasi sync (deteksi dari produk; tanpa reservasi → `UserError`; nilai/partner beda → review; reservasi tanpa baris → release; draft tanpa reservasi → baris dibuang), earn/reverse via outbox, release saat `action_pos_order_cancel`/`remove_from_ui`. Frontend: tombol *Cek Poin* & *Tukar Poin EFN* di layar pembayaran, baris per kelompok pajak `price_type:"automatic"`, dialog batal (Back/ganti pelanggan/hapus order) + release, blok validasi offline, penyesuaian poin otomatis di layar refund, baris poin dinetralkan dari `pos_loyalty`, teks struk. Menu Review Poin / Reservasi / Outbox.
- **E-24:** filter pengganti "Baris negatif non-promo (… tanpa Poin EFN)" + "Order dengan tukar Poin EFN"; filter lama `efn_setup_pos` dinonaktifkan `post_init_hook` (aktif lagi saat uninstall).

## Keputusan/asumsi kontrak (perlu dikonfirmasi lead/BFF)
1. **K-1 eligible_amount**: barang eligible tanpa pajak × faktor uang (1 − nilai poin/nilai barang, tanpa pajak) — mengikuti kalimat "bagian yang dibayar poin tidak menghasilkan poin". `refund_ratio` = barang eligible direfund / barang eligible asal; `refunded_eligible_amount` = barang eligible refund × faktor uang order asal.
2. Earn dikirim untuk setiap order ber-member di config `earn_enabled` (termasuk eligible 0, §9.7); HP non-+62/anonim → `skipped` lokal.
3. Reserve: 409 bisnis (`insufficient_points`, `below_minimum`, `exceeds_max_pct`, `reservation_exists`) → challenge tetap `verified` (kasir boleh coba jumlah lain; bila BFF sudah memakai token, coba berikutnya `token_invalid` → OTP baru). `token_invalid`/`member_mismatch`/`amount_mismatch`/timeout → challenge `failed`.
4. Balance body `{member_ref, pos_config_id, company_code}` (§9.7); params dari `GET /v1/pos/loyalty/params` (cache 5 menit/worker); `amount` OTP = `amount_total` reserve = total order sebelum poin.
5. Retry outbox: jaringan/5xx, 429, `earn_pending`; 409 lain final (`reservation_expired` → reservasi & order review); 404 release = selesai.
6. **Deviasi spesifikasi:** `outbox.order_id` tidak wajib untuk `redeem_release` (order draft dihapus `remove_from_ui`; `restrict` akan menggagalkan penghapusan) — dijaga CHECK constraint. `outbox.reservation_id` `ondelete=set null` (autovacuum reservasi). Field UI baris `efn_loyalty_reservation_id` tidak dibuat (frontend memakai produk + field order `efn_loyalty_points_redeemed/redeem_value`, ditimpa server). Parameter `efn.pos.loyalty.timeout_seconds` tidak dibuat (klien `efn_core` memakai `efn.pos.otp_timeout_seconds`; mengubah klien di luar batas). Rate limit saldo per sesi bersifat in-process per worker.
7. `pos_self_order` (terpasang lewat `efn_setup_pos`) membatalkan, bukan menghapus, order di `remove_from_ui` — release tetap diantre (temuan dari test).

## Validasi (container, DB sementara)
- Harness unit `odoo-test-loy.sh` (salinan dengan image/container `-loy`): **250 test, 0 failed, 0 error, 0 baris ERROR**; `efn_pos_loyalty_ledger` 51 test, `efn_pos_otp_gate` +6 test; `efn_setup_security` ter-install. `check-odoo-deps`: 12 modul, 0 terlarang.
- Tour `odoo-tour-loy.sh`: **9/9** (5 lama + 4 baru: sukses, OTP salah, batal→release, offline diblok), 0 ERROR, 0 skip browser.
- ruff check + format --check (`services/`, `scripts/smoke/`): lolos.

## Belum / sisa
- Integrasi dengan BFF nyata belum dijalankan (stack dev tidak di-restart; modul belum terpasang di DB dev). Langkah smoke `loy_*` ditambahkan tetapi belum dirangkai di `run-e2e-smoke.sh` (di luar batas).
- Tour skenario refund (penyesuaian otomatis di layar refund) belum ada; logika server teruji unit (`efn_loyalty_refund_preview` + validasi sync). Refund berpoin saat offline → review.
- Perlakuan PPN poin (P-8) dan nilai bisnis poin (Q-13) masih placeholder; `totalFactor` promo `pos_loyalty` bila promo dihitung ulang setelah poin terpasang belum diuji.
- Upgrade `efn_pos_otp_gate` 1.5→1.6 di volume dev belum diuji (kolom baru dengan default; tanpa skrip migrasi).

# Gelombang 13c — SEC-58 (High), SEC-59, SEC-60, SEC-62, SEC-63, SEC-65 (review r6), 2026-10-07

Batas file: `services/odoo/addons/**`, `scripts/smoke/odoo_pos_smoke.py`, laporan ini. Tanpa commit; stack dev tidak disentuh (harness `odoo-test-s58.sh` / `odoo-tour-s58.sh`, image & container `-s58`).

## Perubahan
- **SEC-58 `efn_core` 19.0.1.6.0:** `efn.bff.client.post/get` → `_efn_post/_efn_get` (privat; `@api.private` ada di 19.0 tetapi awalan `_` dipilih). Semua pemanggil diperbarui (`efn_pos_otp_gate`, `efn_pos_loyalty_ledger`, `efn_pos_payment_gateway`, `efn_privacy`, test, smoke — smoke kemudian juga diadaptasi agen lain dengan helper `_bff_method` yang kompatibel dua versi).
  - Audit metode publik `efn_*`: hanya RPC kasir `pos.config.efn_otp_*`/`efn_loyalty_*`, `pos.payment.method.efn_gateway_*`, tombol supervisor baru `efn.pos.loyalty.outbox.action_efn_hold_*`, override CE (`pos.order.action_pos_order_cancel/remove_from_ui`, `res.partner.write`, ORM `efn_audit`). `_efn_check_otp_caller` kini juga `check_access('read')` pada config + company config ∈ company user (call_kw hanya `browse`).
  - Gerbang: `efn_core/tests/test_rpc_surface.py` — AST (metode publik baru tanpa `RPC_ALLOWLIST` → gagal; AbstractModel `efn.*` = 0 metode publik), registry (`get_public_method` nyata), HttpCase `call_kw` user `base.group_user` → ditolak, `requests` tidak terpanggil. `efn_pos_loyalty_ledger/tests/test_rpc_access.py` (HttpCase `call_kw`, tag `efn_tour` karena fixture `TestPointOfSaleHttpCommon`): kasir & user biasa ditolak ke `efn.bff.client.*`; user biasa ditolak ke RPC POS/gateway/outbox; kasir PT lain & kasir tanpa sesi ditolak; kasir tidak bisa memutuskan outbox hold; jalur sah `efn_loyalty_balance` via RPC tetap OK.
- **SEC-59 `efn_pos_loyalty_ledger` 19.0.1.1.0:** jenis order dari server (`_efn_loyalty_order_kind`: `refunded_orderline_id`, qty < 0 tanpa asal, `is_refund` hanya menambah kecurigaan). `abs()` dihapus (earn hanya dasar bertanda positif). Retur tanpa struk / tukar barang / refund ambigu → `review` + catatan chatter, tanpa earn/reverse. Temuan: core 19 sudah menolak refund dari > 1 order asal (`ValidationError`).
- **SEC-60:** mismatch baris penyesuaian → outbox reverse `state='hold'` (cron tidak mengirim), order `review`, aktivitas POS manager; tombol *Setujui & kirim* / *Batalkan* (POS manager + record rule + company); rasio ditahan tetap dihitung dalam batas kumulatif.
- **SEC-62:** validasi preventif di `_efn_loyalty_validate_paid` → `UserError` (rollback) untuk baris poin ≥ 0, nilai ≠ `value_idr`, partner beda, poin > `redeem_max_pct` × total barang server, total < 0. `redeem_max_pct` disimpan di reservasi saat reserve (params BFF §9.1, fallback `efn.pos.loyalty.redeem_max_pct_fallback` = 50).
- **Pesan lead (§9.8 v4.3):** body earn membawa `pos_config_id`. `409 earn_limit_exceeded` sudah final + review (test lama); test baru: reverse atas order ber-earn ditolak → `earn_pending` diulang → 48 jam `failed (max_age:earn_pending)` + review. Runbook retur di README modul.
- **SEC-63 `efn_setup_pos` 19.0.1.3.0:** `barcode_hardening_data.xml` (noupdate=0): semua `barcode.rule` tipe `discount` (nomenklatur apa pun) → aturan produk `22` (tidak dihapus agar tidak dibuat ulang upgrade point_of_sale). Filter audit baru *Label harga produk timbang (barcode 23)*. README: risiko PIN `pos_hr` (SHA-1 tanpa salt di browser) + mitigasi (PIN manager ≥ 8 digit, rotasi 90 hari/saat keluar, badge `041`, devtools dimatikan, review filter harian, G13-2/G13-6/Q-27). Test `efn_setup_pos/tests/test_barcode_hardening.py`.
- **SEC-65 `efn_privacy` 19.0.1.3.0:** erasure mengosongkan `partner_id`/`member_ref_masked` (+token) di `efn.pos.otp.challenge` & `efn.pos.loyalty.reservation`, outbox `pending`/`hold` order partner → `skipped (member_erased)`; modul opsional dicek lewat registry.
- Tour `efn_loyalty_tour.js`: langkah cek baris poin kini menunggu (maks 5 dtk) — race RPC verify→reserve terlihat saat run.

## Validasi (container, DB sementara)
- Unit `odoo-test-s58.sh`: **274 test, 0 failed, 0 error, 0 baris ERROR**; `efn_setup_security` ter-install. `check-odoo-deps`: 12 modul, 0 terlarang.
- Tour `odoo-tour-s58.sh`: **15/15** (9 tour lama + 6 HttpCase `test_rpc_access`), 0 ERROR, 0 skip browser.
- ruff check + format (`services/odoo/addons`): lolos.

## Belum / sisa
- `/json/2`: HttpCase dengan bearer key menggantung di harness (kunci baris) → tidak dipakai; `/json/2` memakai `get_public_method` yang sama (dicakup gerbang registry). Uji `/json/2` nyata di staging.
- CI (di luar batas): `EFN_TOUR_MIN_TESTS` sebaiknya 9 → 15. `docs/04` §6 baris "efn.bff.client.get" perlu diganti `_efn_get`.
- Upgrade DB dev (efn_core 1.6, otp_gate 1.7, loyalty 1.1 dengan field/state baru, privacy 1.3, setup_pos 1.3) belum diuji di volume dev.
- SEC-60 lanjutan: field `restore_points` di kontrak reverse (BFF). SEC-61 (plafon/alert/rekonsiliasi) di BFF/ETL. SEC-63: kontrol kas/void/refund-metode masih di browser; aturan diskon buatan admin dinetralkan baru pada upgrade berikutnya. Order berpoin yang ditolak sync tertahan di POS sampai penukaran dihapus (belum ada tour kasus tolak).

# Gelombang 14 — `efn_pos_kds` (E-26, F-52..F-54) + validasi `efn_setup_pos` 19.0.1.4.0, 2026-10-08

Batas file: `services/odoo/addons/efn_pos_kds/**` (baru), `efn_setup_pos/**`, `efn_core/tests/test_rpc_surface.py`, `.github/workflows/ci.yml` (daftar modul + `EFN_TOUR_MIN_TESTS`), laporan ini. Tanpa commit; stack dev tidak disentuh (harness `odoo-test-kds.sh` / `odoo-tour-kds.sh` / `odoo-demo-kds.sh`, image & container `-kds`). Sesi sempat terputus dua kali (Docker Desktop restart); tidak ada perubahan parsial yang hilang.

## 1. `efn_setup_pos` 19.0.1.4.0
- Install + test (tanpa demo): `test_fnb_defaults` 3/3 lolos (preset delivery `available_in_self=False`, takeout `use_timing=False`, dine-in utuh).
- **Demo pertama kali dieksekusi**: `--with-demo` (opsi Odoo 19 terverifikasi) — `efn_setup_pos` + `efn_pos_kds` ter-install dengan semua file demo, 0 baris ERROR (lihat §4). Menjawab U5 QA untuk modul ini.
- Perbaikan: `tests/__init__.py` (ruff I001).

## 2. `efn_pos_kds` 19.0.1.0.0 (depends `pos_restaurant`, `bus`, `efn_core`)
Sesuai `docs/08` §7.3; README berisi ADR-013/023/031, risiko, uji manual, batasan, bagian Referensi (Cybrosys `pos_kitchen_screen_odoo` 19.0.1.0.0 LGPL-3 dipelajari, tidak ada kode disalin).
- **Model:** `efn.kds.station` (`pos.bus.mixin` + `pos.load.mixin`; kategori → routing, sub-kategori ikut induk, kosong = semua/expo maks. 1 per POS; mode `kds`/`kds_printer`(default)/`printer`; printer cadangan wajib terpasang di POS; warn/late PLACEHOLDER Q-19; user perangkat; heartbeat/`is_online` 60 dtk; `action_rotate_token`). `efn.kds.ticket` (+ `.line`) per (order, stasiun, pengiriman, course) dengan cap waktu tiap status, `wait_seconds`, `printed_offline`, kunci unik (order, stasiun, `sequence_no`), **tanpa** partner/nama/HP/alamat/`floating_order_name`/`general_customer_note`/`customer_note`/harga. `pos.order.line.efn_kds_sent_qty`, `pos.order.efn_kds_printed_offline`.
- **Hook server (satu tempat):** override `pos.order._process_order` (pemanggil tunggal `_process_saved_order` dari `sync_from_ui`, order baru & lama) karena butuh nilai sebelum write. Pemicu = isi `last_order_preparation_change` **tanpa `metadata`** berubah (temuan: `_ensure_to_keep_last_preparation_change` memperbarui `serverDate` di setiap sinkron, jadi perbandingan string mentah akan memicu di tiap sinkron) atau course baru di-*fire* (`fireCourse` memakai `byPassPrint` → preparation change tidak berubah). Delta dihitung dari baris DB; "terkirim" otoritatif = jumlah `qty_delta` tiket per (uuid baris, stasiun) → baris terhapus tetap menghasilkan tiket negatif. Baris belum ada di preparation change (belum dikirim kasir) dan course belum di-fire dilewati; induk combo tidak ditiket; order batal (`action_pos_order_cancel`) → tiket `cancelled`. Kegagalan KDS di-log dan di-rollback via savepoint, **tidak** menggagalkan sinkron order.
- **Bump/recall:** `efn_kds_bump` (maju boleh melompat; recall `ready→in_progress`, `served→ready` ≤ `efn.kds.recall_minutes`=10), `efn_kds_line_done` (semua baris → `ready`). Cek: user perangkat stasiun itu atau POS manager + `check_access` (record rule) + company; tulis `sudo` setelah cek.
- **Realtime:** kanal `"<uuid4>-EFN_KDS"`, payload `{type, ticket_ids, ts}`; isi lewat RPC `efn_kds_load`. `EFN_KDS_READY` ke kanal token `pos.config` (badge "Siap" di denah + notifikasi), `EFN_KDS_STATUS` saat layar kembali online. `access_token` stasiun ber-`groups` manager, tidak dimuat ke POS, keluar hanya dari halaman layar (`Cache-Control: no-store`). Heartbeat mengembalikan sidik token → layar reload otomatis setelah rotasi.
- **Layar KDS:** `/efn_kds/<id>` `auth='user'` (404 bila bukan perangkat stasiun/manager/company), bundle `efn_pos_kds.assets_kds` (= `point_of_sale.base_app` + file KDS, pola customer display). Kolom Baru/Dimasak/Siap, umur (jam server), warna warn/late, baris+atribut (server)+catatan, ketuk baris = selesai, bump besar, filter course, recall, beep, mode gelap; polling cadangan tiap heartbeat.
- **Akses perangkat:** grup `efn_pos_kds.group_kds_device`; record rule stasiun/tiket miliknya; rule `res.partner` (hanya dirinya + partner company) → kontak pelanggan tidak terbaca lewat RPC; `order_id/config_id/session_id` tiket ber-`groups` POS user; constraint `res.users` menolak perangkat + POS user/admin. 2FA & WireGuard = prosedur (README).
- **Cadangan printer (F-53):** patch `PosConfig.preparationCategories` (kategori stasiun → tombol *Order* tampil walau tanpa printer), `PosStore.printChanges` (printer cadangan stasiun `kds` dilewati hanya bila POS online + layar hidup menurut RPC `pos.config.efn_kds_station_status` sesaat sebelum cetak; gagal cek = cetak; dianggap terkirim bila ada printer yang dilewati), `sendOrderInPreparationUpdateLastChange` (offline: tetap cetak — standar CE menolak *Order* saat offline — lalu antre sinkron; order ditandai → tiket stasiun `kds` langsung `in_progress` "Sudah dicetak"). Routing/format printer standar, `updateLastOrderChange`, model `restaurant.*`, status `pos.order` tidak diubah.
- **Retensi:** autovacuum tiket > `efn.kds.retention_days` (90, PLACEHOLDER).
- **RPC_ALLOWLIST** (+7): `efn.kds.station.efn_kds_load/efn_kds_heartbeat/action_open_screen/action_rotate_token`, `efn.kds.ticket.efn_kds_bump`, `efn.kds.ticket.line.efn_kds_line_done`, `pos.config.efn_kds_station_status`.

## 3. Test
- `tests/test_kds.py` (tag `efn`, 25 test): routing kategori/sub-kategori, stasiun semua + constraint, baris tanpa stasiun, delta +2/+1/hapus −3, sinkron ulang, baris belum dikirim, tanpa preparation change, course fired lewat sinkron, combo anak lintas stasiun, batal order, cetak offline, mode printer, constraint printer, autovacuum, bump maju/recall berbatas, line done, perangkat stasiun lain/PT lain/tanpa grup/kasir ditolak, order & kontak tidak terbaca perangkat, kunci payload & bus tanpa data pelanggan, token uuid4 + rotasi mengubah kanal, token tak terbaca perangkat/kasir, heartbeat/status, constraint grup perangkat.
- `tests/test_kds_tour.py`: controller (tag `efn`, 2 HttpCase: perangkat hanya stasiunnya, kasir/anonim ditolak, token tidak bocor) + 4 tour `efn_tour` (Chrome nyata): POS meja 5 → nasi + Coca-Cola → *Order* → 1 tiket dapur + 1 tiket bar, `kds_printer` → printer dipanggil; `kds` layar mati → cetak cadangan + tiket "sudah dicetak"/`in_progress`; `kds` layar hidup → printer dilewati, tiket `new`; layar KDS perangkat: tiket tampil tanpa data pelanggan/stasiun lain → Mulai → Siap → Disajikan → recall tersedia.

## 4. Validasi (container, DB sementara)
- Unit `odoo-test-kds.sh` (12 modul + `efn_setup_security`): **304 test, 0 failed, 0 error, 0 baris ERROR** (efn_pos_kds: 25 unit + 2 HttpCase controller; gerbang RPC `test_rpc_surface` hijau dengan 7 entri baru). Baseline sebelum KDS: 277/277.
- `check-odoo-deps`: 13 modul, 0 dependensi terlarang.
- Tour `odoo-tour-kds.sh`: **19/19** (15 lama + 4 KDS), 0 ERROR, 0 skip browser → `EFN_TOUR_MIN_TESTS` 15 → **19**, `efn_pos_kds` ditambahkan ke `EFN_TEST_MODULES` dan `EFN_TOUR_MODULES` di `ci.yml`.
- Demo `odoo-demo-kds.sh` (`--with-demo`): `efn_setup_pos` + `efn_pos_kds` ter-install, 9 file demo, 0 ERROR.
- ruff 0.16.10 check + format `services/odoo/addons`: lolos.

## 5. Belum / sisa
- [B] Badge "Siap" di POS (bus websocket) tidak diuji di tour; payload `EFN_KDS_READY` diuji unit.
- [B] Kiosk self-order end-to-end (asumsi: `self_order_service` memanggil `updateLastOrderChange`), printer Epson HTTPS/DNS (Q-56), perangkat KDS nyata (Q-24), multi-perangkat POS offline.
- Atribut kustom (teks bebas klien) tidak ditampilkan. Status online untuk mode `kds` dicek per pengiriman (RPC), bukan via bus.
- ETL `efn_kds_ticket` → ClickHouse (KPI waktu tunggu) — efn-data-ai-engineer; `docs/04`/`docs/08` belum diperbarui dengan keputusan desain di atas (hook `_process_order`, pemicu tanpa `metadata`) — efn-lead/efn-odoo-functional.
- Upgrade DB dev (modul baru) belum dijalankan di volume dev. Untuk security-reviewer: rule `res.partner` perangkat, constraint grup, `try/except` hook (log + savepoint).

# Gelombang 15 — perbaikan review r8 (SEC-74..SEC-79 bagian Odoo), 2026-10-09

Batas file: `services/odoo/addons/**`, `.github/workflows/ci.yml` (hanya `EFN_TOUR_MIN_TESTS` 19 → 20), laporan ini. Tanpa commit; stack dev tidak disentuh (harness `odoo-test-g15.sh` / `odoo-tour-g15.sh`, image & container `-g15`).

## 1. SEC-75 — `efn_setup_pos` 19.0.1.5.0 (self-order dikunci)
- **Verifikasi image 19.0:** field `pos.config.self_ordering_mode` default `nothing`; `create`/`_prepare_self_order_splash_screen` tidak mengisi `mobile`; satu-satunya skenario yang menulis mode = `load_onboarding_kiosk_scenario` (`kiosk`). Anggapan PLAUSIBLE r8 ("restoran baru otomatis `mobile`") **tidak berlaku** di 19.0. Kunci tetap dipasang.
- `data/pos_self_order_lock.xml` (noupdate=0, jalan tiap install/upgrade): `ir.default.set('pos.config','self_ordering_mode','nothing')` + `_efn_normalize_self_order_mode` (config `mobile`/`consultation` → `nothing`, log INFO id).
- `models/pos_config.py`: constraint menolak `mobile`/`consultation` (create & write, termasuk lewat Settings) kecuali `efn.pos.self_order_mobile_allowed` benar. `models/ir_config_parameter.py`: sakelar dimatikan/dihapus → normalisasi seketika. Parameter hanya `base.group_system` (ACL CE) dan masuk `efn_audit` `CONTROL_SPECS` (selalu diaudit, log tidak ikut GC). Modul ini kini memuat sedikit Python (dicatat di README).
- Test `test_self_order_lock.py` (4): config restoran baru `nothing` + ir.default, mobile/QR menu ditolak (create & write), sakelar on → mobile boleh / off → dinormalkan, normalisasi DB lama + query pra-go-live = 0.

## 2. SEC-74 — akun perangkat KDS (`efn_pos_kds` 19.0.1.1.0)
- **Allowlist grup** (`models/res_users.py`): grup efektif ⊆ `base.group_user.all_implied_ids` ∪ {`group_kds_device`} (grup yang di-imply KE grup perangkat juga ditolak). Ditegakkan di `res.users` (constraint `group_ids`) dan `res.groups` create/write (`user_ids`/`implied_ids`/`implied_by_ids` → cek ulang semua user perangkat, dari relasi tersimpan).
- **Sandbox model** (`models/ir_rule.py`, override `ir.rule._compute_domain`, non-sudo, user perangkat): hanya `efn.kds.*`, `res.users` (diri), `res.partner` (diri + partner company, AND — tidak bisa di-OR rule grup lain), `res.company/lang/currency/country(.state)`, `res.groups(.privilege)`, `res.users.settings`, `decimal.precision`, awalan `ir.`/`bus.`/`base.`/`web_tour.`; model lain domain FALSE → produk & `standard_price`, `hr.employee(.public)`, `resource.resource`, Discuss/`mail.message`, loyalty, dst. tidak terbaca, termasuk model modul yang dipasang belakangan (tanpa migrasi). Dipilih karena rule grup biasa di-OR dengan rule `base.group_user` dan `hr` tidak ada di dependency `efn_pos_kds`.
- **Uji runtime** (`test_device_runtime_surface_no_customer_name`): sebagai user perangkat, iterasi SEMUA model terpasang ber-ACL baca (DB 12 modul + CE ±105 modul): model di luar allowlist = 0 record; model allowlist dengan many2one `res.partner` tidak menampilkan nama pelanggan (order ber-partner dibuat dulu) → **lolos** (menjawab PLAUSIBLE `display_name` r8 untuk set modul ini). Layar KDS (tour Chrome, login perangkat) tetap jalan di bawah sandbox.
- Rule perangkat XML dipindah ke luar `noupdate` + `migrations/19.0.1.1.0/pre-migrate.py` membalik flag `ir_model_data.noupdate` 4 xmlid (rule company tetap noupdate).
- Temuan saat implementasi: ORM 19 menerapkan `domain` field many2many saat mencari `device_user_ids in ids` → user yang baru dicabut dari grup tidak ketemu; pencarian stasiun untuk rotasi memakai tabel relasi langsung (SQL berparameter).

## 3. SEC-76 — kegagalan tiket
- `pos.order._process_order`: `SerializationFailure`/lock/deadlock (`PG_CONCURRENCY_EXCEPTIONS_TO_RETRY`) naik → Odoo retry; `UniqueViolation` (nomor tiket) → `ConcurrencyError` (retry); exception lain → log `EFN_KDS_TICKET_FAILED {json}` (logger `odoo.addons.efn_pos_kds.alert`, level ERROR: order/config/sesi/company id, stasiun+mode, `kds_only`, nama tipe exception; tanpa PII/pesan exception; traceback hanya DEBUG) + `pos.order.efn_kds_ticket_failed=True` (readonly, nilai dari klien dibuang; dikosongkan saat pembuatan berikutnya berhasil).
- POS (`pos_store_patch.js`): perubahan yang ditahan untuk stasiun `kds` disimpan per order; setelah sinkron di `sendOrderInPreparation`, bila tanda gagal → `printChanges` CE ke printer yang ditahan + notifikasi kasir sticky (juga untuk `kds_printer`: pesan "pastikan dapur menerima cetakan").
- Test: 4 unit (alert JSON tanpa nama/HP/catatan/pesan exception + tanda + pulih, tanda klien dibuang, serialisasi naik, unik → ConcurrencyError) + tour baru `EfnKdsPosTicketFailedTour` (layar hidup, server gagal → printer cadangan dipanggil + notifikasi).
- **Untuk devsecops:** rule Loki `count_over_time({service="odoo"} |= "EFN_KDS_TICKET_FAILED" [5m]) > 0` → on-call outlet (belum dibuat; di luar batas file).

## 4. SEC-77 — token kanal & kanal kasir
- Rotasi otomatis (`_efn_kds_rotate_token`, log tanpa token): stasiun `device_user_ids`/`active`/`company_id` berubah; user perangkat `active`/`group_ids`/`login`/`password`/`company(_ids)` berubah atau dihapus; dicabut dari grup perangkat lewat form Grup; **berkala** cron harian `EFN KDS: rotasi token kanal stasiun` (token > `efn.kds.token_max_age_days` = 7, PLACEHOLDER, min 1; nextcall 20:00 UTC). Field baru `token_rotated_at`.
- `EFN_KDS_READY` dan `EFN_KDS_STATUS` → kanal **partner kasir** (`_bus_send`, kanal yang hanya dilanggani sesi websocket ter-autentikasi user itu): penerima = pembuka sesi terbuka + user POS yang memanggil `pos.session.load_data` (override CE, dicatat di `efn_kds_listener_user_ids`, ber-`groups` manager), aktif, internal, grup POS user, company cocok. Payload + `config_id`; POS menyaring per config (`this.bus.subscribe`). Tidak ada lagi notifikasi KDS di kanal token `pos.config` (QR self-order/kiosk).
- `device_user_ids`, `last_seen`, `token_rotated_at` ber-`groups` POS manager → kasir tidak membaca akun tablet.
- `RPC_ALLOWLIST` +1: `("pos.session","load_data")` (override CE).

## 5. SEC-78 (Odoo) — `efn_audit`
- `audit_policy.mask_free_text`: HP Indonesia dengan/tanpa pemisah (`0812-3456-7890`, `+62 812 …`, `(+62) 812.…`, `628…`) → `62812****890`, email → `b***@domain`, NIK/NPWP 15–16 digit & NPWP berformat → `****123`; tanggal/jam tidak tersentuh. Dipakai `_efn_audit_write_purge_log` **sebelum** create, MAC, dan sink. Test baru di `test_audit_sink.py` (+ MAC valid atas teks ter-mask) dan `test_audit_tamper.py` (sakelar SEC-75 = parameter kontrol, `efn.pos.*` lain tidak).

## 6. SEC-79 — gerbang `http.route`
- `efn_core/tests/test_route_surface.py` (BaseCase, AST, tanpa DB): setiap `@http.route`/`@route` di `efn_*` (luar tests/migrations) wajib `(path, auth)` persis di `ROUTE_ALLOWLIST`; `auth` default `user`; `public`/`none`/`bearer` butuh `PREAUTH_REVIEWED`; `csrf=False` butuh `CSRF_EXEMPT_REVIEWED`; override tanpa path (`@http.route()`) dan path dinamis ikut dicatat & wajib allowlist; entri basi gagal; uji-diri scanner pada sumber sintetis. Allowlist saat ini: `/efn_kds/<int:station_id>` (user).

## 7. Validasi (container, DB sementara; detail angka di bawah)
- Unit `odoo-test-g15.sh` (12 modul + `efn_setup_security`): **337 test, 0 failed, 0 error, 0 baris ERROR** (sebelumnya 304; +33: efn_pos_kds 61, efn_core 61 termasuk 7 gerbang route, efn_audit 79, efn_setup_pos 16). Jenis WARNING sama dengan baseline G14 (tidak ada jenis baru).
- `check-odoo-deps`: 13 modul, 0 dependensi terlarang. Gerbang RPC (`test_rpc_surface`) dan route (`test_route_surface`) hijau.
- Tour `odoo-tour-g15.sh`: **20/20**, 0 ERROR, 0 skip browser (termasuk layar KDS login perangkat di bawah sandbox dan tour baru SEC-76) → `EFN_TOUR_MIN_TESTS` 19 → **20** di `ci.yml`.
- ruff 0.16.10 check + format + compileall `services/odoo/addons`: lolos.
- Iterasi: 3 kegagalan run pertama diperbaiki (dump bus memuat repr target partner; allowlist sempat ikut menerima grup yang di-imply ke grup perangkat; pencarian stasiun terfilter domain field m2m — lihat §2).

## 8. Belum / sisa
- [B] Upgrade DB lama (pre-migrate noupdate, `ir.default`, normalisasi self-order) diuji lewat unit/simulasi SQL, **belum** `-u` pada DB hasil versi sebelumnya (dev/staging).
- [B] Badge "Siap" lewat kanal partner kasir tidak diuji end-to-end websocket (payload & penerima diuji unit). Kasir yang tidak memanggil `load_data` dan bukan pembuka sesi baru menerima setelah memuat ulang POS.
- [B] Sandbox perangkat diuji pada set 12 modul CI; modul CE lain yang dipasang belakangan otomatis tertolak (default deny), tetapi bila suatu saat layar KDS butuh model baru, allowlist `DEVICE_MODELS` harus ditambah (kode).
- Akun perangkat sebagai user *share* (opsi lebih kuat r8) butuh ADR kecil. Rate-limit `/pos-self` Traefik, rule Loki `EFN_KDS_TICKET_FAILED` dan deteksi HP di `efn_audit_sink` = devsecops. Inventaris PDP v5 (SEC-79 bagian dokumen) = architect/lead.
- Cron rotasi berjalan 20:00 UTC; rotasi menyebabkan layar KDS memuat ulang (~detik). Mengubah daftar perangkat stasiun juga memuat ulang layar lain di stasiun yang sama.

---

# Gelombang 17 — E-28 `efn_online_order` + `efn_setup_online` (9 Okt 2026)

Kontrak: docs/04 §10 v5.0 (delta §10.4.4 + L-1..L-7 menang atas docs/08 §8.3) + **L-11 (§10.17)**: blok `channel` di katalog. Batas file: `services/odoo/addons/efn_online_order/**`, `services/odoo/addons/efn_setup_online/**`, `efn_core/tests/test_rpc_surface.py` (hanya 13 entri `RPC_ALLOWLIST`), laporan ini. Tanpa commit/push; stack dev & DB `efn_odoo` tidak disentuh (harness `odoo-test-g17.sh`/`odoo-tour-g17.sh`/`demo.sh`, image `-g17`, container & DB sementara, sudah dibersihkan). Sesi sempat terputus (Docker Desktop mati); dilanjutkan dari working tree.

## 1. Modul
- **`efn_online_order` 19.0.1.0.0** (depends `sale_stock`, `point_of_sale`, `mail`, `efn_core`): model `efn.online.channel`, `efn.online.outlet`, `efn.online.order`, `efn.online.refund`, `efn.online.event.outbox`, wizard `efn.online.handover.wizard`; field `product.template.efn_online_*`, `sale.order.efn_online_order_id`; constraint partner generik tanpa PII juga di `res.partner`. Grup `group_online_channel` tanpa ACL dokumen; record rule company di 5 model; ACL lengkap (staf baca saja; tulis state hanya lewat metode).
- 10 metode BFF + 3 metode staf (lihat README). `_efn_online_check_caller`: context klien dibuang **sebelum** operasi ORM (lang tak valid pun tidak dievaluasi), grup + `user_ids` + company, AccessError seragam, `channel_inactive`.
- Idempotensi: place (advisory lock + unik; `payload_sha256` tanpa kode ambil; `quote_hash` dihitung ulang dari `sale.order.new()` dan dari SO tersimpan), mark_paid/cancel/refund/refund_settled berkunci. UniqueViolation → `ConcurrencyError`.
- Bayar: FOR UPDATE, nominal ≠ payable/intent kedua → `review`, SO batal → `refund_required: full`, stok kurang → `stock_short`, konfirmasi dalam savepoint (total berubah → rollback + `review`), faktur + `invoice_cash_rounding_id` + `account.payment.register` di jurnal titipan (`_efn_register_payment` = titik ganti tunggal ADR-019).
- Refund: reversal faktur standar + qty disesuaikan, kumulatif ≤ dibayar per SO; batal setelah faktur = nota kredit penuh; batal dari UI SO (ops) = event `cancelled` `initiator=odoo` + refund.
- Outbox: TLS via helper `efn.bff.client` (https, CA internal, tanpa redirect) + `X-EFN-Service-Key` + `X-EFN-Signature` HMAC-SHA256 (`EFN_ONLINE_EVENT_HMAC_FILE`), tanpa fallback unsigned; urutan `seq`; 400/401/403/404/409/422 final; retry 1 m→1 j; 48 j → gagal + aktivitas; autovacuum `done` > 30 hari.
- Kode ambil: pbkdf2-sha512 (passlib), tidak di payload hash/log, dihapus saat final; wizard tidak raise saat salah (counter ter-commit), 5× → `review`.
- **L-11**: `efn_online_catalog` → `channel: {code, name, company_code, currency, outlets:[{code, public_name, pickup_enabled, prep_minutes, active}]}` dari `efn.online.outlet` (termasuk nonaktif), di setiap halaman. Test `test_catalog_channel_block_lists_all_outlets` + asersi di test demo `efn_setup_online`.
- **`efn_setup_online` 19.0.1.0.0** (depends `sale_stock`, `sale_loyalty`, `efn_online_order`, `efn_setup_company`): `api_key_duration` 90, partner generik + tim Super-App RTL/FNB, `ir.default loyalty.program.sale_ok=False` + `post_init_hook`; demo kanal RTL (outlet TOKO-01, gudang demo OL01, 25 menit), FNB nonaktif (FP kota), jurnal titipan, pembulatan Rupiah, 3 produk (PPN `tax_ST4`), user teknis tanpa API key.

## 2. Validasi (Odoo 19 nyata, container, DB sementara)
- Baseline 12 modul + `sale_stock` (auto-install `sale_loyalty`, `sale_mrp`, `sale_stock_product_expiry`; `pos_sale` tidak ter-install karena `sale_management` tidak ada): **337 test, 0 failed, 0 error, 0 ERROR** → tidak ada regresi dari `sale`.
- Penuh 14 modul (+ `efn_setup_security` install terpisah): **390 test, 0 failed, 0 error(s), 0 baris ERROR/CRITICAL** (efn_online_order + efn_setup_online menambah 53; `test_demo_channels` di-skip karena CI tanpa demo).
- Demo (`--with-demo`, efn_setup_pos + efn_setup_online, tag `/efn_setup_online,/efn_online_order`): **53 test, 0 failed, 0 error, 0 skip**.
- Tour (5 modul tour + `efn_online_order` → `sale_stock`/`sale_loyalty` terpasang): **20/20, 0 ERROR, 0 skip browser**.
- `check-odoo-deps`: 15 modul, 0 dependensi terlarang. ruff 0.16.10 check + format: lolos. Gerbang RPC (`RPC_ALLOWLIST` +13) & route (tanpa route baru) hijau.

## 3. Penyimpangan dari kontrak (dengan alasan)
1. `action_efn_ready` memakai `check_access('read')` + grup + company (bukan `check_access('write')`): staf gudang sengaja tidak diberi ACL tulis `efn.online.order` agar state tidak bisa diubah lewat ORM generik.
2. Delta `deleted` katalog dihitung dari produk company kanal/global yang berubah sejak `since` dan tidak lagi terpublikasi (maks 2 000, halaman pertama saja); bisa memuat id yang belum pernah dikirim (BFF mengabaikan).
3. Quote mengembalikan `issues` (`not_found`/`out_of_stock`/`order_limit`) alih-alih error; place mengubahnya jadi `not_found`/`out_of_stock`/`invalid_input`. `efn_online_status` mengembalikan `state:"absent"` untuk ref tak dikenal (konsisten dengan cancel).
4. `efn_online_stock` tidak mengembalikan produk di luar katalog kanal.
5. Event `paid` dan `refunded` (initiator `bff`) ikut dikirim (ada di daftar event §10.5; BFF memetakan/mengabaikan).
6. Temuan verifikasi 19.0: promosi otomatis `sale_ok=True` **tanpa** reward yang sudah diklaim tidak menambah diskon saat `action_confirm` (diuji: tetap `confirmed`). Guard total tetap dipasang; test regresi #37 mensimulasikan perubahan total lewat override `_action_confirm`.

## 4. Belum / [B]
- `/json/2` nyata lewat `odoo-rpc-gateway` + API key (HttpCase bearer menggantung di harness) — integrasi/QA smoke.
- Dua place paralel di dua cursor nyata (diuji simulasi UniqueViolation → ConcurrencyError).
- Reservasi stok (`confirm`) & delivery ditolak constraint; `restock` refund hanya aktivitas; Σ refund parsial bisa selisih Rp1 akibat pembulatan per nota kredit → ditolak `amount_mismatch`.
- `review` diselesaikan via BFF (`efn_online_cancel reason=review`) atau admin; tidak ada tombol "lanjutkan" (di luar 13 metode).
- Demo pembulatan pakai `biggest_tax` (tanpa akun selisih); akun titipan Holding & pemetaan akun = Finance (Q-2/Q-ON6).
- Temuan lain (bukan dari modul ini): demo `efn_setup_pos` gagal dimuat di DB `--with-demo` (barcode `2100001000004` bentrok dengan demo CE "Newspaper Rack") — CI tidak memuat demo jadi tidak terlihat; untuk efn-odoo-functional.
- Untuk devsecops/CI: tambah `efn_online_order efn_setup_online` ke `EFN_TEST_MODULES` dan `efn_online_order` ke `EFN_TOUR_MODULES` (tour tetap 20); mount `odoo_online_event_hmac` ke Odoo (`EFN_ONLINE_EVENT_HMAC_FILE`).

## Gelombang 17 — perbaikan review keamanan r10 (10 Okt 2026, L-16)
Batas file: hanya `efn_online_order/**` (tidak menyentuh `efn_setup_company/pos/mrp` yang sedang diubah paralel). Tanpa commit/push.
- **SEC-95**:
  - Batal SO online dari UI untuk bagian berbayar (`paid/confirmed/stock_short/ready/review` atau sudah ada pembayaran) hanya `sales_team.group_sale_manager`; grup dicek dengan user asli sebelum hook sudo.
  - Field `sale.order.efn_online_cancel_note` (min. 10 karakter, grup manager) wajib diisi. Teks hanya di chatter, tidak ke BFF.
  - Bagian dengan picking keluar `done` ditolak.
  - Event `cancelled` membawa `refund.initiator=odoo` dan `cancel_reason:"ops"`.
  - Permintaan lead: blok `refund` ber-initiator `odoo` membawa `initiator_uid`. `initiator_login` hanya dikirim bila cocok `^[A-Za-z0-9._-]{1,64}$` (regex BFF), jadi email tidak pernah terkirim. Field baru `efn.online.refund.initiator_user_id`.
  - `stock.picking._action_done` menolak validasi picking keluar SO online kecuali penanda di `cr.cache` diisi `_efn_handover` setelah kode valid. Context RPC tidak bisa memalsukannya.
  - Perilaku CE 19 (source image): tombol *Cancel* SO hanya `confirm=` JS generik tanpa cek grup/faktur. `action_cancel` hanya menolak SO `locked`. `_action_cancel` membatalkan faktur **draf** saja, faktur ter-posting tetap. Wizard mass-cancel juga lewat `action_cancel`. Jadi kontrolnya di hook efn.
- **SEC-99**: hash kode ambil dihapus saat `picked_up`, `cancelled`, masuk `review`, dan oleh cron pengingat. Cron menghapus `confirmed/ready` yang lebih tua dari `efn_online.pickup_code_max_days` (default 7 hari sejak siap/bayar) dan membuat aktivitas staf. Wizard lalu menjawab "kode kedaluwarsa".
- **SEC-101**: user teknis hanya `base.group_user` + `group_online_channel` (+ implied). Ditegakkan di constraint kanal, `res.users` (grup/company berubah), dan `res.groups` (implied/user berubah). Install `efn_setup_security` sesudahnya tetap lolos.
- **SEC-103**: wizard tidak melempar exception setelah membaca kode. Akses ditolak, company lain, kode salah, atau validasi gagal dijawab sebagai pesan; `code` selalu dikosongkan dan ter-commit.
- **Test baru**:
  - `TestEfnOnlineSecurityR10` (6).
  - `test_sec103_code_not_kept_when_validation_fails`.
  - `TestEfnOnlineInitiatorPayload` (3: uid+login aman; email → uid saja; refund BFF tanpa identitas).
  - Penyesuaian `test_ops_cancel_from_sale_order_ui` dan `test_handover_other_company_denied`.
- **Validasi**:
  - Modul online (run cepat sebelum tambahan initiator): 108 test, 0 failed.
  - Suite efn penuh 14 modul + `efn_setup_security`: 405 test, 0 failed, 0 error(s), 0 baris ERROR
  - Tour 20/20.
  - Demo `--with-demo`: 64 test, 0 failed, 0 skip.
  - ruff check/format lolos.
- **Untuk lead**: kunci tambahan `cancel_reason` (event `cancelled`) dan `refund.initiator_uid/initiator_login` belum tertulis di docs/04 §10.5. Parser BFF menerima kunci tambahan; `initiator_*` sudah dibaca BFF.

## Gelombang 17 — re-review G17: SEC-104 / SEC-105 (10 Okt 2026, L-19)
- **SEC-104**: semua penanda internal berbasis context diganti `cr.cache`.
  - `internal_scope`/`in_scope` di `efn_online_common`, dengan scope `CONFIRM`/`CANCEL`/`HANDOVER`.
  - Penanda lama `efn_online_internal` (confirm & cancel) dihapus.
  - `sale.order.write` state SO online di luar scope ditolak.
  - `_efn_handover` menolak SO batal, tanpa move pelanggan terbuka, atau yang sudah sebagian keluar (order -> `review`).
- **SEC-105**: guard pindah ke `stock.move._action_done` (move `sale_line_id` SO online -> lokasi customer/transit) dan menggantikan guard `stock.picking`.
  - `pos.order.line.create` menolak `sale_order_origin_id` SO online.
  - Modul jembatan baru **`efn_online_pos_sale`** (auto-install, depends `efn_online_order` + `pos_sale`): domain loader POS mengecualikan SO online; override `load_sale_order_from_pos` menolak SO online (+1 entri `RPC_ALLOWLIST`, override CE).
  - Alasan modul terpisah: override `pos_sale` hanya efektif di atas `pos_sale` dalam MRO, sedangkan `efn_online_order` tidak boleh menarik `pos_sale`/`sale_management`. Ini di luar batas file awal (folder baru); disetujui konteks tugas L-19 (b).
- **Test baru** (`TestEfnOnlineReReviewG17`, 7 + `efn_online_pos_sale` 1):
  - context `efn_online_internal`/kunci cache palsu tidak membuka guard batal;
  - write state langsung ditolak;
  - handover setelah picking dibatalkan -> review;
  - ubah tipe operasi lalu validasi ditolak;
  - pindah move ke picking manual ditolak;
  - parsial/backorder ditolak;
  - baris POS ber-SO online ditolak;
  - loader/`load_sale_order_from_pos` POS mengecualikan SO online.
- **Untuk devsecops/CI**: tambahkan `pos_sale efn_online_pos_sale` ke `EFN_TEST_MODULES` agar jalur POS teruji (menarik `sale_management`).

- **Validasi** (container, DB sementara):
  - Suite efn penuh 16 modul (+ `pos_sale` → `sale_management`, + `efn_online_pos_sale`), lalu `efn_setup_security`: **413 test, 0 failed, 0 error, 0 baris ERROR**.
  - Tour dengan `pos_sale` + `efn_online_pos_sale`: **20/20, 0 ERROR, 0 skip browser**.
  - Modul online tanpa `pos_sale`: **118 test, 0 failed** (test POS di-skip dengan benar).
  - ruff check/format lolos.

## Gelombang 17 — re-review akhir r10: SEC-110 (10 Okt 2026)
- **Kode** (`efn_online_order/models/sale_order.py`): guard tulis pada field kunci serah terima, di luar jalur internal `cr.cache` (`in_any_scope`: konfirmasi/batal/serah terima; context diabaikan).
  - `stock.move`: `sale_line_id`, `location_dest_id`, `location_final_id`, `picking_type_id`, `picking_id`. Juga memasang move asing ke baris SO online.
  - `stock.move.line`: `location_dest_id`, `picking_id`, `move_id`.
  - `stock.picking`: `location_dest_id`, `picking_type_id`. Diperlukan karena field move tersebut computed-stored dan ikut berubah lewat picking.
  - No-op (nilai sama) dan field lain tetap boleh.
- **Test** `TestEfnOnlineGuardedFieldsSec110` (5):
  - tiap field move ditolak, termasuk dengan context palsu;
  - field move line ditolak;
  - field picking ditolak;
  - memasang move asing ke baris SO online ditolak;
  - no-op/field lain boleh; serah terima wizard dan batal manager tetap jalan.
  - Dua test SEC-105 disesuaikan: tulis ORM kini ditolak, jaring `_action_done` diuji lewat perubahan langsung DB.

- **Validasi** (container, DB sementara):
  - Suite efn penuh (+ `pos_sale`, `efn_online_pos_sale`, lalu `efn_setup_security`): **418 test, 0 failed, 0 error, 0 baris ERROR**. Warning baru `no translation language detected` dari helper guard diperbaiki (`env._`), kini 0.
  - Tour (+ `pos_sale`, `efn_online_pos_sale`): **20/20, 0 ERROR, 0 skip**.
  - ruff check/format lolos.

---

# Gelombang 18 — E-30 (poin = alat bayar), E-31 (refund ke metode asal), E-34 (daftar member 17+) (12 Okt 2026)

Kontrak `docs/04 §9.9` v6 (K-18-1..K-18-8, L-23, L-24 BFF), fit-gap `docs/08 §9.2/§9.3/§9.5`, keputusan P-8. Branch `chore/wave-18`; tanpa commit/push. Folder `efn_setup_*` tidak disentuh (functional paralel). Validasi di container/DB sementara (harness `odoo-test-g17.sh`/`odoo-tour-g17.sh`); DB dev tidak disentuh.

## E-30 — `efn_pos_loyalty_ledger` 19.0.2.0.0
- `pos.payment.method.efn_is_loyalty_points` + constraint (jurnal bank, bukan kas, tanpa terminal/QR, tanpa split, satu aktif per company), dimuat ke POS; `pos.config.efn_loyalty_points_method` (id).
- Frontend: tukar poin (OTP `pos_redeem` tetap) -> baris **pembayaran** Poin EFN senilai `value_idr` (metode tidak tampil manual, numpad tidak mengubah nominal, hapus baris = batal + release); offline nonaktif; refund berpoin menambah pembayaran Poin EFN negatif proporsional (preview server kumulatif).
- Server saat sync (toleransi 0): tanpa reservasi / nominal beda / <= 0 / partner beda / > batas % total incl. pajak / kembalian dari porsi non-tunai -> `UserError`; refund: hanya negatif, hanya bila asal berpoin, tidak melebihi sisa poin asal; selisih alokasi > Rp1 -> reverse `hold` + review. Baris produk lama ditolak.
- Outbox: `commit` + `payment_amount` (L-24: `409 payment_mismatch` -> final, reservasi & order review — diuji); `reverse` + `points_restored_value` (int >= 0). Earn K-18-4 `Σ eligible × (1 − poin ÷ total incl)`; flag `earn_exclude_point_payment` dibaca dari `GET /v1/pos/loyalty/params` (cache, tanpa panggil BFF saat sync), fallback `ir.config_parameter`.
- Tutup sesi: selisih metode Poin EFN ditolak (`_validate_session`).
- Migrasi: produk diskon lama diarsipkan (data lama dibiarkan, belum ada produksi), filter audit pengganti E-24 dihapus + filter `efn_setup_pos` dipulihkan, hook E-24 dihapus (`migrations/19.0.2.0.0`). 4 tour Poin EFN diganti (redeem, OTP salah, batal [hapus baris + kembali], offline).
- **Kebutuhan data untuk functional (`efn_setup_pos`)**: per PT jurnal bank "Poin EFN" (tanpa rekening), akun liabilitas/kliring poin (PLACEHOLDER Q-F18-3) sebagai `outstanding_account_id`, metode "Poin EFN" (`efn_is_loyalty_points=True`, `payment_method_type='none'`, tanpa split) dimasukkan ke `payment_method_ids` config ber-redeem. Tanpa itu tombol tukar tersembunyi & reserve ditolak.

## E-31 — `efn_pos_refund_origin` 19.0.1.0.0 (baru, LGPL-3, ADR-031, README)
- Server: pembayaran negatif refund ⊆ metode order asal, ≤ dibayar bersih per metode − refund sebelumnya; retur tanpa struk tunai saja (`efn.pos.refund.unlinked_return_mode` cash|deny); QRIS statis dicatat atas metode asal.
- RPC `pos.config.efn_refund_origin_allocation` (alokasi proporsional kumulatif) — **+1 entri `RPC_ALLOWLIST`**. Frontend: hanya metode asal (retur tanpa struk: tunai), nominal otomatis.

## E-34 — `efn_pos_otp_gate` 19.0.1.8.0
- Purpose `pos_enroll`; tombol "Daftar Member" + dialog centang wajib usia 17+; server menolak selain `age_confirmed is True` (`age_confirmation_required`, tanpa panggil BFF); `age_confirmed: true` dikirim di request & verify; verify `{ok, enrolled}` tanpa token -> challenge `consumed`; Odoo tidak menyimpan usia.

## Validasi
- Suite efn penuh pola CI (+ `pos_sale`, `efn_online_pos_sale`, `efn_pos_refund_origin`, lalu `efn_setup_security`): **452 test, 0 failed, 0 error, 0 baris ERROR**; check-odoo-deps 17 modul 0 terlarang; tanpa warning baru dari kode G18.
- Tour (5 modul POS + online + pos_sale + E-31): **22/22, 0 ERROR, 0 skip** (20 lama/diganti + E-31 + E-34).
- ruff check/format lolos.

## Untuk lead / tim lain
- CI: tambah `efn_pos_refund_origin` ke `EFN_TEST_MODULES` & `EFN_TOUR_MODULES`; `EFN_TOUR_MIN_TESTS` 20 -> 22 (devsecops).
- `scripts/smoke/odoo_pos_smoke.py` masih memakai desain lama (`efn_loyalty_points_product`, produk Poin EFN) -> perlu diperbarui QA (di luar batas file).
- **Bug numpad baris Poin EFN (diperbaiki setelah fe56a07):** akar masalah = number buffer POS memproses tombol numpad secara tertunda (timeout); tombol yang ditekan saat baris poin terpilih (diabaikan) diterapkan ke baris BARU yang ditambahkan sesudahnya (Bank = Rp5) -> order kurang bayar -> Validasi diam (`isPaid()` false). Perbaikan (`payment_screen_patch.js` + `pos_order_patch.js`): baris Poin EFN tidak pernah menjadi baris terpilih (pemilihan diabaikan + notifikasi "nominal terkunci", dideseleksi setelah ditambahkan), input numpad saat baris poin/tanpa baris terpilih dibuang, dan `numberBuffer.capture()` sebelum menambah baris pembayaran baru. Langkah numpad dikembalikan di `EfnPosLoyaltyRedeemTour` sebagai regresi (cek: baris poin tidak terpilih, Bank = 40.000, lunas, struk). Validasi: modul (loyalty/otp/refund/core) 181 test 0 failed; tour 22/22 0 ERROR; ruff lolos.
- Belum: supervisor untuk retur tanpa struk (Q-F18-5); jurnal akrual liabilitas poin (Q-F18-3); refund order lama berbaris produk poin (manual).

---

# Review r11 — perbaikan sisi Odoo (SEC-113, 115, 116, 117, 118, 119, 122) (14 Okt 2026)

Dasar: `docs/reviews/SECURITY_REVIEW_2026-10-12_r11.md`, keputusan L-25 (`docs/04 §9.9`). Branch `chore/wave-18`, tanpa commit/push. `efn_setup_*` tidak disentuh (SEC-121 dikerjakan functional secara paralel).

## Verifikasi status PLAUSIBLE (Odoo 19 nyata, `/web/dataset/call_kw` sebagai kasir `pos_user`, sebelum perbaikan)
- **ACL `pos.payment`:** pada order paid, write `payment_method_id`, write `amount`, create, dan unlink semuanya **DITERIMA**. SEC-113 terkonfirmasi.
- **`is_change` dari payload sync:** pembayaran bank 200 ditambah tunai −100 `is_change: true` tersimpan `is_change=True`. SEC-116 terkonfirmasi. Catatan: core juga membuat baris kembalian dari `amount_return` payload.
- **`res.partner.bank`:** pemegang `base.group_partner_manager` bisa membuat rekening (**DIIZINKAN**). SEC-121 terkonfirmasi; perbaikannya milik functional.
- Setelah perbaikan, probe yang sama mencatat: SEC-113 semua ditolak, `is_change` palsu bernilai False. Probe SEC-121 masih DIIZINKAN karena fixture HttpCase memberi `base.group_partner_manager` secara langsung, bukan lewat grup sempit milik functional.

## Perbaikan
- **efn_core** `scope.py` (baru): penanda jalur internal per cursor (`internal_scope`, `in_scope`).
  - Disimpan sebagai atribut cursor. `cr.cache` tidak dipakai karena dikosongkan saat rollback savepoint di tengah sync.
  - Tidak bisa diisi lewat context RPC, dan bukan model sehingga tidak ada metode RPC baru.
- **SEC-113** (`efn_pos_refund_origin/models/pos_payment.py`): create, unlink, dan write field finansial pembayaran order non-draft ditolak, kecuali di `pos.order._process_order` (sync) atau oleh kode server ber-sudo. Untuk Poin EFN (`efn_pos_loyalty_ledger/models/pos_payment.py`) aturannya lebih ketat: berlaku juga untuk order draft, termasuk mengganti metode baris lain menjadi Poin EFN.
- **SEC-115** (`efn_pos_otp_gate` 19.0.1.9.0):
  - Penanda tahan lama `res.partner.efn_member_locked_at`, diisi server saat OTP diterima BFF (semua purpose, termasuk `pos_enroll`), saat reservasi redeem, dan saat earn non-skipped.
  - Klien tidak bisa menulis penanda ini (`AccessError`).
  - HP member hanya bisa diubah `base.group_system` (chatter + efn_audit) atau OdooBot. Manajer POS tidak lagi cukup.
  - Migrasi otp 19.0.1.9.0 dan loyalty 19.0.2.1.0 mengisi penanda dari riwayat.
  - "Event BFF": saat ini belum ada event member dari BFF ke Odoo.
  - Alur resmi ganti nomor (OTP nomor lama + baru) belum ada di Odoo.
- **SEC-116:** `_efn_recompute_change_flags` (draft & final, sebelum E-31). `is_change` hanya sah untuk tunai negatif dengan kumulatif ≤ min(Σ tender tunai, Σ tender − total baris), ditambah satu satuan cash rounding. Di luar itu `is_change` diset False dan pembayaran dinilai sebagai uang keluar.
- **SEC-117:** `SELECT … FOR UPDATE` pada order asal sebelum menghitung sisa, di `_efn_lock_origin` (refund_origin) dan `_efn_loyalty_lock_origin` (loyalty).
- **SEC-118:** commit `payment_amount = round(order._efn_points_paid())`.
- **SEC-119:**
  - Flag `efn_is_loyalty_points` (create/write) hanya oleh admin `base.group_system`, dengan log peringatan.
  - Bila metode sudah punya pembayaran: flag (dua arah), jurnal, dan arsip metode poin tidak bisa diubah siapa pun selain superuser.
  - Field tidak diberi `groups=`, karena POS memuatnya sebagai kasir.
  - Aturan audit `pos.payment.method` di `efn_setup_audit` disarankan ke functional.
- **SEC-122:** `efn_pos_refund_origin` diberi `auto_install: True` (19.0.1.1.0), sehingga terpasang otomatis di setiap DB yang punya `point_of_sale` + `efn_core`, tanpa bergantung pada modul setup functional.

## Test baru
- `efn_pos_refund_origin/tests/test_payment_integrity.py` (efn): SEC-113 ×3, SEC-116 ×5 (refund kartu dengan is_change/amount_return palsu ditolak, kembalian sah tetap), SEC-117 ×2 (spy urutan kunci + SQL), SEC-122.
- `efn_pos_refund_origin/tests/test_payment_integrity_rpc.py` (efn_tour, call_kw kasir): SEC-113, SEC-116, probe SEC-121.
- `efn_pos_loyalty_ledger`:
  - unit: SEC-113 Poin EFN, SEC-115 penandaan (OTP, reserve, earn; skipped tidak ditandai), SEC-117, SEC-118, SEC-119;
  - RPC kasir (`test_rpc_access.py`): HP member / penanda ditolak, buat pembayaran poin ditolak.
- `efn_pos_otp_gate`: penandaan saat `pos_enroll`, OTP gagal tidak menandai, manajer POS ditolak, kunci tetap berlaku setelah challenge dihapus, penanda server-only & idempoten. Fixture "manager" kini admin.

## Validasi
- Suite efn penuh pola CI (17 modul + `efn_setup_security`): **479 test, 0 failed, 0 error, 0 baris ERROR**. check-odoo-deps: 17 modul, 0 terlarang. Warning baru hanya log yang memang diharapkan (SEC-116/119).
- Tour/HttpCase `efn_tour`: **27/27, 0 ERROR, 0 skip** (22 tour + 5 test RPC kasir baru).
- ruff check/format lolos.

## Belum / catatan
- Migrasi 19.0.1.9.0 / 19.0.2.1.0 (SQL penanda) belum diuji lewat upgrade DB lama.
- SEC-117 hanya diuji dengan spy. Uji dua koneksi nyata tidak mungkin di TransactionCase.
- CI: `EFN_TOUR_MIN_TESTS` 22 → 27 (devsecops).

---

# Re-review G18: SEC-125/126/127/129 + L-26 (SEC-128) sisi Odoo (14 Okt 2026)

Dasar: bagian "Re-review G18" di `docs/reviews/SECURITY_REVIEW_2026-10-12_r11.md` dan L-26 (`docs/04 §9.9`). Branch `chore/wave-18`, tanpa commit/push. Stack dev tidak disentuh; semua uji memakai DB/container sementara.

## Fakta core CE 19 (dibaca dari image `efn-odoo:ci`)
- `pos.make.payment.check()` memanggil `order._process_saved_order(False)`. Akibatnya validasi efn sebenarnya sudah ikut berjalan di wizard. Wizard itu hanya untuk POS manager (ACL CE `access_pos_make_payment`).
- Jalur yang benar-benar terbuka sebelum perbaikan ada tiga:
  - `action_pos_order_paid` lewat RPC;
  - `write({'state': 'paid'|'done'|'invoiced'})`;
  - `create` order ber-state paid. Order baru dari sync memang dibuat langsung ber-state `paid` oleh `_process_order`.
- Ada dua celah tambahan:
  - `amount_paid` bisa ditulis kasir dan dipakai cek lunas wizard;
  - `relations_uuid_mapping` di payload sync bisa menulis field record lain berdasarkan uuid. Ini gadget SEC-126.

## Perbaikan
- **SEC-125** (`efn_pos_refund_origin`):
  - Transisi draft/cancel ke paid/done/invoiced hanya diizinkan di dalam `_process_saved_order` (penanda per cursor `POS_ORDER_SAVE_SCOPE`). Aturan ini berlaku untuk `write` dan `create`, juga untuk POS manager. Superuser/sudo dikecualikan.
  - `amount_paid` dihitung ulang dari pembayaran nyata sebelum cek lunas.
  - Loyalty: draft berpoin hanya dipertahankan bila partner sama dengan reservasi, setiap baris positif, dan jumlahnya = `value_idr` reservasi. Selain itu baris poin dihapus.
- **SEC-126:** penanda sync kini membawa token (id order lama / uuid order baru).
  - `_efn_check_sync_commands` menolak perintah `payment_ids`/`lines` `[1|2|3|4, id]` dan `[6,0,ids]` yang menunjuk record milik order lain.
  - Guard `pos.payment` (umum dan Poin EFN) memeriksa kepemilikan record yang disentuh, termasuk lewat `relations_uuid_mapping`.
  - Penanda sync loyalty yang lama dihapus.
- **SEC-127:** `efn_core.scope.touch_row` = `UPDATE <tabel> SET write_date = write_date WHERE id = …`. Dipakai `_efn_lock_origin` dan `_efn_loyalty_lock_origin`.
- **SEC-129:** `efn_pos_refund_origin` kini menjadi dependency `efn_pos_loyalty_ledger` dan `efn_pos_otp_gate`. Dipilih karena deklaratif dan sekaligus menjamin guard SEC-125/126 hadir; tidak perlu skrip migrasi yang mengubah state modul. Versi: refund_origin 19.0.1.2.0, loyalty 19.0.2.2.0, otp 19.0.1.10.0.
- **L-26:**
  - `commit` mengirim `order_goods_incl` (`round(abs(_efn_goods_incl_raw()))`, dikirim bila > 0); `reverse` mengirim `refund_goods_incl` (>= 0).
  - `409 restore_value_mismatch` pada reverse → `hold` (order `review` + aktivitas).
  - Aksi baru `action_efn_restore_approve` (POS manager, cek company, masuk `RPC_ALLOWLIST`, tercatat di chatter + log) mengirim ulang dengan `restore_approved: true`. Tolak = `action_efn_hold_cancel`.
  - `action_efn_hold_approve` menolak hold jenis ini, karena kirim apa adanya akan ditolak lagi.
  - Field baru `restore_approved` hanya bisa diisi aksi supervisor; kasir tidak punya ACL outbox.

## Verifikasi
- **Probe `call_kw` (HttpCase, tag efn_tour):**
  - Kasir: `action_pos_order_paid`, `write state`, dan `create` ber-state paid semuanya ditolak `UserError`.
  - Manajer `pos_admin`: wizard refund tunai atas order asal yang dibayar bank → `UserError` E-31.
  - Kasir `sync_from_ui` dengan perintah `[1]`/`[2]`/`[4]` atas pembayaran order lain → `UserError`; pembayaran asing tidak berubah.
- **Unit:** semua penolakan diuji bukan `AccessError`. `AccessError` adalah turunan `UserError`, dan ini sempat menutupi hasil palsu pada user uji tanpa `base.group_user`.
  - Refund backend + wizard;
  - `amount_paid` palsu;
  - perintah asing (termasuk `[3]`, `[6]`, `lines`, `relations_uuid_mapping`), perintah milik sendiri yang sah, dan guard di dalam penanda sync;
  - draft Poin EFN dengan nominal/partner yang tidak cocok;
  - skenario A (draft berpoin) dan C (reward ber-OTP yang ditambahkan lewat RPC);
  - L-26: payload, hold, setuju, tolak.
- **SEC-127 dengan dua koneksi PostgreSQL nyata** (`efn_core/tests/test_scope.py`, commit sungguhan):
  - snapshot B diambil lebih dulu → A touch + commit → B touch gagal `SerializationFailure`, lalu retry berhasil;
  - varian B menunggu kunci A → setelah A commit, B gagal serialisasi;
  - kontrol: `SELECT … FOR UPDATE` + commit → B lanjut tanpa error (membuktikan temuan).
- **SEC-129 (DB lama nyata):** addons 8291cfc di-install (otp + loyalty); `efn_pos_refund_origin` berstatus `uninstalled`. Setelah `-u efn_pos_otp_gate,efn_pos_loyalty_ledger` dengan kode sekarang, modul itu `installed` 19.0.1.2.0.
  - Migrasi r11 otp 19.0.1.9.0 dan loyalty 19.0.2.1.0 ikut berjalan tanpa error dan kolom `efn_member_locked_at` terbentuk. Datanya kosong, jadi isi backfill sendiri belum teruji.
  - Cek untuk runbook deploy / pra-go-live: `SELECT state FROM ir_module_module WHERE name='efn_pos_refund_origin'` harus `installed`.
- **Suite efn penuh:** 496 test, 0 failed, 0 error, 0 baris ERROR. check-odoo-deps: 17 modul, 0 terlarang. Warning baru hanya log penolakan/hold yang diharapkan.
- **Tour/HttpCase:** 29/29, 0 ERROR, 0 skip (27 + 2 probe baru).
- **ruff** lolos.

## Untuk lead / tim lain
- CI: `EFN_TOUR_MIN_TESTS` 27 → 29.
- Deploy: tambahkan cek `installed` di atas ke `deploy-validate` (devsecops).
- Residual SEC-119 (ditutup setelah commit 64e10d6): pada metode Poin EFN, `journal_id` dan `outstanding_account_id` hanya bisa diubah admin (`base.group_system`), dan dibekukan untuk semua non-superuser bila metode sudah punya pembayaran. Test: `test_sec119_points_accounting_mapping_admin_only_and_frozen`.

---

# G18-QA-01: kunci idempotensi event tanpa id lokal (15 Okt 2026)

Tanpa commit/push. Mengikuti kontrak BFF bf5c5f9: `KEY_RE` menerima `online:<uuid>:<event>:<seq>`, dedup memakai kunci stabil + `event_id` + sidik isi, dan event dengan `seq <= last_seq` diabaikan.

- **`efn_online_order` 19.0.1.1.0:**
  - **Kunci:** `idempotency_key` = `online:<platform_order_ref>:<event>:<seq>`, tanpa id lokal Odoo.
  - **`event_id`:** UUID v4 per baris outbox, dibuat sekali saat enqueue, disimpan dengan constraint unik, dan dikirim di payload. Retry membaca dari record sehingga `event_id` dan kunci yang dikirim sama persis.
  - **`seq`:** `max(event_seq + 1, detik sejak 2026-01-01 UTC)`. Ini pilihan paling aman: tidak ada panggilan jaringan di dalam transaksi perubahan state, dan tetap naik setelah DB dipulihkan meskipun `event_seq` ikut mundur. Alternatif ditolak: watermark dari BFF (BFF mati berarti state tidak bisa berubah) dan watermark lokal (ikut hilang saat restore).
    - Asumsi: jam server disinkron NTP. Event dalam detik yang sama memakai +1, sehingga seq bisa mendahului jam beberapa detik; aman selama restore lebih lama dari itu.
    - Muat 9 digit sampai ~2057.
  - **`409 event_conflict`:** outbox `failed` + pesanan `review` (kecuali sudah final/review); 409 lain tetap final biasa.
  - **Migrasi:** pre-migrate mengisi `event_id` (`gen_random_uuid`) sebelum constraint dibuat, dan mengganti kunci event `pending` ke format baru.
- **Loyalty POS:** kunci outbox release = `pos_release:<bff_reservation_id>` (`_efn_release_key`), bukan id lokal reservasi. Kunci earn/commit/reverse sudah memakai uuid order.
- **Test:**
  - payload/kunci/`event_id` (UUID v4, sama dengan record);
  - retry dengan `event_id` dan kunci yang sama;
  - `event_id` unik dan `seq` naik ketat;
  - simulasi restore (event & `event_seq` mundur) menghasilkan seq > maksimum yang terkirim;
  - `409 event_conflict` → review, 409 lain tidak mengubah state;
  - `efn_online_status` mengikuti `event_seq`;
  - kunci release loyalty = id BFF.
- **Catatan QA:** `scripts/smoke/online_smoke.py` (event sintetis langsung ke BFF) masih memakai `online:<nonce>:ready:1` dan `seq` 1. BFF tetap menerima format lama.

---

# CI PR #60: `test_sec99_pickup_hash_cleared_on_final_and_review_and_expiry` (18 Okt 2026)

- **Akar masalah:** "bom waktu" di fixture test, bukan bug zona waktu dan bukan bug produk.
  - `tests/common.py::pay()` mengirim `paid_at` tetap `2026-10-09T10:00:00Z`.
  - Kedaluwarsa kode ambil = 7 × 24 jam sejak `ready_at`/`paid_at`. Sejak 2026-10-16 10:00 UTC, order "fresh" (id 28) ikut kedaluwarsa.
  - Run lokal yang lulus terjadi sebelum batas itu.
- **Logika produk benar:** `_efn_expire_pickup_codes` membandingkan datetime UTC naif (`now - timedelta(days)`), tanpa tanggal kalender atau zona waktu. Kode tidak diubah.
- **Perbaikan test:**
  - `pay()` kini memakai waktu sekarang (UTC), dengan parameter `paid_at` opsional.
  - Test SEC-99 menyuntikkan `now` tetap (waktu run CI yang gagal).
  - Test baru menguji batas ±1 detik di 23:59:59/00:00 WIB dan 23:59:59/00:00 UTC, serta prioritas `ready_at` di atas `paid_at`.
- **Validasi:** suite modul online (efn_online_order, efn_setup_online, efn_online_pos_sale) 85 test, 0 failed; ruff lolos.

---

# G19: E-35 reservasi, E-36 promo, E-37a brand_code, L-34/L-35/L-37 (18 Okt 2026)

Branch `chore/wave-19`, tanpa commit. Kontrak docs/04 §11 (menang atas §10 dan docs/08 §10).
**Status: ditulis tanpa shell. Test BELUM dijalankan di Odoo 19 nyata.** Lead menjalankan suite `efn` di container.

## Modul & file
- `efn_online_order` 19.0.1.1.0 → **19.0.1.2.0**.
  - `depends` + `sale_loyalty` (CE).
  - `post_init_hook` (`hooks.py`) + `migrations/19.0.1.2.0/post-migrate.py`: `sale_ok=False` untuk program loyalty lama yang melanggar aturan online.
  - `models/efn_online_common.py`: regex kode promo/brand, `promo_codes_payload`, `promo_code_ref`, `like_escape`.
  - `models/efn_online_channel.py`: `brand_code`, default batas bayar 70, mode `confirm` diizinkan, mesin promo (quote dengan SO di savepoint yang di-rollback), L-35, L-37.
  - `models/efn_online_order.py`: place dalam satu savepoint + konfirmasi + reservasi penuh, `mark_paid` berbasis reservasi, `picked` saat siap, serah terima menandai semua move picked.
  - `models/loyalty_program.py` (baru): constraint §11.2 no. 7, default `sale_ok=False`.
  - Lainnya: `views/efn_online_channel_views.xml`, `README.md`.
  - Test: `tests/common.py` (helper `promo_codes`), `tests/test_online_api.py` (timeout 69–70, `brand_code` di blok channel, program #37 kini wajib pricelist dan reward ikut diklaim), `tests/test_online_g19.py` (baru).
- `efn_setup_online` 19.0.1.2.0 → **19.0.1.3.0**.
  - Demo: kanal ritel `confirm`, `brand_code`, 4 user teknis per kanal (L-37).
  - Test dan README diperbarui.
- `scripts/smoke/odoo_online_smoke.py`: 70 menit, `confirm`, promo `SMOKE10`, user dilepas dari kanal lain (L-37). `describe` ditambah `reserved`, `reserved_picked`, `reward_lines`.

## Perubahan respons json2 (untuk BFF)
- **`efn_online_quote(channel_code, outlet_code, fulfilment, lines, promo_codes=None)`**:
  - `promo_applied: [str]` dan `promo_rejected: [str]` selalu ada, berhuruf besar, tidak beririsan, gabungannya = kode ternormalisasi yang diminta.
  - Baris reward di `lines[]`: kunci yang sama dengan baris biasa ditambah `"reward": true`. `name` = nama program, `subtotal_incl ≤ 0`, `available: true`, maksimal 20 per grup. Baris biasa **tanpa** kunci `reward`.
  - `quote_hash` berubah hanya bila ada promo (kode urut + `reward_id`).
- **`efn_online_place`**: payload `promo_codes` opsional, masuk `payload_sha256` hanya bila tidak kosong. Bentuk hasil tidak berubah.
  - Mode `confirm`: SO `sale` sejak place.
  - Reservasi tidak penuh menghasilkan error **`efn_online:out_of_stock`** (UserError, bukan `issues`). Kode yang sama dengan G17; BFF sekarang memetakannya ke `409 out_of_stock`. Bila §11.1 menghendaki `cart_has_issues`, pemetaannya diubah di BFF.
  - Promo berakhir, kuota habis, atau kupon sudah terpakai menghasilkan `price_changed`.
- **`efn_online_mark_paid`**: bentuk tidak berubah. `short_lines.available` = qty yang masih tercadang.
- **`efn_online_catalog`**: `channel.brand_code` (`null` bila kosong).

## Test ditambah (`test_online_g19.py`, tag efn)
- **E-35:**
  - default kanal 70/`none`, `confirm` diizinkan;
  - place mengonfirmasi SO, move `assigned`, `free_qty` 10→7;
  - melebihi sisa stok: precheck `out_of_stock`, dan reservasi parsial di-rollback tanpa SO (jumlah `sale.order`/`efn.online.order`/`loyalty.card` tetap);
  - cron kedaluwarsa melepas reservasi, lalu bayar terlambat → `refund_required`;
  - batal pelanggan melepas reservasi;
  - `mark_paid` tanpa konfirmasi ulang;
  - stok dicabut dari luar (penyesuaian inventaris sebagai pengganti penjualan POS) → `stock_short` → cancel;
  - order `ready` (picked) tidak tercabut;
  - baris campuran ready → serah terima selesai;
  - regresi mode `none`.
- **E-36:**
  - promo otomatis hanya untuk pricelist brand A (kanal TSC tidak mendapatnya) dan total tidak berubah saat konfirmasi (#37);
  - quote berpromo tanpa jejak DB;
  - kode valid/tak dikenal/duplikat/huruf kecil;
  - kedaluwarsa dan kuota menghasilkan penolakan generik (respons & hash identik);
  - validasi input;
  - mode `none` menolak semua kode;
  - hash berubah bila promo berakhir → `price_changed`;
  - kupon sekali pakai: place kedua `price_changed`, batal → poin pulih;
  - kupon buatan Odoo (huruf kecil) yang diminta huruf besar (L-34);
  - kode milik brand lain ditolak;
  - L-35: diskon 100 % → kode ditolak;
  - bentuk baris reward;
  - beli X gratis Y;
  - `promo_codes` di `payload_sha256`;
  - kode tidak di-log asli;
  - constraint program (4 tipe terlarang, tanpa pricelist, pricelist non-online, wajib OTP, kosongkan pricelist), default `sale_ok` False, hook program lama.
- **E-37a:** regex, tidak unik, nilai di katalog.
- **L-37:** user yang sama di dua kanal ditolak.

## Belum diverifikasi / [B]
- Seluruh test di atas dan test lama **belum dijalankan**.
- **API `sale_loyalty` 19.0** dicek lewat WebFetch source (ringkasan, bukan baris per baris):
  - fungsi yang dipakai: `_try_apply_code`, `_apply_program_reward`, `_get_claimable_rewards`, `_update_programs_and_rewards`, `action_confirm`/`_action_cancel`;
  - field yang dipakai: `loyalty.rule.mode/code`, `loyalty.card.code/points`, `reward.multi_product`, `program.is_nominative`, `sale_ok` default True, dan `api.constrains` dengan callable.
- Yang belum pasti:
  - apakah `total_order_count` menghitung SO `sale` (test kuota);
  - pengembalian poin kupon saat `_action_cancel`;
  - urutan "diskon global lebih baik" bila kode dan promo otomatis sama-sama diskon % order. Kode bisa tercatat `applied` walau reward-nya diganti; test sengaja memakai potongan nominal.
- **Penjualan POS disimulasikan dengan `action_apply_inventory`.** Asumsinya `_free_reservation` dipanggil dan reservasi picked tidak dicabut. Belum dicek apakah quant boleh `reserved > quantity` tanpa error.
- `move.quantity` diasumsikan = qty tercadang. Rantai multi-langkah (pick/ship) tidak diuji.
- Error `LockNotAvailable` `sale_loyalty` dibiarkan naik (Odoo retrying). Dua place paralel di dua cursor tidak diuji.
- ruff belum dijalankan (gaya mengikuti `ruff format`, baris ≤ 120).

## Untuk lead / tim lain
- **QA:** `scripts/smoke/run-e2e-smoke.sh` baris 667 `o.odoo_so_draft_with_code_hash` mengharapkan `so_state == draft`. Di mode confirm nilainya `sale`; ganti ke `sale` + `reserved == true`.
- **BFF:**
  - `promo_applied` grup dikirim ulang saat place;
  - error place `out_of_stock` lihat catatan di atas;
  - baris biasa tidak membawa `reward: false`.
- **DB dev lama:** demo noupdate, sehingga user `efn_online_rtl` masih di 3 kanal. Buat ulang DB, atau smoke `setup` akan melepasnya dari kanal lain.
- **Upgrade:** `-u efn_online_order` ikut memasang `sale_loyalty` bila belum ada. Kanal lama tetap 25 menit/`none` sampai diubah lewat checklist (urutan: Odoo 70 dulu, baru BFF 3600).

---

# G19 — E-38 PoC enkripsi kolom PII Odoo (`efn_pii` 19.0.1.0.0), 18 Okt 2026

Builder (B) G19 (docs/04 §11.6). ADR-036 (Proposed) §8, docs/04 §11.5, §7.2. Laporan PoC lengkap:
`docs/testing/POC_E38_PII_ODOO.md`. Tidak ada file di luar `services/odoo/addons/efn_pii/**` dan `docs/testing/`
yang diubah (selain bagian ini). Modul **tidak** didaftarkan ke CI.

## Modul & file (baru)
- `services/odoo/addons/efn_pii/__manifest__.py`, `__init__.py`, `README.md` (status PoC), `static/description/index.html`.
- `models/keyring.py`: AES-256-GCM key ring (format blob docs/04 §7.2, teks `efnpii1:`+base64, AAD
  `efn:odoo:<tabel>.<kolom>:v1`), HKDF/HMAC blind index, pemuatan `EFN_ODOO_PII_*` dari file, log `EFN_PII_KEYRING`.
- `models/fields_pii.py`: `EfnPiiChar(fields.Char)` (M1): `convert_to_column_insert`, `convert_to_record`,
  `condition_to_sql`.
- `models/res_partner.py`: `efn_pii_protected` (satu arah, admin), redefinisi `phone/email/vat`,
  `efn_{phone,email,vat}_bidx`, `email_normalized`/`phone_sanitized` kosong, `_mail_track`, `copy_data`,
  create/write (penanda palsu, mode terkunci, enkripsi nilai lama), `_register_hook` (R-T18 + status start).
- `tests/common.py`, `test_pii_crypto.py`, `test_pii_partner.py`, `test_pii_overhead.py`,
  `tests/data/efn_pii_test_vectors.json` (vektor publik GCM TC14/TC16 + RFC 5869 A.3). Tag `efn`, `efn_pii_poc`.
  Kunci uji dibangkitkan `os.urandom` di folder tmp per test.

## API Odoo 19.0 yang diverifikasi (source GitHub `19.0`, 18 Okt 2026)
`_create` -> `convert_to_column_insert(stored[fname], self, stored)`; `_flush` -> `get_column_update(record)` ->
`convert_to_column_insert(..., validate=False)` (dirty dibersihkan sebelum konversi); `_fetch_query` ->
`_insert_cache` tanpa konversi; `mapped`/`filtered`/`_read_format`/`_export_rows` lewat `__get__`;
`DomainCondition._to_sql` -> `field.condition_to_sql(field_expr, operator, value, model, alias, query)` setelah
`=`->`in` (`OrderedSet`); `Field.__init_subclass__` `setdefault`; `Field._get_attrs` menggabungkan atribut bila
`isinstance`; `_field_to_sql` memeriksa akses field (karena itu kode memakai `field.to_sql`); `create`/`write`
memanggil `_check_field_access(field, 'write')`; `mail`: `phone tracking=2`, `email tracking=1`,
`email_normalized` compute `@api.depends(lambda self: [self._primary_email])`; `phone_validation`:
`_compute_phone_sanitized` depends `_phone_get_sanitize_triggers()`; `_mail_track(tracked_fields, initial_values)`.

## Belum diverifikasi
- **Test belum dijalankan** (sesi tanpa shell). Lead wajib run di container (perintah di laporan PoC §6): target
  0 failed, 0 baris ERROR/CRITICAL; ambil angka `EFN_PII_POC overhead`.
- ruff belum dijalankan (ditulis untuk line-length 120 dan urutan isort `services/odoo/ruff.toml`).
- Asumsi yang paling mungkin salah bila test gagal: `Field.to_sql(model, alias)` dipakai langsung di
  `condition_to_sql`; `load()` dengan kolom `.id` untuk update; `ir.qweb._render(view_id, values)`; `name_search`
  mengembalikan tuple `(id, name)`; `email_formatted` memakai email ternormalisasi; precommit tracking berjalan di
  `TransactionCase` (`self.env.cr.precommit.run()`).
- `_load_pos_data` asli, QWeb faktur, ekspor Coretax, domain klien POS: tidak diuji (di luar cakupan PoC).
- Vektor ber-AAD `efn:odoo:...` belum ada (butuh satu run untuk menghitung; perintah di laporan PoC §5). Sisi BFF
  test vector belum ditulis (milik efn-bff-engineer).
