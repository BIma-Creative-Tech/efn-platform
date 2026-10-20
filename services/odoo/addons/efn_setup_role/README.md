# efn_setup_role

Modul konfigurasi **data-only** (G22): peran `efn_role` per **PT × jabatan** sesuai matriks `docs/08_ODOO_FIT_GAP.md`
§12.2b (diperbarui §13.4), peran *Terminal POS* per PT ber-POS, daftar **grup sensitif tambahan** untuk sinkron
jabatan, dan pemastian **aturan SoD bawaan** (dirujuk lewat kode). Tidak ada model, field, atau logika Python selain
test; satu `<function>` memanggil metode `efn_role` yang sudah ada.

- **Kontrak:** `docs/04_SERVICE_CONTRACTS.md` §13.3 no. 2–3, **L-64**, **L-65**, **L-67**, **L-69**.
- **Spesifikasi:** `docs/08` §12.2b (matriks), §12.4 (SoD), §12.6 (rencana modul ini), **§13.4** (keputusan G22).
- **ADR:** ADR-001 (satu DB multi-company), ADR-031 (tanpa dependency vendor) — keduanya *Proposed*.
- **Risiko dimitigasi:** R-S14 (hak akses menumpuk), R-S10 (insider), R-B06 (fraud refund/promo), R-L01 (ekspor data
  pribadi oleh auditor).
- **depends:** `efn_role`, `efn_setup_org`, `efn_audit`, `efn_loyalty_accounting`, `account`, `point_of_sale`,
  `sales_team`, `stock`, `purchase`, `mrp` (grup diverifikasi `odoo/odoo@19.0`, 2026-10-19).

## Yang dikonfigurasi

### Peran (`data/efn_role_data.xml`, noupdate) — 82 record
Per PT (`efn`, `gl`, `pb`, `bcr`, `bcf`, `farm`) 13 peran berjabatan (`role_<jabatan>_<pt>`) + `role_pos_terminal_<pt>`
untuk GL, PB, BCR, BCF.

| Jabatan | Grup eksplisit | Sinkron dari jabatan | Catatan |
|---|---|---|---|
| Direktur | `account.group_account_readonly` | tidak | laporan lintas PT lewat Metabase |
| Finance Manager | `account.group_account_manager`, `account.group_account_user` | tidak (sensitif) | penyetuju kebijakan poin; ⟂ Loyalty Manager (SOD-03) |
| Staf Finance | `account.group_account_user` | tidak | |
| Store Manager | `point_of_sale.group_pos_manager` | tidak (sensitif) | ⟂ Kasir (SOD-02); approver retur tanpa struk (E-41) |
| Kasir | `point_of_sale.group_pos_user`, `efn_role.group_role_cashier` | **ya** | kasir ber-user saja; kasir PIN tanpa user tidak perlu peran |
| Terminal POS (tanpa jabatan) | `point_of_sale.group_pos_user` | — | user terminal bersama (login PIN `pos_hr`) |
| Merchandiser | `sales_team.group_sale_manager`, `stock.group_stock_user` | tidak (sensitif) | ⟂ Approver Refund (SOD-01) |
| Staf Pengadaan | `purchase.group_purchase_user`, `stock.group_stock_user` | tidak | SOD-05 report_only |
| Staf Gudang | `stock.group_stock_user` (+ `mrp.group_mrp_user` di PB & BCF = Central Kitchen) | **ya** | |
| Staf CS | `sales_team.group_sale_salesman_all_leads` | tidak | membaca semua pesanan/pelanggan → penetapan sengaja (UU PDP) |
| Approver Refund CS | idem + `efn_role.group_refund_approver` (cermin BFF) | tidak (sensitif) | ⟂ Sales Manager (SOD-01) |
| Sales Manager Online | `sales_team.group_sale_manager`, `stock.group_stock_user` | tidak (sensitif) | stock user: batal SO saat purchase_stock terpasang [^sec172] |
| Loyalty Manager | `efn_loyalty_accounting.group_loyalty_manager` | tidak (sensitif) | pembuat kebijakan poin |
| Auditor Internal | `account.group_account_readonly`, `efn_audit.group_auditor` | tidak | **tanpa** `base.group_allow_export` (SEC-155); ekspor review lewat tombol snapshot |

