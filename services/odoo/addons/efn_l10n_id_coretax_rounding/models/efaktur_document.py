"""Pemicu ketat E-40 sebelum XML e-Faktur dibuat (``download_efaktur`` -> ``_generate_xml``, ``action_regenerate``,
``action_download`` untuk dokumen tanpa lampiran). Galat penyesuaian -> ``UserError``: XML tidak dibuat, sehingga
XML yang keluar selalu punya jurnal penyesuaian yang sesuai (laporan pajak = Σ VAT XML)."""

from odoo import models


class L10nIdEfakturCoretaxDocument(models.Model):
    _inherit = "l10n_id_efaktur_coretax.document"

    def _generate_xml(self, regenerate=False):
        invoices = self.invoice_ids.filtered(lambda m: m.state == "posted" and m.move_type == "out_invoice")
        invoices._efn_coretax_sync_rounding(strict=True)
        return super()._generate_xml(regenerate=regenerate)
