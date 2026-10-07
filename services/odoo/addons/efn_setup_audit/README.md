# efn_setup_audit

Modul konfigurasi **data-only** berisi aturan audit untuk modul custom `efn_audit` (Gelombang 10). Sejak versi 19.0.2.0.0 **tidak lagi bergantung pada OCA `auditlog`** — kebijakan proyek: tanpa dependency OCA/Cybrosys/vendor; OCA & Cybrosys hanya referensi desain (CLAUDE.md, ADR-031 *Proposed*). Lisensi kembali **LGPL-3**.

- **Risiko dimitigasi:** R-S10 (insider mengubah harga/stok), R-B06 (fraud promo kasir), SEC-07 (perubahan HP member). **ADR:** ADR-015/ADR-031, ADR-020.
- **Fit-gap:** `docs/08_ODOO_FIT_GAP.md` F-17; spesifikasi model `efn_audit`: §5.1.
- **Status:** `installable: True` (Gelombang 10, bersama `efn_audit`). Nama field dicocokkan dengan kontrak di header `data/efn_audit_rule_data.xml`; semua xmlid model/field terverifikasi lewat install nyata Odoo 19 + test `tests/test_setup_audit.py`.

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
`account.tax`, `account.fiscal.position` (tarif PB1 per kota, ADR-027), `pos.payment.method`, `pos.config` (`restrict_price_control`, `manual_discount`). Perubahan hak akses (`res.users` grup/aktif/login/company, `res.groups`, `ir.rule`, `ir.model.access`, API key) **tidak perlu aturan di sini**: sejak `efn_audit` 19.0.1.3.0 (Gelombang 13, SEC-49) model tersebut diaudit bawaan (hard-coded, tidak ikut GC, masuk sink). Catatan SEC-56: `stock.quant.user_id` kini tersimpan `res.users,id` (tanpa nama staf). `account.move`: pakai *Restrictive Audit Trail* standar dulu.

## Keputusan klien yang dibutuhkan
- **Retensi** log audit (Q-6 auditor eksternal, Q-49, DPO): placeholder 730 hari.
- **Masking HP/email**: log menyimpan nilai ter-mask (`62812****890`), bukan nilai lengkap. Bila auditor wajib melihat nomor lengkap → butuh opsi enkripsi kolom (lihat docs/08 §5.1) dan persetujuan DPO.
- Siapa auditor (grup *EFN Audit: Auditor*) per PT.

## Migrasi dari versi 19.0.1.x (OCA auditlog)
Hanya DB dev/CI yang pernah memakai versi lama. Urutan (dengan image lama yang masih berisi OCA): (1) **upgrade `efn_setup_audit`** sambil install `efn_audit` (`-i efn_audit -u efn_setup_audit,...`) — dependency berpindah dari `auditlog` ke `efn_audit`; tanpa langkah ini uninstall `auditlog` ikut meng-uninstall `efn_setup_audit`; (2) ekspor log `auditlog_log` lama bila perlu; (3) **uninstall OCA `auditlog`** — wajib, karena `patches.py` sudah dihapus sehingga auditlog yang tertinggal memicu `AssertionError: Could not find all values ... to flush them`; (4) build & jalankan image tanpa OCA. Perintah lengkap + verifikasi: `docs/runbooks/dev-stack-boot.md` §3a (diuji pada volume dev 6 Okt 2026).

`patches.py` dan `data/auditlog_rule_data.xml` sudah dihapus dari repo (Gelombang 10).

## Cara install
```bash
odoo -d <db> -i efn_setup_audit   # menarik efn_audit, product, stock, loyalty
```

## Test
`tests/test_setup_audit.py` (tag `efn`): 9 aturan + parameter retensi termuat; regresi bug flush OCA (create produk bervarian saat partner belum di-flush → lulus, log create/write `list_price`); `stock.quant` mutasi (`_update_available_quantity`, write `quantity`) tanpa read audit & tanpa log, `inventory_quantity` tercatat; filter `ir.config_parameter`; HP partner ter-mask & create partner tidak dicatat; promo loyalty tercatat; performa import 200 produk (overhead ≈ +22 query).

## Batasan
- Semua xmlid model/field terverifikasi lewat install nyata Odoo 19 (CI lokal + volume dev).
- Overhead `product.template` semua field belum diukur pada beban PoC (1.000 produk / 100 order POS) — persempit `tracked_field_ids` bila mahal.
