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
