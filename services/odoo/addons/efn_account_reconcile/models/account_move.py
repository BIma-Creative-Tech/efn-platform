"""Reset ke draft membatalkan rekonsiliasi lebih dulu.

Port ADR-049 dari `custom_account_reconcile/models/account_move.py`.

Terverifikasi di CE 19 (`account/models/account_move.py` `button_draft`): tidak memanggil `remove_move_reconcile()`,
sehingga pembayaran yang di-reset ke draft tetap menempel di tagihannya — tagihan terbaca "Paid" padahal entri
pembayaran keluar dari neraca saldo (hanya move posted), dan umur hutang tidak lagi cocok dengan neraca saldo.
Sesudah draft, akun baris yang masih punya partial juga bisa diganti dan merusak pencocokan permanen.

Solusi: unreconcile SEBELUM `super()` (saat move masih posted, partial konsisten dan lock date ditolak sebelum state
berubah), lalu catat dokumen yang dilepas di chatter. Override ini bersama `efn_intercompany`/`efn_account_deferred`/
`efn_account_asset` — semuanya memanggil `super()` (MRO).
"""

from odoo import _, models


class AccountMove(models.Model):
    _inherit = "account.move"

    def button_draft(self):
        for move in self:
            matched = move.line_ids.filtered(lambda line: line.matched_debit_ids or line.matched_credit_ids)
            if not matched:
                continue
            counterparts = (
                matched.matched_debit_ids.debit_move_id
                | matched.matched_debit_ids.credit_move_id
                | matched.matched_credit_ids.debit_move_id
                | matched.matched_credit_ids.credit_move_id
            ).move_id - move
            matched.remove_move_reconcile()
            move.message_post(
                body=_(
                    "Reset ke draft: rekonsiliasi dibatalkan lebih dulu sehingga dokumen pasangannya terbuka kembali. "
                    "Alokasikan ulang setelah entri ini di-post lagi. Dokumen yang dilepas: %(docs)s",
                    docs=", ".join(sorted(counterparts.mapped("name"))) or "-",
                )
            )
        return super().button_draft()
