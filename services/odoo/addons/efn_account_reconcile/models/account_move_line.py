"""Tolak perubahan akun/partner pada item jurnal yang masih direkonsiliasi (port ADR-049 dari
`custom_account_reconcile/models/account_move_line.py` + `custom_levis_bank_reconcile/models/account_move_line.py`).

`account.move.line._check_reconciliation` ada di CE 19 tetapi tidak dipanggil apa pun. Rekonsiliasi hanya bermakna
bila kedua sisi ada di akun & partner yang sama, jadi dua perubahan itu merusaknya diam-diam. Nominal sengaja TIDAK
dijaga: mesin CE menulis debit/credit/amount_currency pada baris rekonsiliasi saat membukukan selisih kurs.

Perubahan EFN: escape hatch context `skip_reconciliation_guard` hanya dihormati bila `env.su` (di sumber cukup
context, yang bisa diisi klien RPC).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: Field yang membuat rekonsiliasi tidak bermakna bila berubah di bawahnya.
STRUCTURAL_FIELDS = ("account_id", "partner_id")


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    efn_transaction_ref = fields.Char(
        string="No. Transaksi",
        compute="_compute_efn_transaction_ref",
        help="Nomor yang dikenali di luar Odoo: referensi baris/entri, referensi pembayaran faktur, atau dokumen asal. "
        "Nomor entri hanya bila tidak ada yang lebih baik.",
    )

    @api.depends("ref", "move_id.ref", "move_id.payment_reference", "move_id.invoice_origin", "move_id.name")
    def _compute_efn_transaction_ref(self):
        for line in self:
            move = line.move_id
            candidates = (
                line.ref,
                move.ref,
                move.payment_reference if move.is_invoice(include_receipts=True) else None,
                move.invoice_origin,
                move.name,
            )
            line.efn_transaction_ref = next((str(c).strip() for c in candidates if c and str(c).strip()), False)

    def write(self, vals):
        if not (self.env.su and self.env.context.get("skip_reconciliation_guard")):
            self._efn_assert_not_matched(vals)
        return super().write(vals)

    def _efn_assert_not_matched(self, vals):
        """Tolak bila baris yang masih punya partial benar-benar mengubah salah satu :data:`STRUCTURAL_FIELDS`
        (state move apa pun). Dibandingkan dengan nilai tersimpan: penulisan no-op tetap boleh."""
        touched = [fname for fname in STRUCTURAL_FIELDS if fname in vals]
        if not touched:
            return
        for line in self:
            if not (line.matched_debit_ids or line.matched_credit_ids):
                continue
            changed = [fname for fname in touched if (line[fname].id or False) != (vals[fname] or False)]
            if not changed:
                continue
            raise UserError(
                _(
                    "%(fields)s tidak dapat diubah pada item jurnal yang masih direkonsiliasi — pencocokannya akan "
                    "tetap menautkan dua dokumen yang tidak lagi berpasangan, tanpa terlihat di neraca saldo maupun "
                    "laporan umur.\n\nBatalkan rekonsiliasi %(entry)s dulu, ubah, lalu cocokkan lagi.",
                    fields=", ".join(self._fields[fname].string for fname in changed),
                    entry=line.move_id.name or _("entri ini"),
                )
            )
