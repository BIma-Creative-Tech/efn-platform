# efn_setup_menu (G55)

Memisahkan menu **Finance** dan **Accounting**, dan mengganti label **Manufacturing** menjadi **Produksi**.
Spesifikasi: `docs/fit-gap/G54_G55_G61_ODOO_SETUP.md` §G55 (efn-odoo-functional, 23 Nov 2026).

## Tujuan

| Menu | Isi |
|---|---|
| **Finance** (`account.menu_finance`, record CE yang sama; label diganti, grup & ikon CE tetap) | Dashboard, Customers, Vendors, **Kas & Bank** (baru: Rekening Koran Bank, Register Kas, Transfer Internal) |
| **Accounting** (baru, `efn_setup_menu.menu_accounting_root`, seq 56, grup `account.group_account_readonly` + `account.group_account_manager`, ikon CE akun) | **Jurnal & Penutupan** (`account.menu_finance_entries`), Review, Reporting, Configuration |
| **Produksi** (`mrp.menu_mrp_root`) | hanya label |

Menu `efn_account_reports` dan `efn_intercompany` ikut pindah karena induknya pindah. Modul ini **tidak** depend ke
modul-modul itu. xmlid akar Accounting dipakai `efn_web_home` (`ROOT_MENU_GROUPS`, kelompok "accounting").

Label ditulis untuk en_US **dan setiap bahasa terpasang** oleh `ir.ui.menu._efn_apply_menu_labels`. Method ini
dipanggil `<function>` di `data/menu_data.xml` (noupdate=0), sehingga berjalan saat install dan setiap upgrade.

## ADR & keputusan

- ADR-031: hanya CE (`account`, `mrp`). Tidak ada referensi OCA/Cybrosys. Semuanya memakai API standar `ir.ui.menu`.
- Default yang dipakai sampai dijawab: Q-G55-1 (ikon Accounting = ikon Finance CE) dan Q-G55-2 (user Billing saja kehilangan
  "Invoice Analysis" karena Reporting pindah ke Accounting).
- xmlid akar = `menu_accounting_root`, mengikuti `efn_web_home`. Spesifikasi menulis `menu_efn_accounting_root`.

## Risiko yang dimitigasi / diketahui

- **Urutan upgrade:** menu CE dideklarasikan noupdate=0, jadi `-u account` / `-u mrp` me-reset induk, urutan, dan label
  en_US. `button_upgrade` ikut meng-upgrade modul yang bergantung (termasuk modul ini) dan memuatnya **sesudah**
  account/mrp, sehingga perubahan diterapkan lagi. Bila `account`/`mrp` di-upgrade dengan cara yang tidak menyertakan
  dependent (mis. skrip yang menulis `state` langsung), jalankan `-u efn_setup_menu`.
- **Bahasa baru:** `update_field_translations` menolak bahasa nonaktif, jadi hanya bahasa yang terpasang saat itu yang
  ditulis. Setelah mengaktifkan bahasa baru (pemuatan terjemahan CE bisa mengisi "Manufaktur"/"Faktur"), jalankan
  `-u efn_setup_menu`.
- Menu modul lain yang menempel ke menu yang dipindah akan ikut pindah. Jalankan kueri di spesifikasi §G55.1 di efn-prod
  sebelum dan sesudah install untuk memeriksanya.

## Uji

- Otomatis (`tests/test_menu_labels.py`, tag `efn`): memeriksa induk keempat menu, grup akar, label en_US dan id_ID
  (id_ID diaktifkan di test), menu efn_account_reports (dilewati bila modulnya tidak terpasang), dan bahwa user Billing
  saja tidak melihat akar Accounting.
- Manual (tidak bisa di TransactionCase):
  1. `-i efn_setup_menu` → periksa ada Finance dan Accounting di beranda, dan Produksi.
  2. `-u mrp`, lalu `-u account` → label dan induk tetap.
  3. Aktifkan bahasa baru → `-u efn_setup_menu` → label benar di bahasa itu.

## Batasan

- Belum ada ikon terpisah untuk Accounting (Q-G55-1).
- Isi action "Transfer Internal" (`account.action_account_payments_transfer`) memakai domain dan konteks CE apa adanya.
- Uninstall (`hooks.py`) hanya mengembalikan induk dan urutan keempat menu CE ke `account.menu_finance`, agar akar
  Accounting bisa dihapus (`parent_id` ondelete=restrict). Label CE di semua bahasa dipulihkan dengan `-u account mrp`.
