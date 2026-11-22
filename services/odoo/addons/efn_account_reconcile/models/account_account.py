"""Penanda akun yang tidak boleh dicocokkan otomatis (G58 §3.3; ide generik dari Levi's: setoran tunai)."""

from odoo import fields, models


class AccountAccount(models.Model):
    _inherit = "account.account"

    efn_no_auto_match = fields.Boolean(
        string="EFN: Jangan Cocokkan Otomatis",
        help="Centang untuk akun outstanding kas POS / setoran tunai. Satu setoran bank biasanya gabungan beberapa "
        "hari, sehingga kecocokan nominal tunggal adalah kebetulan: baris mutasi dengan kandidat di akun ini tidak "
        "pernah dicocokkan otomatis, hanya lewat wizard (tombol 'Isi otomatis').",
    )
