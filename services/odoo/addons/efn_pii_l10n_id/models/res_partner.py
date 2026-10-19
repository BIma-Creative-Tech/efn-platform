"""ADR-036 §0.2 (18-7, K-36-7): NIK & nomor dokumen pembeli Coretax terenkripsi + blind index.

Odoo 19.0 `l10n_id_efaktur_coretax/models/res_partner.py` (WebFetch 19 Okt 2026): `l10n_id_nik = fields.Char('NIK')`,
`l10n_id_buyer_document_number = fields.Char("Document Number")`, `l10n_id_buyer_document_type` (Selection, default
TIN), `l10n_id_pkp` (compute stored dari `vat`), tanpa constraint. Dibaca ORM oleh
`account.move._l10n_id_coretax_build_invoice_vals` (BuyerDocumentNumber, BuyerTin = vat) -> XML tetap plaintext lengkap.

- E (`EfnPiiChar`) untuk partner terlindungi; B: `efn_l10n_id_nik_bidx`, `efn_l10n_id_buyer_document_number_bidx`
  (subkunci per field, normalisasi huruf-angka huruf besar) -> `=`/`in`/nilai lengkap `ilike` tetap menemukan
  (dipakai `efn_online_order._efn_invoice_party_partner`: `('l10n_id_buyer_document_number', '=', ...)`).
- Modul ini dimuat setelah `l10n_id_efaktur_coretax`, sehingga kelas field EfnPiiChar menang (R-T18).
"""

from odoo import api, fields, models

from odoo.addons.efn_pii.models.fields_pii import PROTECTED_FIELD, EfnPiiChar
from odoo.addons.efn_pii.models.res_partner import BIDX_SOURCES, PII_FIELDS
from odoo.addons.efn_pii.models.res_partner import ResPartner as EfnPiiPartner

L10N_ID_FIELDS = ("l10n_id_nik", "l10n_id_buyer_document_number")


class ResPartner(models.Model):
    _inherit = "res.partner"

    _efn_pii_fields = (*PII_FIELDS, *L10N_ID_FIELDS)
    _efn_pii_bidx_sources = (*BIDX_SOURCES, *L10N_ID_FIELDS)
    _efn_pii_bidx = {
        **EfnPiiPartner._efn_pii_bidx,
        "l10n_id_nik": ("efn_l10n_id_nik_bidx", "l10n_id_nik"),
        "l10n_id_buyer_document_number": ("efn_l10n_id_buyer_document_number_bidx", "l10n_id_buyer_document_number"),
    }

    l10n_id_nik = EfnPiiChar()
    l10n_id_buyer_document_number = EfnPiiChar()

    efn_l10n_id_nik_bidx = fields.Char(
        compute="_compute_efn_pii_bidx_l10n_id",
        store=True,
        index=True,
        readonly=True,
        copy=False,
        groups="base.group_system",
    )
    efn_l10n_id_buyer_document_number_bidx = fields.Char(
        compute="_compute_efn_pii_bidx_l10n_id",
        store=True,
        index=True,
        readonly=True,
        copy=False,
        groups="base.group_system",
    )

    @api.depends("l10n_id_nik", "l10n_id_buyer_document_number", PROTECTED_FIELD)
    def _compute_efn_pii_bidx_l10n_id(self):
        self._efn_pii_fill_bidx(L10N_ID_FIELDS)
