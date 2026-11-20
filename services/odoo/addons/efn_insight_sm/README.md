# efn_insight_sm — Insight Supermarket: persona, cakupan outlet, definisi KPI per PT (G47)

**Versi:** 19.0.1.0.0 · **Lisensi:** LGPL-3 · **Depends:** `stock`, `efn_insight`, `efn_assortment`, `efn_audit`
(CE + modul EFN; tanpa modul pihak ketiga — ADR-031) · **auto_install:** ya

## Tujuan
Modul jembatan (L-187) untuk EFN Insight supermarket per persona:
1. **Grup persona** (hak *data* di Insight): Direksi (`exec`), Category manager/buyer (`category`), Kepala toko
   (`store`), DC/logistik (`dc`), Marketing (`marketing`), plus **pengelola definisi KPI** (`kpi_admin`).
2. **Cakupan token `eit2`**: menimpa hook privat `res.users._efn_insight_sm_scope()` dari `efn_insight`.
3. **Kategori dikelola** per user (`res.users.efn_insight_categ_ids`) untuk filter awal persona `category`.
4. **Definisi & ambang KPI per PT** (`efn.insight.sm.kpi`, 39 kunci) yang dibaca ETL `sm_dims` → `dim_sm_kpi_config`.

Dipisah dari `efn_insight` karena `efn_insight` juga terpasang di instalasi F&B tanpa modul supermarket; persona
`store` tidak bermakna tanpa `efn_outlet_warehouse_ids` (`efn_assortment`).

## ADR, kontrak, fit-gap
- ADR-041 §3.3–§3.4 (Proposed). Kontrak `docs/04_SERVICE_CONTRACTS.md` §28.13 (v20.3, L-186..L-203; KPI §28.13.8c
  L-200/L-201). **Fit-gap `docs/fit-gap/G47_INSIGHT_SM.md`** menang untuk detail Odoo (perubahan kontrak C-1..C-9
  menunggu architect menyerapnya ke docs/04).
- File normatif default KPI: `docs/contracts/insight_sm_kpi_defaults.json` (dibuat efn-data-ai-engineer).

## Keputusan desain
| Topik | Keputusan |
|---|---|
| Privilege | **Satu `res.groups.privilege` per grup** (kategori `efn_insight.module_category_efn_insight`, sequence 20..70). Widget hak akses Odoo 19 menampilkan satu pilihan per privilege dan menyimpan `Command.set`; bila persona berbagi privilege, user tidak bisa memegang dua persona dan persona kedua terhapus saat form user disimpan (fit-gap §2.1, C-2). |
| Implied | Persona → `efn_insight.group_insight_user`. `kpi_admin` → `base.group_user` saja (tidak memberi dashboard). Persona **tidak** meng-implies grup tindakan (replenishment/expiry/campaign/assortment) dan sebaliknya; gabungan dibuat lewat peran `efn_role` (Q-SM29). |
| Sensitif | `efn_role.SENSITIVE_GROUP_XMLIDS` + exec/category/dc/marketing/kpi_admin (tidak ikut sinkron jabatan). `store` tidak sensitif. |
| **Q-G47-1 (pemilik proyek, 21 Nov 2026)** | `group_insight_kpi_admin` **boleh diberikan ke user mana pun** lewat form user (privilege sendiri). **Tidak ada** pembatasan SoD di kode (usulan C-2 fit-gap tidak dibangun). Kendali = audit `efn_audit` + record rule multi-company + daftar sensitif `efn_role`. |
| Cakupan `wids` | `SMC` = company di `res_company_users_rel` user, aktif, `efn_brand_code ∈ SM_CODES` (`all_brands` tidak memberi SMC). Persona exec/category/dc/marketing → `{0} ∪` semua warehouse aktif SMC. `store` → `efn_outlet_warehouse_ids ∩` warehouse aktif SMC (tanpa 0). `store` tanpa outlet sah → tidak masuk `roles`, flag `no_outlet` (halaman info bila tidak ada kode lain). |
| `cats` | `efn_insight_categ_ids` yang masih ada, urut naik, hanya bila `category` masuk `roles` (C-5: `product.category` tanpa `active`). Batas 50 dicek controller (`scope_too_large`). |
| Audit kategori/outlet | Lewat `efn_audit.CONTROL_SPECS["res.users"]` (opsi A fit-gap §3.2), **bukan** `efn.audit.rule` res.users di modul ini (unik per model). |
| KPI: penyimpanan | Baris kunci-nilai per PT (`company_id`, `key` Selection tertutup, `value` Char, `note` ≤ 128, `active`), unik `(company_id, key)`. Registri tertutup `models/kpi_registry.py` (tipe/rentang/pilihan/default + 10 pasangan ambang). |
| KPI: nilai kanonik (C-1) | Nilai dikanonikkan saat create/write (int tanpa nol depan, float tanpa `.0` untuk bilangan bulat — `2`, `0.5`; bool `0/1`; enum apa adanya), sehingga `v_sm_kpi_config.is_default = (baris tidak ada/nonaktif) OR (value = literal default)` bisa dibandingkan byte-exact oleh ETL/ClickHouse. Field UI `is_default` memakai semantik yang sama. |
| KPI: validasi | Galat `efn_insight_sm:kpi_<kode>`: `unknown_key`, `type`, `range`, `choice`, `pair` (lower < upper ketat, dibaca dari nilai efektif: baris aktif atau default; juga saat arsip/hapus), `note`, `readonly` (kunci/PT tidak bisa diganti), `company` (hanya PT `SM_CODES`), `duplicate` (constraint SQL). |
| KPI: hak | ACL: `group_insight_kpi_admin` CRUD; `base.group_system` baca. Persona tidak punya akses (membaca lewat ClickHouse). Record rule global `company_id in company_ids`. Audit `audit_rule_efn_insight_sm_kpi` (create/write/unlink + nilai saat hapus). Tanpa 4-eyes di v1. |
| KPI: seed | `post_init_hook` membuat 39 baris default untuk setiap company `SM_CODES` yang sudah ada, dan `res.company` create/write yang menetapkan `efn_brand_code ∈ SM_CODES` melakukan hal sama (di DB baru `efn_setup_supermarket` membuat PT SMD **setelah** modul ini terpasang). Idempoten: baris yang sudah ada (termasuk arsip/diubah) tidak disentuh. Baris tidak ada tetap = default. |
| Menu | *Inventory → Configuration → Definisi KPI Insight (Supermarket)* (grup `kpi_admin`). Menu induk butuh hak Inventory; pengelola KPI tanpa hak Inventory memakai tautan `/odoo/action-efn_insight_sm.action_efn_insight_sm_kpi`. |
| RPC | Tidak ada metode publik baru (semua `_…` atau override ORM). |