[^sec172]: **Residu SEC-172 (diterima, L-82 c, R-S16).** `stock.group_stock_user` juga memberi hak membuat dan memvalidasi transfer
stok di PT itu, bukan hanya membatalkan SO. Alternatif `sudo()` terbatas di `efn_online_order` ditolak. Kompensasi: review
akses bulanan (`efn_role`) wajib mencakup pemegang peran ini dan transfer stok yang mereka validasi.

**Tidak dibuat:** peran *Kurir* (tanpa user Odoo) dan *Superadmin IT* (peran admin hanya boleh dikelola Superadmin —
buat lewat UI bila perlu). Penetapan ke orang (`efn.role.line`) **tidak** ada di data (A-5, 18-24).

### Grup sensitif tambahan (`data/ir_config_parameter.xml`, noupdate)
`efn_role.sensitive_group_xmlids` = `account.group_account_readonly`, `account.group_account_user`,
`purchase.group_purchase_user`, `efn_audit.group_auditor`, `efn_audit.group_auditor_personal`,
`sales_team.group_sale_salesman_all_leads`. Hanya **menambah** daftar bawaan `efn_role` (L-67/SEC-154); akibatnya
constraint `efn_role:sync_sensitive_role` menolak flag sinkron pada peran keuangan, pengadaan, audit, dan CS.

### Aturan SoD (`data/efn_sod_rule_data.xml`, noupdate=0)
`<function model="efn.sod.rule" name="_efn_ensure_default_rules"/>` — **tidak** membuat ulang aturan; memanggil metode
idempoten `efn_role` (kunci `code`) agar **SOD-01/02/03** (`block`) dan **SOD-05/06** (`report_only`) ada segera setelah
semua modul pemilik grup terpasang. L-69: SOD-03 wajib `block` sebelum kebijakan poin pertama dipublikasikan (cek di
*Settings → Users & Companies → Aturan SoD*).

## Cara mengubah
- Access Rights: *Settings → Users & Companies → Peran EFN* — ubah grup, arsipkan peran yang tidak berlaku di PT itu
  (mis. Kasir di Holding/FARM). Upgrade modul tidak menimpa (noupdate).
- Tetapkan ke orang: *Penetapan Peran* (manual) atau isi jabatan karyawan (hanya Kasir & Staf Gudang yang tersinkron
  otomatis oleh cron harian 00:30 WIB).
- Auditor holding/DPO yang perlu log data pribadi lintas PT: tambahkan `efn_audit.group_auditor_personal` ke peran
  auditor Holding secara manual setelah 18-38 diputuskan.

## Keputusan klien yang dibutuhkan
- **Q-ORG-1** jabatan per PT (default: semua ada; arsipkan yang tidak dipakai).
- **Q-ORG-2** CS / IT / Auditor terpusat di Holding? (18-38 masih terbuka).
- **Q-ORG-4** SOD-05/SOD-06 dinaikkan ke `block`? (default `report_only`).
- Nama orang per jabatan & anggota `efn-cs-approver` (A-5).

## Test
`tests/test_setup_role.py` (tag `efn`): matriks per PT × jabatan, peran Terminal POS, jumlah & tanpa Kurir/Superadmin,
unik per jabatan, data-only tanpa penetapan & noupdate, sinkron hanya peran non-sensitif, parameter sensitif tambahan
(+ flag ditolak), Auditor tanpa ekspor/admin, tidak ada peran yang memberi ekspor/admin, aturan SoD dirujuk lewat
kode (tidak ada xmlid aturan di modul ini; SOD-03 `block`), tidak ada peran yang sendirian melanggar aturan `block`,
penerapan Kasir + penolakan Store Manager (SOD-02), sinkron jabatan Gudang vs Store Manager.

## Belum / belum terverifikasi
- Test dijalankan lead di container (suite penuh G22) dan masuk `EFN_TEST_MODULES` CI.
- [B] `<function>` pada metode privat di data XML 19.0: `convert.py` hanya memblokir nama dunder/`_UNSAFE_ATTRIBUTES`
  (diverifikasi WebFetch 2026-10-19) — perilaku runtime menunggu install.
- Regresi: dengan modul ini terpasang, grup peran (mis. `point_of_sale.group_pos_user`) menjadi "terkelola" bagi user
  ber-flag `efn_role_managed` saja; user test modul lain tidak ber-flag → tidak terdampak. Jalankan suite
  `--test-tags efn` lengkap.

## Referensi
Tidak ada kode vendor. Konsep peran = himpunan grup dari OCA `server-backend@19.0` `base_user_role` (LGPL-3) sudah
dicatat di `efn_role` (ADR-031).
