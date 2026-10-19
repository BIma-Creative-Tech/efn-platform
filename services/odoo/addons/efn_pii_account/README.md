# efn_pii_account (19.0.2.0.0)

Modul penghubung `auto_install` untuk `efn_pii` + `account` (CE). ADR-036 (Accepted, §0.2 tabel field, §0.6), ADR-031.
Risiko: **R-T18** (definisi ulang field PII oleh modul lain), R-L01; menutup SEC-153 / AC-10 (bagian faktur).

## Isi
| Bagian | Perilaku | Sumber 19.0 (WebFetch 19 Okt 2026) |
|---|---|---|
| `res.partner.name` | kelas `EfnPiiChar` dipulihkan (account: `name = fields.Char(tracking=True)`) | `account/models/partner.py` |
| `account.move.invoice_partner_display_name` | **E** untuk faktur partner terlindungi; penanda `account.move.efn_pii_protected` (stored, precompute, satu arah, `groups=base.group_system`) dari partner / commercial partner | `compute='_compute_invoice_partner_display_info', store=True`, depends `partner_id`, `invoice_source_email`, `partner_id.display_name` |
| Pencarian | `invoice_partner_display_name` `ilike`/`=` untuk faktur terlindungi → `partner_id IN (partner complete_name cocok, token nama)`; view standar mencari lewat `partner_id` (`ilike`/`child_of`) → token nama partner | `account_move_views.xml`: filter_domain tidak memakai field ini |
| Partner baru terlindungi | faktur lama ikut dienkripsi (hook `_efn_pii_after_protect`) | — |
| `res.partner.bank.acc_number`/`acc_holder_name` | kelas `EfnPiiChar` dipulihkan (account: `fields.Char(tracking=True)`) | `account/models/res_partner_bank.py` |
| Duplikat rekening | SQL account `this.acc_number = other.acc_number` tidak cocok untuk ciphertext → untuk rekening terlindungi dicari lewat `sanitized_acc_number` (blind index) | idem `_compute_duplicate_bank_partner_ids` |

## Uji manual
1. Pasang tiga kunci uji, buat pelanggan perorangan "Budi Faktur", buat faktur → `psql`:
   `SELECT invoice_partner_display_name FROM account_move WHERE id=<id>` → `efnpii2:…`; daftar faktur menampilkan nama.
2. Cari faktur "budi" (filter pelanggan) → ditemukan; potongan tengah kata → tidak.
3. Rekening vendor perorangan: `SELECT acc_number, sanitized_acc_number FROM res_partner_bank` → `efnpii2:` + hex 64.

## Batasan
- Urutan daftar faktur menurut nama pelanggan = urutan ciphertext untuk faktur terlindungi (O1, Q-PII11).
- `invoice_source_email` (vendor bill dari email masuk) dan PDF faktur di filestore tetap plaintext (tahap 2).
- Duplikat rekening terlindungi vs tidak terlindungi bernomor sama tidak terdeteksi.
- Audit CE modul yang mendefinisikan ulang field PII `res.partner` (image `efn-odoo:ci`, 18 Okt 2026): `mail`,
  `base_vat` (→ `efn_pii_base_vat`), `account` (→ modul ini), `l10n_id_efaktur_coretax` (→ `efn_pii_l10n_id`).

## Test
`tests/test_pii_account.py` (tag `efn`, `efn_pii`; `AccountTestInvoicingCommon` dengan kunci kelas).

## Referensi (ADR-031)
Tidak ada kode OCA/Cybrosys. Ditulis dari source Odoo CE 19.0.
