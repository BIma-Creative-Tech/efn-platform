# efn_tax_withholding (19.0.1.0.0) — G59-A PPh potong & Daftar Pemotongan PPh (e-Bupot)

> **Ringkasan manajemen.** PPh 23 (jasa), PPh 4(2) (sewa), dan PPh 26 (vendor luar negeri) dipotong langsung di tagihan
> vendor memakai fitur pajak standar Odoo, sehingga hutang ke vendor otomatis neto dan hutang PPh tercatat. Modul ini
> hanya menambahkan kode objek pajak e-Bupot, nomor/tanggal bukti potong dari Coretax, dan satu layar **Daftar Pemotongan
> PPh** per masa yang bisa diekspor ke Excel untuk input e-Bupot. Tarif dan kode objek diisi Finance (placeholder sampai
> konsultan pajak mengonfirmasi).

- **Spesifikasi:** `docs/fit-gap/G59_G60_ACCOUNTING_PORT.md` §G59-A (keputusan D-1, D-2).
- **ADR:** ADR-049 (port & adaptasi `odoo-platform`), ADR-027 (tarif pajak = konfigurasi, bukan kode), ADR-031,
  ADR-036 (NPWP terenkripsi `efn_pii_base_vat`).
- **Risiko dimitigasi:** R-L03 (salah hitung/lapor pajak: jurnal PPh dari engine pajak CE — tax grid, refund, reset, lock
  date ikut aturan CE; tidak ada engine kedua), R-T18 (NPWP dibaca lewat ORM, tidak lewat SQL), R-T03/R-L08 (provenance).
- **Depends:** `account`, `l10n_id`, `efn_core`. Tidak ada model baru → `ir.model.access.csv` hanya header; hak baca daftar
  = ACL/record rule multi-company `account.move.line` CE.

## Cara kerja
1. **Pajak PPh = pajak negatif CE** (`type_tax_use='purchase'`, `amount_type='percent'`, `amount < 0`), distribusi pajak
   ke akun hutang PPh. Tab baru **PPh (e-Bupot)** di form pajak: `efn_pph_kind` (PPh 23 / 4(2) / 26 / 22 / 15) dan
   `efn_bupot_object_code` (format `NN-NNN-NN`). Constraint: jenis PPh terisi ⇒ persentase negatif; pajak PPh aktif wajib
   punya kode objek.
2. **PPh 26:** fiscal position "Vendor Luar Negeri" (auto, grup negara selain Indonesia); pajak PPh 26 diberi
   *Fiscal Positions* = FP itu dan *Replaces* (`original_tax_ids`) = PPh 23 (model CE 19: pemetaan di pajak tujuan).
   **Vendor tanpa NPWP:** FP manual "Vendor tanpa NPWP" → PPh 23 4% (⚠ konfirmasi konsultan pajak, Q-G59-5).
3. **Daftar Pemotongan PPh** (Accounting → Reporting → Taxes & Fiscal; grup Accounting User/Manager): list + pivot baris
   pajak PPh di jurnal **posted**: tanggal, dokumen, partner, NPWP (`efn_pph_partner_vat`, related ORM → terdekripsi), kode
   objek, DPP (`tax_base_amount`), PPh (`balance`), nomor & tanggal bukti potong (edit inline). Filter Masa, group by
   pajak/partner/bulan. Ekspor = fitur *Export* standar list.
4. **Bukti potong:** `efn_bupot_number` / `efn_bupot_date` di `account.move.line` (`copy=False`), boleh diisi setelah
   posted (bukan field terlindung lock date/hash CE 19). Hanya baris pajak PPh (constraint) dan hanya user Accounting
   User/Manager (override `write`; user Billing ditolak `AccessError`).

## Checklist konfigurasi produksi (Finance, per PT, lewat UI — tidak ada `post_init_hook`)
| Pajak | Tarif | Akun distribusi (invoice & refund) | Kode objek | Catatan |
|---|---|---|---|---|
| PPh 23 jasa | -2 % | `21210030` Tax Payable PPh 23 | placeholder `24-104-01` | produk jasa: *Vendor Taxes* |
| PPh 23 tanpa NPWP | -4 % | `21210030` | placeholder `24-104-01` | FP "Vendor tanpa NPWP", Replaces PPh 23 |
| PPh 4(2) sewa | -10 % | `21210050` Tax Payable 4(2) | placeholder `28-403-01` | produk "Sewa Outlet" |
| PPh 26 | -20 % | `21210060` | placeholder `27-100-99` | FP "Vendor Luar Negeri", Replaces PPh 23 |
| PPh 23 dipotong pelanggan (B2B) | -2 %, `sale` | `11510030` Prepaid Tax PPh 23 | sesuai bukti potong | opsional |

