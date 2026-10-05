# OCA addons

Direktori ini diisi saat build (CI) atau lokal dengan `scripts/fetch-oca.sh`. Hanya modul yang dipakai, branch 19.0 (lihat R-T04, ADR-021). Sumber status: `docs/06_ODOO_CE19_MODULE_AUDIT.md` §2.

| Repo OCA (branch 19.0) | Modul | Versi teraudit | Lisensi | Kegunaan |
|---|---|---|---|---|
| OCA/multi-company | `purchase_sale_inter_company`, `account_invoice_inter_company` | 19.0.1.0.1 / 19.0.1.0.3 | AGPL-3 | Inter-company otomatis (menggantikan "modul Multi-Company custom"). `purchase_sale_stock_inter_company` **tidak ada** di 19.0 — perilaku picking inter-company harus diuji di PoC. |
| OCA/server-tools | `auditlog` | 19.0.2.0.0 | AGPL-3 | Jejak audit perubahan harga/stok/poin (R-S10) |
| OCA/mis-builder | `mis_builder` | 19.0.1.2.1 | AGPL-3 | Laporan konsolidasi multi-company (§3.6) |
| OCA/reporting-engine | `report_xlsx` | 19.0.1.0.2 | AGPL-3 | Dependensi wajib `mis_builder` |
| OCA/server-ux | `date_range` | 19.0.1.0.0 | LGPL-3 | Dependensi wajib `mis_builder` |
| OCA/manufacture | `quality_control_oca` | 19.0.1.0.0 | AGPL-3 | Evaluasi untuk QC intake farming (fase lanjut) |
| OCA/pos | sesuai audit | — | — | Banyak ekstensi POS baru ada di 18.0 (perlu porting); KDS/preparation display **tidak ada** di OCA 19.0 maupun CE 19.0 |

Koreksi dari versi sebelumnya:
- `stock_picking_batch` adalah modul **CE** (LGPL-3, ikut image `odoo:19.0`), bukan OCA — tidak perlu di-fetch.
- OCA/l10n-indonesia branch 19.0 **kosong**; lokalisasi memakai `l10n_id` / `l10n_id_efaktur_coretax` dari CE.
- `pos_preparation_display` tidak ada di CE 19.0 dan tidak boleh dijadikan dependency.

Catatan: `scripts/fetch-oca.sh` harus ikut mengambil OCA/reporting-engine dan OCA/server-ux agar `mis_builder` bisa di-install (di luar area agent Odoo; lihat docs/06 §2).

Jangan commit kode OCA ke repo ini; gunakan submodule atau fetch saat build agar lisensi AGPL dan upstream update tetap bersih. Addon EFN yang bergantung pada modul OCA AGPL wajib berlisensi AGPL-3.
