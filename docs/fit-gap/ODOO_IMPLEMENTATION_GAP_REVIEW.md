# Review gap implementasi Odoo EFN (G63, 23 Nov 2026)

Lingkup: kondisi DB `efn_odoo` di env gladi lokal `efn-prod` (dibaca read-only 23 Nov 2026) dibanding kebutuhan F&B (PB)
dan Supermarket (SMD) untuk pilot. Bukan VPS/production. Terkait: `docs/fit-gap/G54_G55_G61_ODOO_SETUP.md`,
`docs/fit-gap/G61_KATEGORI_PRODUK_PB.md`, ADR-042, ADR-049, `docs/runbooks/efn-prod-odoo-reset.md`.

> **Ringkasan manajemen.** Setelah branch `feat/odoo-completion` di-merge dan diterapkan, POS PB bisa dibuka, menu
> Finance/Accounting/Produksi terpisah, kategori produk rapi, dan halaman masuk bermerek. Yang masih menghalangi pilot:
> (1) beberapa modul EFN yang sudah jadi **belum dipasang** di efn-prod (laporan keuangan, role/hak akses, keamanan
> password), (2) **saldo awal** stok/piutang/utang belum dimuat, (3) **akun PB1/service charge dan akun persediaan** masih
> placeholder menunggu Finance, (4) fitur accounting port Levi's (aset, rekonsiliasi bank, PPh) baru direncanakan (G57–G60).

## 1. Sudah ditangani di branch ini

| # | Kebutuhan | Hasil | Gelombang |
|---|---|---|---|
| 1 | POS PB tidak bisa Open Register (32 config tanpa metode bayar) | Wizard "Lengkapi konfigurasi POS" (cek + apply, idempoten, per company) | G54 |
| 2 | Finance dan Accounting terpisah; "Manufacturing" → "Produksi" | `efn_setup_menu` | G55 |
| 3 | Tampilan home `custom_home_console` | `efn_web_home` (port, tanpa ikon Enterprise) + gaya login Odoo | G56 |
| 4 | Kategori PB tercampur (aset/expense/bahan baku; 940 aset & expense ber-stok; 1.680 menu tanpa kategori) | Pohon per peran akuntansi + mapping migrasi + skrip rekategorisasi (dry-run efn-prod: 5.282 produk, 0 tak terpetakan) | G61 |
| 5 | Halaman login custom seperti EAL | `services/login-gateway` + wiring infra | G62 |

## 2. Gap yang tersisa (diprioritaskan)

| Prio | Gap | Bukti (efn-prod 23 Nov) | Dampak | Usulan | Pemilik |
|---|---|---|---|---|---|
| P1 | Modul EFN yang sudah ada di repo belum dipasang: `efn_account_reports`, `efn_intercompany`, `efn_role`/`efn_setup_role`, `efn_setup_security`, `efn_setup_audit`, `efn_setup_mrp`, `efn_loyalty_accounting`, `efn_pos_payment_gateway`, `efn_l10n_id_coretax_rounding` | `ir_module_module` | Tanpa laporan keuangan resmi, tanpa role/SoD, kebijakan password default, tanpa audit rule | Tulis **daftar modul baku per env** (file di repo, dipakai runbook reset & deploy-validate); pasang di efn-prod setelah merge | efn-lead + devsecops |
| P1 | Saldo awal stok/AR/AP tidak dimuat | ADR-042, Q-ESB21; 0 quant PB | Laporan stok & neraca kosong; tidak bisa cutover | Keputusan Finance: tanggal saldo awal + sumber (ESB vs neraca manual) | Finance |
| P1 | Akun PB1, service charge, persediaan per peran = placeholder | Q-2, Q-ESB16; kategori lama tanpa akun | Jurnal POS/stok salah akun | Finance memetakan akun; perbarui `ROLE_PROPS` (`recategorize_pb.py`) dan data `efn_setup_company` | Finance |
| P1 | Rekening bank perusahaan belum ada | `res_partner_bank` = 0 untuk semua PT | QRIS/transfer/rekonsiliasi bank tidak bisa | Input rekening per PT (data rahasia, bukan di repo) | Finance |
| P2 | User & karyawan POS | 16 user internal; `hr.employee` 1 per PB/SMD | Kasir tidak bisa login per orang (pos_hr), SoD lemah | Setelah `efn_setup_role`: buat user per persona + karyawan per outlet | Ops + efn-lead |
| P2 | Lock date & tahun fiskal | `fiscalyear_lock_date`/`hard_lock_date` kosong | Periode tertutup masih bisa diubah | SOP tutup buku bulanan + lock date | Finance |
| P2 | Valuasi stok periodik (bukan real-time) | G61: AVCO dipasang, valuasi tetap periodik | HPP tidak otomatis di jurnal | Putuskan Q-ESB16 lalu ubah `property_valuation` per peran | Finance |
| P2 | Menu PB tanpa grup POS jatuh ke "Menu / Lainnya" (786 item) dan kategori D/T terpisah (dine-in/takeaway) | dry-run G61 | Laporan per kategori menu kurang berguna | Lengkapi `pos.category` di ESB/Odoo; pertimbangkan gabung D/T menjadi satu kategori menu | Ops/Category manager |
| P2 | Accounting port Levi's: konfigurasi data | Modul G57–G60 dibangun 23 Nov; akun uang muka kas kecil, akun software, tarif MDR, kode objek PPh, format file bank = placeholder | Fitur bisa dipakai setelah Finance mengisi akun/tarif | Finance mengisi Q-G57-1/2, Q-PC1, Q-G58-1/2, Q-G59-1, Q-G60-5 (lihat fit-gap G57–G60) | Finance |
| P3 | Menu "Apps" terlihat semua user internal | CE 19: `base.menu_management` tanpa grup (DB efn-prod) | User melihat daftar modul (tidak bisa install tanpa hak) | Opsional: grup `base.group_system` lewat `efn_setup_menu` | efn-lead |
| P3 | QRIS manual = kasir menyatakan lunas sendiri | SEC-G54-03 | Peluang fraud kasir | SOP rekonsiliasi harian mutasi QRIS oleh Finance (bukan kasir); gateway QRIS (ADR-026) | Finance + Security |
| P3 | PT lain (GL, Bacha, WTZ, IMD, CUP, FARM) belum punya data master | 0 warehouse/BOM | Tidak bisa uji lintas PT | Menunggu tarikan ESB per brand / data master | Ops |
| P3 | Printer termal, email keluar, payment gateway | K25-04, ADR-048, ADR-016/026 | Struk & notifikasi | Gelombang terpisah | — |

## 3. Pertanyaan terbuka untuk pemilik proyek / Finance
1. G61 A1–A11 (lihat `docs/fit-gap/G61_KATEGORI_PRODUK_PB.md`): mis. LVA dibebankan atau dikapitalisasi, Freight-Import
   sebagai landed cost, "Inventory - F&B" sebagai bahan baku. Default sudah dipakai di dry-run.
2. Q-ESB16: metode biaya (AVCO) dan valuasi real-time per peran.
3. Q-2: akun liabilitas PB1 dan pendapatan service charge.
4. Daftar modul baku efn-prod: setuju memasang semua modul §2 P1?
