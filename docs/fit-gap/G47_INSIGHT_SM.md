# G47 — Fit-gap Odoo CE 19 untuk EFN Insight supermarket per persona (`efn_insight_sm`)

Penulis: efn-odoo-functional, 21 Nov 2026. Dasar: `docs/04_SERVICE_CONTRACTS.md` §28.13 (v20.3, L-186..L-203),
ADR-041 §3.3–§3.4 (Proposed), `docs/design/insight-supermarket.md`, ADR-031 (tanpa modul pihak ketiga; OCA/Cybrosys
hanya referensi desain). Worktree `efn-platform-g47`, branch `feat/wave-47-insight-supermarket`, sejajar `main`
(G44, G45 #114, G50 #117 sudah masuk).

Dokumen ini **tidak** menulis kode. Isinya keputusan Standar vs Custom, koreksi kecil untuk kontrak, dan lingkup
minimal untuk efn-odoo-engineer.

Legenda status:
- **[V]**: diverifikasi di source `odoo/odoo` branch 19.0 lewat WebFetch (21 Nov 2026).
- **[R]**: diverifikasi di repo ini (worktree g47).
- **[B]**: belum terverifikasi. Penyebabnya perilaku runtime yang belum diuji atau source yang tidak terbaca.

Belum ada yang diuji di instance Odoo berjalan.

---

## 0. Ringkasan manajemen

- **Hampir semuanya sudah tersedia di Odoo standar.** Grup akses, tautan langsung ke dokumen Odoo, dan semua data yang
  dibaca ETL ada di Odoo CE 19.
- **Dua hal tetap harus dibangun.** CE tidak menyimpan "kategori yang dikelola seorang buyer", dan tidak punya tempat
  menyimpan definisi/ambang KPI per PT.
- **Ada satu temuan penting di layar pengaturan user Odoo 19.** Grup yang berada di bawah satu "privilege" tampil
  sebagai **satu pilihan saja**. Bila kelima persona ditaruh di satu privilege seperti biasa, admin tidak bisa memberi
  satu orang dua persona (mis. Direksi + Kepala toko). Lebih buruk lagi, menyimpan form user diam-diam **menghapus**
  persona kedua. Solusinya murah: satu privilege per persona.
- **Masalah yang sama sudah ada di tiga modul yang berjalan** (replenishment, kedaluwarsa, audit). Perbaikannya
  diusulkan terpisah.
- **Tiga koreksi kecil untuk kontrak ETL**, supaya angka tidak salah:
  1. "qty diterima" outlet bisa terhitung dua kali bila penerimaan outlet dua langkah;
  2. penanda baris reward di pesanan online tidak tersimpan di database, sehingga ETL wajib memakai kolom lain;
  3. nilai susut SMD hanya ada bila modul CK terpasang di database yang sama.

---

## 1. Tabel fit-gap

| # | Kebutuhan (kontrak §28.13) | Standar CE 19 | Cukup? | Keputusan | Status |
|---|---|---|---|---|---|
| F1 | 5 grup persona + `group_insight_kpi_admin` | `res.groups` + `implied_ids` + `res.groups.privilege` (`category_id`, `sequence`, `description`, `placeholder`) | Ya (data XML) | Data XML di `efn_insight_sm`. **Satu privilege per grup** (§2.1) | [V] model, [V] widget |
| F2 | Satu user boleh memegang beberapa persona | Widget `res_user_group_ids` di Odoo 19: setiap privilege = **field selection** (satu nilai). Grup tanpa privilege = checkbox "Extra Rights" | **Tidak** bila satu privilege | Satu privilege per persona. Grup di privilege yang sama tidak bisa dipegang bersamaan lewat UI | [V] |
| F3 | Grup persona sensitif tidak ikut sinkron jabatan (SEC-193) | — (mekanisme `efn_role`) | — | Tambah 5 xmlid ke `efn_role.models.role_policy.SENSITIVE_GROUP_XMLIDS`. xmlid yang belum terpasang otomatis dilewati | [R] |
| F4 | "Kategori dikelola" per user (`efn_insight_categ_ids`) | `product.category` tanpa field user. `product.template.responsible_id` (stock, `company_dependent`) = penanggung jawab aktivitas logistik **per produk** | Tidak | Gap kecil: field Many2many di `res.users` (kontrak) | [V] |
| F5 | Audit perubahan `efn_insight_categ_ids` | `efn_audit` mengaudit `res.users` sebagai **model kontrol** dengan daftar field bawaan (`group_ids, active, login, company_ids, company_id, share`). `efn.audit.rule` **unik per model** | Sebagian | Tambahkan field cakupan ke spesifikasi kontrol `res.users` di `efn_audit` (§3.2) | [R] |
| F6 | Definisi & ambang KPI per PT (39 kunci) | `ir.config_parameter` global (tanpa `company_id`/record rule/validasi tipe). `res.company` bisa diberi field, tetapi 39 kolom. Tidak ada fitur KPI per PT di CE | Tidak | Gap: model `efn.insight.sm.kpi` (kontrak) — **dikonfirmasi** (§4) | [V] / [R] |
| F7 | Tautan "Buka di Odoo" | Router web 19.0 `/odoo/...`: `action-<id|xmlid>`, `m-<model>`, `<model.ber.titik>`, path action, `<id>`/`new` | Ya | Pola per model (§5) | [V] |
| F8 | Login saat sesi Odoo habis tetap kembali ke tautan | `/odoo/<path:subpath>` `auth="none"`: tanpa sesi → 303 ke `/web/login?redirect=<full_path>` | Ya | Tidak butuh kode | [V] |
| F9 | Data ETL: rantai move DC → outlet, picking gabungan, `qty_received` PO, kupon online, nilai susut | CE `stock`, `purchase_stock`, `sale_loyalty`, `pos_loyalty`, `stock_account` | Ya, dengan koreksi | §6 (3 koreksi kontrak) | [V] sebagian, [B] runtime |
| F10 | Cakupan `wids` kepala toko | `res.users.efn_outlet_warehouse_ids` (`efn_assortment`) — sudah dipakai record rule `efn_replenishment` | Ya (sudah ada) | Satu sumber untuk Odoo dan Insight | [R] |

---

## 2. Grup persona

### 2.1 Temuan: privilege Odoo 19 = satu pilihan [V]

Sumber: `addons/web/static/src/webclient/res_user_group_ids_field/res_user_group_ids_field.js` dan
`res_user_group_ids_privilege_field.js` (19.0).

- Setiap privilege di `category.privilege_ids` menjadi field `type: "selection"`. Grup **tanpa** `privilege_id` menjadi
  checkbox di "Extra Rights" (`filter((group) => !group.privilege_id)`).
- Nilai awal selection = `privilege.group_ids.findLast((gId) => selectedIds.has(gId))`. Bila user memegang dua grup
  privilege yang sama yang tidak saling `implied`, hanya satu yang tampil.
- Saat ada perubahan, widget menulis `x2ManyCommands.set(selectedGroupIds)`. Daftar itu hanya berisi nilai selection
  (ditambah grup shadow dan checkbox extra). **Grup kedua di privilege yang sama dihapus dari user** pada perubahan
  pertama di widget hak akses.

Konsekuensi untuk G47: vektor kontrak A (`roles=["exec","store","user"]`) mustahil dipertahankan lewat UI bila kelima
persona berbagi satu privilege. Pemberian lewat XML/RPC tetap bisa, tetapi akan hilang begitu admin menyunting hak
user tersebut.

### 2.2 Desain yang direkomendasikan (data XML, tanpa kode)

| xmlid | Nama | Privilege (baru, satu grup per privilege) | `implied_ids` | Sensitif (efn_role) |
|---|---|---|---|---|
| `efn_insight_sm.group_insight_exec` | Insight SM: Direksi | `res_groups_privilege_insight_sm_exec` "Insight Supermarket: Direksi" | `efn_insight.group_insight_user` | ya |
| `efn_insight_sm.group_insight_category` | Insight SM: Category manager/buyer | `…_category` | idem | ya |
| `efn_insight_sm.group_insight_store` | Insight SM: Kepala toko | `…_store` | idem | **tidak** |
| `efn_insight_sm.group_insight_dc` | Insight SM: DC/logistik | `…_dc` | idem | ya |
| `efn_insight_sm.group_insight_marketing` | Insight SM: Marketing | `…_marketing` | idem | ya |
| `efn_insight_sm.group_insight_kpi_admin` | Insight SM: pengelola definisi KPI | `…_kpi_admin` | `base.group_user` saja (tidak implies persona) | ya |

Catatan desain:
- Semua privilege memakai `category_id` = `efn_insight.module_category_efn_insight`, yang sudah ada [R], dengan
  `sequence` 10..60. Hasilnya, satu blok "EFN Dashboard Insight" di form user berisi 1 + 6 dropdown: "Insight"
  (pengguna/semua brand) lalu satu dropdown per persona dengan pilihan "–" atau nama persona.
- Isi `description` privilege dengan kalimat pendek tentang hak data (mis. "Melihat margin & GMROI semua toko PT
  supermarket. Tidak memberi hak menyetujui apa pun."). Field ini ditampilkan widget [V pemakaian
  `privilege.description`].
- Field `comment` grup diisi dengan kalimat yang sama, untuk laporan review akses `efn_role`.
- **Jangan** memakai grup tanpa privilege (checkbox "Extra Rights"). Bagian itu hanya terlihat di mode developer
  [B untuk 19.0, perilaku 17/18] dan mudah terlewat saat review.
- `implied_ids` ke `group_insight_user` sudah cukup: menu "Dashboard Insight" dan gerbang 403 controller memakai
  `has_group(group_insight_user)` [R `controllers/main.py`]. Persona tidak perlu masuk ke `efn_insight`.

### 2.3 Lokasi: modul jembatan, bukan `efn_insight` — **setuju dengan kontrak (L-187)**

- `efn_insight` terpasang juga di DB/instalasi F&B. Persona `store` tidak bermakna tanpa
  `res.users.efn_outlet_warehouse_ids` (`efn_assortment`), dan `wids` tanpa `stock.warehouse.efn_is_dc`.
- `auto_install: True` dengan `depends` `efn_insight` + `efn_assortment`: terpasang otomatis di DB tunggal yang memuat
  keduanya. Ini sesuai prinsip satu database multi-company.

### 2.4 Hubungan dengan grup modul yang ada [R]

| Modul | Grup | Privilege | Hubungan dengan persona |
|---|---|---|---|
| `efn_replenishment` | `group_replenishment_store`, `_buyer`, `_admin` | satu privilege "EFN Replenishment" (kategori Supply Chain) | Hak **tindakan**. Persona `store` ≠ `replenishment_store`. Keduanya membaca cakupan outlet dari field yang sama, `efn_outlet_warehouse_ids` |
| `efn_expiry_monitor` | `group_markdown_manager`, `group_expiry_override` | satu privilege | Hak tindakan markdown/override |
| `efn_assortment` | `group_assortment_manager` | sendiri | Hak tindakan master assortment |
| `efn_campaign` | `group_campaign_user` ← `_approver`; `_bot` | satu privilege (Marketing) | Hak tindakan kampanye. Persona `marketing` hanya data |
| `efn_insight` | `group_insight_user` ← `group_insight_all_brands` | satu privilege (rantai) | Diwarisi persona lewat `implied_ids` |
| `efn_role` | penanda kasir/refund approver; sinkron jabatan | — | §2.5 |

**Persona tidak boleh meng-implies grup tindakan, begitu pula sebaliknya** (kontrak §28.13.2). Kombinasi yang wajar,
mis. "Kepala toko SMD" = `group_replenishment_store` + `group_insight_store` + POS user, dibuat sebagai **peran**
`efn.role` di `efn_setup_role` setelah Q-SM29 dijawab. Penggabungannya bukan lewat `implied_ids`.

### 2.5 Sinkron jabatan (`efn_role`) [R]

- `efn_role` hanya menyinkronkan peran `sync_from_job=True` yang **tidak** memuat grup sensitif.
  - Daftar sensitif = konstanta `SENSITIVE_GROUP_XMLIDS` di `efn_role/models/role_policy.py`, ditambah parameter
    `efn_role.sensitive_group_xmlids`. Parameter ini hanya bisa menambah dan merupakan parameter kontrol audit.
  - xmlid yang belum terpasang dilewati (`xmlid_group_ids`). Polanya sudah dipakai untuk
    `efn_insight.group_insight_all_brands` (SEC-193).
- **Rekomendasi:** tambahkan `efn_insight_sm.group_insight_exec`, `_category`, `_dc`, `_marketing`, `_kpi_admin` ke
  konstanta itu (efn_role naik versi patch). `group_insight_store` **tidak** ditambahkan, sesuai kontrak.
  - **Jangan** mengisi parameter `efn_role.sensitive_group_xmlids` dari data modul. Parameter itu satu string, sehingga
    data modul akan menimpa tambahan admin.
  - Pilihan konstanta tidak menambah dependency `efn_insight_sm` → `efn_role`.
- Persona `store` yang didapat dari jabatan tanpa `efn_outlet_warehouse_ids` menghasilkan halaman info `no_outlet`.
  Ini perilaku kontrak, bukan kebocoran.

---

## 3. Field `res.users.efn_insight_categ_ids`

### 3.1 Tidak ada padanan CE [V]

- `product.category` 19.0 tidak punya field user, `active`, maupun `company_id` [V `product/models/product_category.py`].
- `product.template.responsible_id` (stock) hanya mengatur penerima aktivitas logistik **per produk** dan bersifat
  company-dependent [V]. Field ini bukan kepemilikan kategori. Memakainya berarti mengisi ribuan produk dan bentrok
  dengan fungsi aslinya.
- Tidak ada konsep buyer per kategori di `purchase`. `purchase.order.user_id` adalah buyer per dokumen.

→ Gap dikonfirmasi. Struktur kontrak sudah tepat: Many2many `product.category`, tabel
`efn_res_users_insight_categ_rel(user_id, categ_id)`, label "Kategori dikelola (Insight)".

Rekomendasi rinci:
- Taruh di tab yang sama dengan "Outlet (kepala toko)" milik `efn_assortment`
  (`view_users_form_efn_outlets`, page `groups="base.group_erp_manager"`) [R], atau di page baru "Insight Supermarket"
  dengan grup yang sama. Widget `many2many_tags`.
- `res.users` hanya bisa ditulis `base.group_erp_manager` (ACL CE), jadi hak ubah sudah sesuai kontrak tanpa kode.
  **Jangan** menambah field ini ke `SELF_WRITEABLE_FIELDS`.
- **Koreksi kontrak (C-5):** §28.13.3 menulis "`cats` = id aktif". `product.category` tidak punya `active` [V], jadi
  cukup "id yang masih ada, urut naik". Kategori bersifat global (tanpa company), sehingga user bisa memilih kategori
  F&B. Itu aman karena `cats` v1 hanya filter default (L-189). Batas 50 tetap dicek pembuat token.

### 3.2 Audit: tambahkan ke spesifikasi kontrol `res.users` di `efn_audit` [R]

Fakta di repo:
- `efn_audit/models/audit_policy.py` `CONTROL_SPECS["res.users"]` = `("group_ids", "active", "login", "company_ids",
  "company_id", "share")`. Field yang tidak ada di model dilewati (`if f in model._fields`).
- `efn.audit.rule` punya constraint `unique(model_id)`. Aturan admin pada model kontrol hanya **menambah** field.
- **Belum ada** aturan `res.users` di repo. Akibatnya `efn_outlet_warehouse_ids` (G44), yang menentukan record rule
  replenishment dan `wids` Insight, saat ini **tidak teraudit**.

Opsi:

| Opsi | Cara | Kelebihan | Kekurangan |
|---|---|---|---|
| **A (direkomendasikan)** | `efn_audit` menambah `"efn_outlet_warehouse_ids", "efn_insight_categ_ids"` ke tuple `CONTROL_SPECS["res.users"]` | Field cakupan data = hak akses (semangat SEC-49). Log tidak ikut GC dan masuk sink. Tanpa dependency baru. Tidak memakan slot `efn.audit.rule` res.users yang unik | Nama field modul lain tertulis di `efn_audit`, tetapi dilewati otomatis bila modulnya tidak terpasang |
| B | `efn_insight_sm` membuat `efn.audit.rule` untuk `base.model_res_users` dengan `tracked_field_ids` kedua field | Tanpa menyentuh `efn_audit` | Mengunci slot unik `res.users`: modul lain yang butuh aturan `res.users` harus menyunting record ini dan bergantung pada `efn_insight_sm`. Upgrade modul pemilik dengan `(6, 0, …)` menghapus tambahan modul lain |

Test yang perlu ditambahkan: menulis `efn_insight_categ_ids` menghasilkan satu `efn.audit.log` dengan nilai lama/baru
berupa id. `res.users` termasuk `PERSONAL_MODELS`, jadi `res_name` = `res.users,<id>` [R].

---

## 4. Model `efn.insight.sm.kpi`

### 4.1 Model vs `ir.config_parameter` vs `res.company` — **kontrak dikonfirmasi**

| Opsi | Penilaian |
|---|---|
| `ir.config_parameter` | Ditolak. Global, tanpa `company_id`/record rule/validasi tipe. Kunci `efn.%` ikut aturan audit parameter, tetapi 39 × jumlah PT kunci string sulit dikelola user bisnis |
| 39 field di `res.company` | Ditolak. Setiap kunci baru = migrasi skema `res_company` (tabel inti yang dibaca semua modul). Hak ubah tidak bisa dipisah dari admin company. ETL membaca `res_company` lebar |
| Satu record per PT dengan 39 field bertipe (`efn.insight.sm.kpi.config`) | UX lebih baik (widget asli, tanpa parsing). Tetapi mengubah kontrak ETL/grant (`id, company_id, key, value, active, write_date`) dan test parity JSON per kunci. **Tidak sepadan** pada titik ini |
| **Baris kunci-nilai per PT (kontrak)** | Diterima. Sesuai grain `dim_sm_kpi_config` `(company_id, key)`. Kunci baru = tambah entri registri, tanpa migrasi skema. Audit per baris |

### 4.2 Rekomendasi struktur untuk engineer

- Field:
  - `company_id`: wajib, `index`, default `env.company`, `check_company`;
  - `key`: `Selection(selection=_selection_key)` dari `KPI_REGISTRY`, wajib, `readonly` setelah create;
  - `value`: Char, wajib;
  - `note`: Char, `size` ditegakkan 128 di constraint;
  - `active`.
- Field compute non-stored untuk UI: `value_type`, `default_value`, `allowed_range`/`allowed_choices` (teks bantuan
  dari registri). Field ini tidak masuk grant ETL.
- `models.Constraint("UNIQUE(company_id, key)")` (sintaks 19.0, sama dengan `efn_replenishment` [R]). Catatan: baris
  yang diarsipkan tetap memakan slot unik. UI "kembalikan ke default" = arsipkan, lalu aktifkan kembali (bukan create
  baru).
- `@api.constrains("key", "value")` memvalidasi tipe/rentang dari registri dengan kode galat
  `efn_insight_sm:kpi_<kode>`. Pasangan ambang divalidasi lintas baris per company (`warn < bad`, `good > warn`)
  dengan membaca nilai efektif (baris aktif atau default).
- Record rule global `[('company_id', 'in', company_ids)]` (pola `efn_replenishment` [R]).
- ACL: `group_insight_kpi_admin` CRUD. Tanpa baris untuk grup lain: persona membaca lewat ClickHouse, bukan Odoo.
  `base.group_system` sudah melewati ACL.
- Audit: `efn.audit.rule` `audit_rule_efn_insight_sm_kpi` (create/write/unlink, `capture_unlink_values`). Model baru,
  jadi tidak bentrok dengan constraint unik.
- `post_init_hook` membuat 39 baris default per company `efn_brand_code ∈ SM_CODES` (kontrak). Company SM yang dibuat
  setelah install tidak mendapat baris. Ini aman karena "baris tidak ada = default", dan bisa ditambah lewat UI.
- Menu: `stock.menu_stock_config_settings` → "Definisi KPI Insight (Supermarket)", `groups="group_insight_kpi_admin"`.
  Menu "Dashboard Insight" adalah `act_url` top-level, jadi anaknya tidak bisa dinavigasi [R `efn_insight_actions.xml`].
  View list `editable="bottom"` dikelompokkan per company, dengan kolom default & rentang.
- RPC surface: tidak ada metode publik baru. Constraint/hook berawalan `_` (`TestEfnRpcSurfaceStatic`).

### 4.3 Usulan penyesuaian kontrak

- **C-1 (`is_default`)**: §28.13.9 `v_sm_kpi_config.is_default` = "baris Odoo tidak ada/nonaktif". Karena
  `post_init_hook` membuat 39 baris, `is_default` akan selalu 0 dan tombol "i" tidak pernah menampilkan "default".
  Usul: `is_default = (baris tidak ada/nonaktif) OR (nilai = literal default)`. Literal default sudah ada di view,
  jadi biayanya hampir nol.
- **C-2 (SoD, opsional)**: `group_insight_kpi_admin` mengubah definisi fill rate/OOS yang menilai kinerja DC/toko.
  Usulan:
  - tambahkan aturan SoD `efn_role` (data `efn_setup_role`): `kpi_admin` ✗ `group_replenishment_store` /
    `group_insight_store`;
  - status: **saran**, menunggu Q-G47-1.

---

## 5. Pola URL deep link Odoo 19 ("Buka di Odoo")

### 5.1 Fakta router & server [V]

- `router.js` (19.0) memecah `/odoo/<seg>/<seg>/…`:
  - segmen angka atau `new` = `resId` (bila setelah bagian action/model) atau `active_id` (bila sebelumnya);
  - `action-<n>` = id action;
  - `action-<module.xmlid>` = xmlid action;
  - `m-<model>` = model;
  - segmen berisi titik = model;
  - selain itu = `path` action.
- `action_service.js`: state `{model, resId}` tanpa action → `ir.actions.act_window` `res_model`/`res_id`, view
  `form`. State `{action, resId}` → action dimuat lalu form `resId`.
- `/web/action/load` (`controllers/action.py`): `action_id` int, atau string bertitik → `env.ref` (harus
  `ir.actions.*`), atau tanpa titik → `search([('path','=',…)])`. Gagal → `MissingActionError`. Client lalu mundur ke
  stack sebelumnya atau ke aplikasi default.
- `/odoo/<path:subpath>` `auth="none"`: tanpa sesi → 303 ke `/web/login?redirect=<full_path>`, sehingga tautan tetap
  dibuka setelah login. Fragmen `#` tidak dikirim browser, jadi **jangan** pakai format lama `/web#id=…`.
- Hak akses tetap ditegakkan server: ACL + record rule. Kepala toko yang membuka usulan outlet lain melihat galat
  akses, bukan data.

### 5.2 Pola per model

Format umum: `{EFN_INSIGHT_ODOO_URL}/odoo/action-<xmlid action>/<id>`. Fallback generik:
`{EFN_INSIGHT_ODOO_URL}/odoo/<model>/<id>` (form default model, tanpa breadcrumb list).

| Model (id dari view) | URL yang dipakai `lib/odoo-links.ts` | Action & bukti | Status |
|---|---|---|---|
| `efn.replenishment.proposal` (`proposal_id`) | `/odoo/action-efn_replenishment.efn_replenishment_proposal_action/<id>` | action ada [R `efn_replenishment_views.xml:136`] | pola [V], xmlid [R] |
| `efn.expiry.alert` (`alert_id`) | `/odoo/action-efn_expiry_monitor.efn_expiry_alert_action/<id>` | [R `efn_expiry_alert_views.xml:87`] | [V]/[R] |
| `efn.lot.markdown` (`markdown_id`) | `/odoo/action-efn_expiry_monitor.efn_lot_markdown_action/<id>` | [R `efn_lot_markdown_views.xml:98`] | [V]/[R] |
| `efn.campaign` (`campaign_id`) | `/odoo/action-efn_campaign.efn_campaign_action/<id>` | [R `efn_campaign_views.xml:140`] | [V]/[R] |
| `purchase.order` (`order_id`) | `/odoo/action-purchase.purchase_rfq/<id>` | `purchase_rfq` path `purchase`, domain `[]`. Bisa ditulis `/odoo/purchase/<id>`. `purchase_form_action` (path `purchase-orders`) ber-domain `state='purchase'` → pager salah untuk RFQ draf | [V] |
| `stock.picking` (`picking_id`) | `/odoo/action-stock.action_picking_tree_all/<id>` | "Transfers", domain kosong, tanpa path | [V] |
| `stock.lot` (`lot_id`) | `/odoo/action-stock.action_production_lot_form/<id>` | path `lots`. Domain lokasi per company | [V] |
| `product.template` (`product_tmpl_id`, kontrak) | `/odoo/action-stock.product_template_action_product/<id>` | context `search_default_goods` (tidak memengaruhi form) | [V] |
| `product.product` (diminta lead; kontrak memakai template) | `/odoo/action-stock.stock_product_normal_action/<id>` | "Product Variants" | [V] |
| `stock.warehouse.orderpoint` (bila kelak dipakai) | `/odoo/action-stock.action_orderpoint_replenish/<id>` | `view_mode` `list,kanban,form`. **Jangan** `stock.action_replenishment` (server action, path `replenishment`) untuk satu record. `action_orderpoint` punya filter default `trigger=auto`, sehingga orderpoint terkelola (`manual`) tidak muncul di list | pola [V]; render form orderpoint [B] |
| Daftar `stock.scrap` (tanpa id) | `/odoo/action-stock.action_stock_scrap` (atau `/odoo/scraps`) | path `scraps` | [V] |
| Daftar log bias (tanpa id) | `/odoo/action-efn_replenishment.efn_replenishment_bias_log_action` | [R `:161`] | [V]/[R] |

Aturan untuk frontend:
1. `id` wajib bilangan bulat > 0, dibangun dengan template tetap per model (allowlist), tanpa string dari pengguna.
2. Pakai **xmlid action**, bukan id numerik (berbeda antar-DB) dan bukan `path` (bisa berubah antar-versi; xmlid juga
   bisa berubah, tetapi galatnya terdeteksi `MissingActionError`).
3. `target="_blank"` wajib dengan `rel="noopener noreferrer"`, agar URL Insight beserta filternya tidak bocor ke log
   referer Odoo.
4. Pengguna multi-PT: record company lain yang tidak aktif di pemilih company Odoo bisa memunculkan galat
   multi-company. Perilaku pindah company otomatis di 19.0 [B]. Untuk user yang hanya punya SMD, ini tidak terjadi.
5. Persona tanpa grup modul (mis. persona `marketing` tanpa `efn_campaign.group_campaign_user`) mendapat galat akses
   di Odoo. Teks tombol cukup "Buka di Odoo (butuh hak di Odoo)" atau tombol disembunyikan per persona di aplikasi.

---

## 6. Data CE yang dibaca ETL G47

### 6.1 Rantai DC → outlet (`fact_dc_fill_rate`)

Fakta [V `stock/models/stock_warehouse.py` `create_resupply_routes` / `_get_supply_pull_rules_values`]:
- Satu PT → transit = `company.internal_transit_location_id`.
- Leg rute resupply:
  1. bila DC tidak `ship_only`: DC Stock → DC Output (pick type, `make_to_stock`);
  2. DC Output (atau Stock bila `ship_only`) → transit (out type, `location_dest_from_rule=True`);
  3. transit → `outlet.lot_stock_id` (in type outlet, `make_to_order`).
- DC SMD = `pick_ship` [R G44], jadi ada tiga move per baris: pick, ship, receipt.
- `stock.move.move_dest_ids`/`move_orig_ids` memakai tabel `stock_move_move_rel(move_orig_id, move_dest_id)` [V].
- `stock.move.orderpoint_id` (Many2one, index) [V].
- `stock.move.quantity` tersimpan (compute store) [V].
- `location_final_id` tersimpan [V].

Temuan:
1. **`proposal.picking_id` = picking ship, bukan pick [B runtime].** `_efn_create_supply_document` mengambil
   `moves.picking_id[:1]` dari move berlokasi asal di gudang DC, urut id [R]. Urutan pembuatan pull-MTO adalah receipt →
   ship → pick, sehingga move DC pertama = ship (Output → transit). Artinya, "qty dikirim" memang keluar DC.
   - **Rekomendasi ETL**: jangan bergantung pada urutan id.
   - Ambil move `done` di `picking_id` beserta backorder-nya dengan syarat `location_dest_id.usage = 'transit'`.
   - Bila `picking_id` ternyata picking pick (Stock → Output), ikuti `move_dest_ids` satu langkah ke move ship.
   - Tambahkan test runtime di `efn_replenishment` (engineer) yang mengunci jenis picking yang disimpan.
2. **Koreksi kontrak C-3 (`qty_received` dobel)**:
   - Leg 3 tidak memaksa tujuan dari rule (`location_dest_from_rule` default). Untuk outlet dengan penerimaan 2/3
     langkah, move transit → *Input* lalu push Input → Stock tertaut lewat `stock_move_move_rel`. Kedua move ber-
     `location_dest_id.warehouse_id` = outlet [B detail push 19.0; kode rule V].
   - Rumus kontrak ("Σ quantity move `done` lanjutan … `location_dest_id` ber-`warehouse_id` = outlet, kedalaman ≤ 2")
     akan **menjumlah dua kali**.
   - Usul predikat: hanya move yang **melintasi** batas outlet, yaitu `location_dest_id.warehouse_id = outlet AND
     (location_id.warehouse_id IS DISTINCT FROM outlet)`. Kedalaman ≤ 2 tetap dipakai untuk menelusuri.
   - Outlet demo saat ini 1 langkah, jadi masalahnya belum muncul di demo, tetapi akan muncul di outlet nyata yang
     memakai QC/Input.
3. **Satuan**: `stock_move.quantity` dalam `stock_move.product_uom`, sedangkan `qty_approved` dalam UoM produk.
   ETL wajib mengonversi lewat `uom_uom`. Kolom faktor UoM 19.0 [B]. Orderpoint 19.0 punya `replenishment_uom_id` [V
   G45], jadi move bisa berbeda UoM.

### 6.2 Apakah CE menggabungkan move beberapa usulan ke satu picking? [V source; B runtime]

`stock.move._search_picking_for_assignation` 19.0 langsung mengembalikan picking kosong bila move **tanpa**
`reference_ids` (model baru `stock.reference`, tabel `stock_reference_move_rel`; `procurement.group`/`group_id` tidak
terlihat di 19.0) [V].

Orderpoint `_prepare_procurement_values` hanya mengisi `reference_ids` bila ada context `origins` [V].
`action_efn_approve` memanggil `_efn_create_supply_document` **per usulan** [R].

Akibatnya:
- antar-usulan tidak pernah digabung ke picking terbuka yang sudah ada;
- dalam satu panggilan, move dikelompokkan `_key_assign_picking` = `(reference_ids, location_id, location_dest_id,
  picking_type_id[, move_orig_ids.picking_id])` → satu picking per leg per usulan.

→ `picking_shared` seharusnya ≈ 0. Kolom tetap dipertahankan sebagai penjaga, untuk kasus approval batch kelak atau
picking yang dibuat manual. Ini **menyederhanakan** batasan "alokasi FIFO" di kontrak menjadi kasus pengecualian.

### 6.3 `purchase.order.line.qty_received` [V]

- `qty_received` compute **store=True** (`compute_sudo=True`, inverse) dalam UoM baris (`product_uom_id`).
  Isinya penjumlahan move `done` (`purchase_stock._prepare_qty_received`), dikurangi retur `to_refund`.
- `product_qty`, `price_subtotal` (store), `date_planned` (store), `qty_invoiced` (store) [V].
- `purchase.order.state` ∈ `draft|sent|to approve|purchase|cancel` (tanpa `done`; penguncian lewat boolean `locked`)
  [V]. `is_late` kontrak (`po_state = 'purchase'`) benar.
- `purchase.order.picking_type_id` tersimpan (purchase_stock) [V] → join ke warehouse DC bisa dilakukan.
- `from_orderpoint`: pakai `purchase_order_line.orderpoint_id IS NOT NULL` [V field, index `btree_not_null`] lalu join
  orderpoint terkelola `efn_replenishment`. **Tambahkan grant** kolom `orderpoint_id` di `purchase_order_line` dan
  kolom `stock_warehouse_orderpoint(id, warehouse_id, efn_replenishment_managed)` [R
  `efn_replenishment/models/stock_purchase.py`].
- Catatan: penerimaan DC 2 langkah → `qty_received` naik saat barang tiba di Input DC (move ber-`purchase_line_id`),
  bukan saat masuk Stock.

### 6.4 Kupon/voucher (redemption) [V]

| Kanal | Field | Tersimpan? | Catatan |
|---|---|---|---|
| POS (`pos_loyalty`) | `pos.order.line.coupon_id` → `loyalty.card` (index `btree_not_null`), `reward_id`, `is_reward_line` (Boolean biasa), `points_cost` | ya | Sesuai kontrak |
| Online (`sale_loyalty`) | `sale.order.line.coupon_id` → `loyalty.card` (`readonly`), `reward_id`, `points_cost` | ya | **`is_reward_line` compute non-stored** → ETL wajib memakai `reward_id IS NOT NULL`, bukan `is_reward_line` (**C-4**) |

Kanal online EFN = `sale.order` + `sale_loyalty` `_try_apply_code` [R `efn_online_order`]. Quote promo dijalankan di
savepoint yang di-rollback, jadi hanya SO terkonfirmasi yang tersisa. Filter `sale_order.state = 'sale'`.

Risiko [B]: bila pesanan online juga diteruskan menjadi `pos.order` (`efn_online_pos_sale`/`efn_online_kds_bridge`),
redemption bisa terhitung dua kali. ETL perlu aturan "satu sumber per pesanan". Cek alur sebelum `sm_campaign`
menambah kanal online.

Grant tambahan untuk redemption online: `sale_order_line` (`id, order_id, coupon_id, reward_id, price_subtotal`),
`sale_order` (`id, company_id, state, date_order, amount_total`).

### 6.5 Scrap SMD: `efn_waste_value` / `efn_warehouse_id`

- Kedua field **hanya ada bila `efn_ck_production` terpasang** [R]. Modul itu `depends` `mrp`, `stock_account`,
  `mrp_account`, `efn_kitting`.
  - Di DB tunggal produksi yang memuat PT F&B dengan CK, kolom ada dan **dihitung untuk semua scrap, termasuk SMD**:
    compute store pada create/`do_scrap`, tanpa syarat company/CK.
  - Di DB demo SMD tanpa CK, kolom **tidak ada**.
- `do_scrap` `efn_ck_production` mewajibkan alasan (`scrap_reason_tag_ids`) untuk semua company bila modul terpasang
  [R]. Itu justru baik untuk waste per alasan.
- CE 19 [V]:
  - `stock.scrap` tanpa field warehouse;
  - `location_id` tersimpan;
  - `stock.location.warehouse_id` tersimpan (kontrak);
  - tabel alasan default `stock_scrap_stock_scrap_reason_tag_rel(stock_scrap_id, stock_scrap_reason_tag_id)` (nama
    default ORM, cocok dengan query `efn_ck_reports` [R]);
  - `stock_account` 19.0 menyimpan nilai di **`stock.move.value`** (Monetary biasa, tersimpan, tanpa compute).
    Model `stock.valuation.layer` tidak dirujuk lagi.
- **Rekomendasi ETL (fallback G47)**:
  - `warehouse_id = COALESCE(s.efn_warehouse_id, sl.warehouse_id)` lewat join `stock_location` pada `s.location_id`;
  - `waste_value_cost = COALESCE(s.efn_waste_value, Σ |stock_move.value| move scrap state done, qty × standard_price)`.
  - `standard_price` di 19.0 = field company-dependent (jsonb). Cara baca SQL [B].
  - ETL perlu mendeteksi keberadaan kolom (`information_schema.columns`) agar job tidak gagal di DB tanpa CK.
  - Ini menjawab bagian teknis **Q-SM40**. Bagian bisnisnya tetap: Finance memutuskan basis nilai (valuasi CE vs biaya
    standar).
- Apakah scrap move SMD bernilai (`value` ≠ 0) bergantung pada valuasi kategori (perpetual/periodik) dan
  auto-install `stock_account` [B].

---

## 7. Rekomendasi minimal `efn_insight_sm` dan pembagian dengan `efn_insight`

### 7.1 `efn_insight` 19.0.2.0.0 (tetap di modul dasar)

- Builder token `eit2` + `eit1` (rollback `EFN_INSIGHT_TOKEN_VERSION`), konstanta `ESB_CODES`/`SM_CODES`/`PERSONAS`
  di `lib/insight_token.py`, aturan konsistensi, batas payload, halaman info `no_outlet`/`scope_too_large`, audit
  `ir.logging` v2.
- `models/res_users.py` dengan **satu** metode privat `_efn_insight_sm_scope()` → cakupan kosong
  (`{"personas": [], "codes": [], "cids": [], "wids": [], "cats": []}`). Ini model pertama di `efn_insight`, tetapi
  hanya `_inherit`, sehingga tanpa ACL baru. Kill switch `INSIGHT_SM_ENABLED` dicek di controller, bukan di hook,
  agar `efn_insight_sm` tidak bisa melewatinya.

### 7.2 `efn_insight_sm` 19.0.1.0.0

```
efn_insight_sm/
  __manifest__.py   depends: ["efn_insight", "efn_assortment", "efn_audit", "stock"], auto_install: True,
                    license LGPL-3, post_init_hook (seed KPI)
  __init__.py, hooks.py
  models/res_users.py           efn_insight_categ_ids + override _efn_insight_sm_scope() (aturan §28.13.3 butir 2)
  models/efn_insight_sm_kpi.py  model + KPI_REGISTRY (sumber: docs/contracts/insight_sm_kpi_defaults.json)
  security/efn_insight_sm_security.xml   6 privilege + 6 grup (§2.2) + record rule KPI (noupdate)
  security/ir.model.access.csv           kpi_admin CRUD
  data/efn_audit_rule_data.xml           audit_rule_efn_insight_sm_kpi
  views/res_users_views.xml              field kategori (page erp_manager)
  views/efn_insight_sm_kpi_views.xml     list editable + search + action + menu Inventory > Configuration
  tests/ (tag efn)                       vektor A/B/C, N2/N3/N5, no_outlet, privilege 1-grup, audit categ, KPI
                                         validasi/rentang/pasangan, record rule company, parity JSON
  README.md                              ADR-041 §3.4, L-186..L-203, R-S22/R-T22, keputusan klien Q-SM29
```

Tidak perlu:
- **controller baru**: tetap `/efn/insight/open` milik `efn_insight`;
- **record rule untuk persona**: persona tidak membuka dokumen Odoo baru;
- **ACL `res.users`**: field baru di model yang ACL-nya sudah ada.

### 7.3 Perubahan kecil di modul lain (di luar `efn_insight_sm`)

| Modul | Perubahan | Alasan |
|---|---|---|
| `efn_role` | + 5 xmlid persona/kpi_admin di `SENSITIVE_GROUP_XMLIDS` (patch) | SEC-193, §2.5 |
| `efn_audit` | + `efn_outlet_warehouse_ids`, `efn_insight_categ_ids` di `CONTROL_SPECS["res.users"]` (patch) + test | §3.2. Menutup juga celah audit G44 |
| `efn_setup_supermarket` (data, efn-odoo-functional) | 19.0.3.0.0 demo: persona di user demo + 2 user demo baru (Direksi, Marketing) + `efn_insight_categ_ids` contoh. `depends` + `efn_insight_sm` | Dikerjakan **setelah** modul engineer ada (ref xmlid). Pemetaan nyata menunggu Q-SM29 |
| `efn_setup_role` | Template peran SMD (kepala toko, buyer/category, DC, marketing, direksi) | Menunggu Q-SM29. Jangan otomatis |

Tidak dibuat modul `efn_setup_insight_sm` terpisah. Tidak ada konfigurasi CE yang perlu dipasang selain yang di atas,
dan data persona contoh termasuk demo `efn_setup_supermarket`.

---

## 8. Audit modul yang ada: privilege berisi grup independen [V widget + R data]

| Modul | Grup dalam satu privilege yang tidak saling `implied` | Dampak di UI | Rekomendasi |
|---|---|---|---|
| `efn_replenishment` | `group_replenishment_store`, `_buyer`, `_admin` | Admin replenishment + buyer (kombinasi wajar) tidak bisa dipegang bersamaan. Menyunting user menghapus salah satu | Pisahkan privilege per grup, **atau** `_admin` implies `_buyer` bila disetujui (SoD: admin ≠ store tetap) |
| `efn_expiry_monitor` | `group_markdown_manager`, `group_expiry_override` | Supervisor yang juga markdown manager kehilangan satu grup | Pisahkan privilege |
| `efn_audit` | `group_auditor_personal`, `group_manager` (keduanya → auditor) | Auditor DPO yang juga mengelola aturan kehilangan satu grup | Pisahkan privilege `group_manager` |
| `efn_role` | `group_role_cashier`, `group_refund_approver` | Kombinasi ini justru dilarang SoD-01. Efek UI tidak merugikan, tetapi diam-diam | Biarkan. Catat di README |
| `efn_campaign` | `group_campaign_user` ← `_approver`; `_bot` | Bot = akun teknis tunggal, aman | Tidak perlu |

Ini **bukan duplikasi fitur standar**, melainkan cacat konfigurasi yang muncul di 19.0, karena model privilege baru.
Tidak ada bagian `efn_insight`/`efn_assortment`/`efn_replenishment` yang menduplikasi fitur CE untuk kebutuhan G47.

---

## 9. Usulan perubahan kontrak (untuk efn-lead/efn-architect)

| ID | Bagian | Perubahan |
|---|---|---|
| **C-1** | §28.13.9 `v_sm_kpi_config.is_default` | `= baris tidak ada/nonaktif OR nilai = default`, karena hook membuat semua baris |
| **C-2** | §28.13.2 | Satu `res.groups.privilege` per grup persona/kpi_admin (temuan widget §2.1). Tambah 5 xmlid ke daftar sensitif `efn_role` |
| **C-3** | §28.13.8 `fact_dc_fill_rate.qty_received` | Hanya move yang melintasi batas outlet (`location_id.warehouse_id ≠ outlet`). Hindari dobel di penerimaan 2/3 langkah. `qty_shipped` = move `done` ber-`location_dest_id.usage='transit'` di `picking_id`+backorder |
| **C-4** | §28.13.8 `fact_campaign_result` (online) | Reward line online = `sale_order_line.reward_id IS NOT NULL` (bukan `is_reward_line`, non-stored). SO `state='sale'`. Grant `sale_order(_line)`. Aturan satu-sumber bila online juga menjadi `pos.order` |
| **C-5** | §28.13.3 `cats` | "id aktif" → "id yang ada". `product.category` tanpa `active` |
| **C-6** | §28.13.8 `fact_scrap` (G47) | `COALESCE(efn_warehouse_id, stock_location.warehouse_id)` dan `COALESCE(efn_waste_value, Σ|stock_move.value|, qty×standard_price)` dengan deteksi kolom. Grant `stock_scrap`, `stock_location(id, warehouse_id)` |
| **C-7** | §28.13.8 `fact_dc_purchase_line.from_orderpoint` | Sumber = `purchase_order_line.orderpoint_id` ⋈ `stock_warehouse_orderpoint.efn_replenishment_managed`. Tambah grant kolom |
| **C-8** | §28.13.11 | Pola URL §5.2 menggantikan "belum terverifikasi". `rel="noopener noreferrer"`. `purchase.order` lewat `purchase_rfq` |
| **C-9** | §28.13.15 | Pindahkan dari "belum terverifikasi" ke [V]: `qty_received` tersimpan, `sale.order.line.coupon_id` tersimpan, pola URL. "Penggabungan picking" → [V] source (tanpa `reference_ids` tidak digabung), runtime [B] |

---

## 10. Pertanyaan klien

- **Q-G47-1** (Ops/Finance): Siapa pengelola definisi KPI (`kpi_admin`)? Bolehkah ia juga kepala toko/DC yang dinilai
  KPI itu? Usul: tidak (SoD). Pemegangnya Finance/BI.
- **Q-G47-2** — *diputuskan pemilik proyek 21 Nov 2026*: outlet SMD **dua langkah (Input → Stock) sejak awal**.
  - Diterapkan di `efn_setup_supermarket` 19.0.3.0.0 (demo + migrasi + test rantai runtime).
  - Konsekuensinya, **C-3 wajib**: `qty_received` = move receipt transit → Input, yang melintasi batas outlet.
- **Q-SM29** (tetap): penetapan persona per jabatan. Default usulan: Direksi → exec; Category manager → category +
  `efn_insight_categ_ids`; Kepala toko → store + outlet; Manajer DC/buyer → dc; Marketing → marketing.
- **Q-SM40** (tetap, bagian bisnis): basis nilai susut SMD (valuasi CE vs biaya standar) bila `efn_ck_production`
  tidak terpasang.

---

## 11. Referensi desain (bukan dependency, ADR-031)

| Kebutuhan | Referensi | Branch 19.0 | Lisensi | Ide yang diambil / tidak |
|---|---|---|---|---|
| Persona = paket grup | OCA `server-backend/base_user_role` (+ `_company`, `_history`) | ada [V daftar direktori] | [B] (umumnya LGPL-3, belum dicek manifest) | Peran = himpunan grup + riwayat. Sudah ditiru `efn_role`. Persona G47 tetap grup biasa |
| Definisi KPI configurable | OCA `mis-builder/mis_builder` 19.0.1.2.2 | ada [V] | [B] (dikenal AGPL-3, belum dicek manifest) | KPI sebagai data yang diedit user, bukan kode. **Tidak** diambil: ekspresi bebas (risiko injeksi/performa). G47 memakai registri tertutup |
| Cybrosys | — | — | — | Tidak ada modul relevan yang diverifikasi |

---

## 12. Belum terverifikasi

- Perilaku runtime semua temuan §6: jenis picking yang disimpan `proposal.picking_id`, push Input → Stock outlet 2
  langkah di 19.0, konversi UoM move, nilai `stock.move.value` pada scrap SMD, pembacaan SQL `standard_price` jsonb.
- Section "Extra Rights" hanya terlihat di mode developer pada 19.0.
- Pindah company otomatis saat membuka deep link record PT yang tidak aktif.
- Render form `stock.warehouse.orderpoint` dari deep link.
- Lisensi `base_user_role` dan `mis_builder` (manifest tidak dibaca).
- Double-count redemption online ↔ POS (`efn_online_pos_sale`).
- Tidak ada instance Odoo yang dijalankan. Tidak ada kode yang ditulis.
