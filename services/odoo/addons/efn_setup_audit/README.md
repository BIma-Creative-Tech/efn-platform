# efn_setup_audit

Modul konfigurasi **data-only** berisi aturan audit untuk modul custom `efn_audit` (Gelombang 10). Sejak versi 19.0.2.0.0 **tidak lagi bergantung pada OCA `auditlog`** — kebijakan proyek: tanpa dependency OCA/Cybrosys/vendor; OCA & Cybrosys hanya referensi desain (CLAUDE.md, ADR-031 *Proposed*). Lisensi kembali **LGPL-3**.

- **Risiko dimitigasi:** R-S10 (insider mengubah harga/stok), R-B06 (fraud promo kasir), SEC-07 (perubahan HP member). **ADR:** ADR-015/ADR-031, ADR-020.
- **Fit-gap:** `docs/08_ODOO_FIT_GAP.md` F-17; spesifikasi model `efn_audit`: §5.1.
- **Status:** `installable: False` sampai `efn_audit` ada (tugas efn-odoo-engineer). Engineer mengubahnya ke `True` di PR yang sama dengan `efn_audit`, setelah nama field model dicocokkan dengan kontrak di header `data/efn_audit_rule_data.xml`.

## Aturan yang dibuat (`data/efn_audit_rule_data.xml`, noupdate)
| Model | Create | Write | Delete | Field dilacak | Mask | Catatan |
|---|---|---|---|---|---|---|
| `product.pricelist` | ✓ | ✓ | ✓ (snapshot) | semua | – | harga member/promo/zona |
| `product.pricelist.item` | ✓ | ✓ | ✓ (snapshot) | semua | – | aturan harga |
| `product.template` | ✓ | ✓ | ✓ | semua | – | `list_price`, `standard_price`, master |
| `loyalty.program` / `.reward` / `.rule` | ✓ | ✓ | ✓ (snapshot) | semua | – | promo mekanik POS; `efn_requires_otp` ikut tercatat |
| `stock.quant` | – | ✓ | ✓ | `inventory_quantity`, `inventory_date`, `user_id` | – | hanya hitungan opname; mutasi tidak dilacak |
| `res.partner` | – | ✓ | ✓ | `name`, `phone`, `email`, `vat`, `active`, `company_id` | `phone`, `email` | HP = `member_ref` OTP |
| `ir.config_parameter` | ✓ | ✓ | ✓ (snapshot) | `key`, `value` | – | filter: `efn.%`, `auth_totp.policy`, `auth_password_policy.minlength` |

Parameter `efn.audit.retention_days` = **730 (PLACEHOLDER)** di `data/ir_config_parameter.xml`.

Yang **sudah dicatat standar CE** (tidak perlu aturan): penyesuaian stok yang diterapkan (`stock.move.is_inventory`, *Moves History*); chatter `list_price`/kategori produk (`tracking=True`) dan mata uang/company pricelist; jurnal akuntansi lewat *Restrictive Audit Trail* (`res.company.restrictive_audit_trail`, `account` 19.0).

## Usulan tambahan (belum dibuat, butuh persetujuan + ukur volume)
`account.tax`, `account.fiscal.position` (tarif PB1 per kota, ADR-027), `pos.payment.method`, `pos.config` (`restrict_price_control`, `manual_discount`), `res.users` (perubahan grup). `account.move`: pakai *Restrictive Audit Trail* standar dulu.

## Keputusan klien yang dibutuhkan
- **Retensi** log audit (Q-6 auditor eksternal, Q-49, DPO): placeholder 730 hari.
- **Masking HP/email**: log menyimpan nilai ter-mask (`62812****890`), bukan nilai lengkap. Bila auditor wajib melihat nomor lengkap → butuh opsi enkripsi kolom (lihat docs/08 §5.1) dan persetujuan DPO.
- Siapa auditor (grup *EFN Audit: Auditor*) per PT.

## Migrasi dari versi 19.0.1.x (OCA auditlog)
Hanya DB dev/CI yang pernah memakai versi lama. Urutan: (1) install `efn_audit`; (2) ekspor log `auditlog.log` lama bila perlu; (3) **uninstall OCA `auditlog`** — wajib, karena `patches.py` (perbaikan bug `ThrowAwayCache` × ORM 19) tidak lagi dimuat, sehingga auditlog yang tertinggal akan memicu `AssertionError: Could not find all values ... to flush them`; (4) upgrade `efn_setup_audit`. Aturan `auditlog.rule` lama (xmlid `rule_*`, noupdate) ikut hilang saat auditlog di-uninstall.

**Pembersihan repo (efn-lead/engineer, agent ini tidak bisa menghapus file):** `git rm services/odoo/addons/efn_setup_audit/patches.py services/odoo/addons/efn_setup_audit/data/auditlog_rule_data.xml` — keduanya sudah tidak dimuat manifest/`__init__.py`.

## Cara install (setelah `efn_audit` tersedia)
```bash
odoo -d <db> -i efn_setup_audit   # menarik efn_audit, product, stock, loyalty
```

## Batasan
- Belum pernah di-install dengan `efn_audit` (model belum ada). xmlid model/field mengikuti konvensi `model_<model>` / `field_<model>__<field>`; `stock.quant` (`inventory_quantity`, `inventory_date`, `user_id`) dan `product.template.list_price` (tracking) terverifikasi di source 19.0; xmlid `base.field_ir_config_parameter__*` dan `base.field_res_partner__*` belum diuji install.
- `filter_domain` dievaluasi di memori oleh `efn_audit` (spesifikasi); belum diimplementasikan.