Kode objek dan tarif wajib dikonfirmasi konsultan pajak (Q-G59-1). Data demo (`demo/account_tax_pph_demo.xml`, hanya DB
demo) membuat set di atas bernama `... (PLACEHOLDER)` untuk setiap PT ber-CoA l10n_id.

## Provenance (ADR-049)
- **Asal:** `odoo-platform` `addons/ee_gap/custom_tax_id` 19.0.0.8.0 (LGPL-3, `author: Custom Platform`), snapshot
  `origin/main` dibaca 23 Nov 2026 (hash commit belum dicatat — snapshot tanpa metadata git; efn-lead mengisi dari repo
  pemilik). Folder `data/` sumber (seed kategori PPh) tidak dibutuhkan.
- **Tidak ada kode yang disalin.** Di-drop (spesifikasi §G59-A): `withholding_category.py`, `withholding_rule.py`,
  `account_move_withholding_line.py`, engine `_post`/`_custom_apply_withholding` di `account_move_inherit.py` (CE sudah
  membukukan PPh; engine sumber pun melewati baris berpajak negatif), DPP Nilai Lain `account_tax_inherit.py` (dobel dengan
  `l10n_id_efaktur_coretax`), `res_partner/company/country/uom/product_template_inherit.py`, wizard Faktur Pengganti &
  bulk validation, migrasi 19.0.0.8.0, test sumber. Ide yang dipakai ulang (ditulis baru): kode objek per jenis PPh.
  Rename: `x_custom_*` → `efn_*`.
- **Ditunda (P3, Q-G59-4):** simpan/pulihkan nominal pajak yang diketik saat reset to draft.

## Uji manual (DB dev, demo)
1. Accounting → Configuration → Taxes: buka "PPh 23 Jasa 2% (PLACEHOLDER)" → tab PPh (e-Bupot). Ubah tarif ke 2 → ditolak.
2. Tagihan vendor Rp1.000.000 dengan PPh 23 → Post: Hutang usaha 980.000, Hutang PPh 23 20.000.
3. Vendor berfiscal position "Vendor Luar Negeri" → produk jasa otomatis PPh 26.
4. Accounting → Reporting → Taxes & Fiscal → Daftar Pemotongan PPh: baris tagihan muncul (draft tidak), isi nomor bukti
   potong inline, Export.
5. Login user Billing → menu tidak tampil; tulis nomor bukti potong via RPC ditolak.

## Test
`tests/test_withholding.py` (tag `efn`, `AccountTestInvoicingCommon`): (1) AP neto + hutang PPh 23 + DPP, (2) FP PPh 23 →
PPh 26 (map_tax + tagihan), (3) constraint tarif/tipe/regex/kode wajib, (4) daftar hanya posted & hanya company user,
(5) nomor bukti potong setelah posted oleh accountant; Billing ditolak; baris non-PPh ditolak; tidak tersalin,
(6) NPWP tersimpan terenkripsi dan terbaca plaintext lewat ORM untuk accountant, ditolak untuk Billing (di-skip bila
`efn_pii_base_vat` tidak terpasang).

## Batasan / belum
- **Export XLSX tidak diaudit:** `efn_audit` tidak menangkap export list (dicek: tidak ada hook export di `efn_audit`) →
  sisa risiko PII (NPWP perorangan keluar sistem). Mitigasi: menu & field NPWP hanya untuk grup Accounting.
- XML e-Bupot Coretax tidak dibangun (Q-G59-3); input/impor manual dari XLSX.
- PPh saat **pembayaran** (`l10n_account_withholding_tax` CE) tidak dipakai dan tidak boleh dicampur dengan pola ini
  (Q-G59-2 default: saat tagihan).
- Retensi data = retensi dokumen pajak (10 tahun, UU KUP) — ⚠ konfirmasi Legal.
- Aturan `efn.audit.rule` untuk perubahan pajak PPh belum ada (milik `efn_setup_audit`).
- Modul perlu ditambahkan ke `EFN_TEST_MODULES` CI (efn-lead).

**Hash commit `odoo-platform` (origin/main, dicatat efn-lead 23 Nov 2026):** `custom_tax_id` `c824872`.