## Risiko yang dimitigasi
- **R-S22 (usulan)** kepala toko melihat outlet lain: `wids` dihitung server dari field teraudit, ditandatangani
  (`eit2`), lapis kedua row policy ClickHouse (di luar modul ini).
- **R-T22 (usulan)** angka menyesatkan: definisi KPI tertulis, tervalidasi, teraudit, dan konsisten antar-ambang.
- R-S14 (hak menumpuk tanpa review): persona sensitif tidak ikut sinkron jabatan; perubahan grup/cakupan teraudit.
- Hilangnya grup diam-diam saat form user disimpan (cacat konfigurasi privilege Odoo 19) — juga diperbaiki di
  `efn_replenishment`, `efn_expiry_monitor`, `efn_audit`.

## Cara uji manual
1. Pasang modul (otomatis bila `efn_insight` + `efn_assortment` terpasang). Settings → Users: blok "EFN Dashboard
   Insight" berisi dropdown "EFN Dashboard Insight" lalu enam dropdown persona/pengelola KPI.
2. Beri user Direksi + Kepala toko sekaligus, simpan, buka lagi: keduanya tetap terpilih.
3. Tab "Outlet (kepala toko)" isi outlet; tab "Insight Supermarket" isi kategori. EFN Audit → Log: perubahan tercatat
   (`res.users,<id>`, tanpa nama).
4. Env Odoo `INSIGHT_ENABLED=true`, `INSIGHT_SM_ENABLED=true`: menu Dashboard Insight → token `eit2` (lihat payload di
   insight-web). Kepala toko tanpa outlet → halaman "Akun Anda belum ditautkan ke outlet".
5. Inventory → Configuration → Definisi KPI Insight: ubah `thr.oos.warn_pct` ke `5` (sama dengan bad) → ditolak
   `kpi_pair`; ubah `critical.cover_days` ke `2.50` → tersimpan `2.5`; EFN Audit mencatat nilai lama/baru.

## Test (`tests/`, tag `efn`)
`test_scope.py` (cakupan tiap persona, store tanpa outlet/outlet PT lain/arsip, exec+store = vektor A, cats, PT arsip,
switcher, all_brands, konsistensi `build_payload_v2` dari cakupan nyata), `test_kpi.py` (registri 39 kunci, **parity
JSON**, seed idempoten, kanonik, nilai tidak sah, pasangan termasuk arsip/hapus, readonly, unik, PT non-SM, catatan,
ACL/record rule, audit), `test_privilege.py` (satu privilege per grup, implied, tanpa grup tindakan, emulasi simpan
widget dengan beberapa persona), `test_sm_sso.py` (HttpCase: token exec/store, `no_outlet`, kill switch SM, rollback
eit1, cats + `scope_too_large`).

**Parity JSON**: test mencari `docs/contracts/insight_sm_kpi_defaults.json` naik dari folder modul atau dari env
`EFN_KPI_DEFAULTS_JSON`. Di container yang hanya me-mount `addons`, test di-**skip** dengan pesan jelas; set env atau
mount `docs/contracts` agar parity benar-benar dijalankan.

## Referensi desain (bukan dependency, tidak disalin — ADR-031)
OCA `server-backend/base_user_role` (konsep peran = himpunan grup; sudah ditiru `efn_role`) dan OCA
`mis-builder/mis_builder` (KPI sebagai data yang diedit user; **tidak** diambil: ekspresi bebas). Lisensi keduanya
belum diverifikasi dari manifest (fit-gap §11). Tidak ada kode yang disalin.

## Batasan / belum
- Penetapan persona per jabatan (Q-SM29) dan data demo persona (`efn_setup_supermarket` 19.0.3.0.0) = efn-odoo-functional.
- Validasi pasangan per baris: mengubah dua ambang sekaligus perlu urutan yang menjaga `lower < upper`.
- `INSIGHT_SM_ENABLED` dan row policy ClickHouse wajib direview security sebelum production (docs/04 §28.13.12).
- Belum diuji di instance Odoo berjalan oleh penulis (sesi tanpa shell); lead menjalankan test container.
