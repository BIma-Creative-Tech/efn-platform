"""G60-B (estimasi MDR) + G60-C (analitik Outlet) pada tutup sesi POS.

CE 19 ``_validate_session`` -> ``_create_account_move`` (sudo untuk user POS) -> ``_create_bank_payment_moves`` (payment
per metode bank sudah dibuat & posted: Dr outstanding / Cr piutang POS) -> ... -> jurnal sesi di-post setelahnya.

- MDR: setelah super ``_create_bank_payment_moves``, per metode bank yang punya ``efn.mdr.rate`` berlaku dibuat satu
  jurnal ``Dr akun beban MDR (analitik Outlet) / Cr akun outstanding metode`` dan di-post. Gagal/konfigurasi kurang =
  dilewati + pesan chatter + log WARNING (tutup sesi = jalur transaksi; MDR bukan). Selisih ke potongan bank aktual
  (true-up) = reconcile model di UI rekonsiliasi G58.
- Outlet: setelah super ``_create_account_move`` (jurnal sesi masih draft), baris laba-rugi dicap akun analitik gudang
  outlet (``config_id.warehouse_id.efn_analytic_account_id``), digabung dengan distribusi bawaan.

Ditulis ulang dari ide odoo-platform `custom_levis_localization/models/pos_session.py` (stamping OU di semua baris);
EFN hanya mencap baris laba-rugi (Q-G60-8) lewat satu titik pasca-pembuatan, bukan override per ``_get_*_vals``.
"""

import logging

from odoo import Command, _, fields, models
from odoo.exceptions import UserError

from .account_move import ACCOUNTING_READERS
from .outlet_analytic import stamp_outlet

_logger = logging.getLogger(__name__)


class PosSession(models.Model):
    _inherit = "pos.session"

    efn_mdr_move_ids = fields.One2many(
        "account.move", "efn_mdr_pos_session_id", string="Jurnal MDR", readonly=True, groups=ACCOUNTING_READERS
    )
    efn_mdr_move_count = fields.Integer(compute="_compute_efn_mdr_move_count", groups=ACCOUNTING_READERS)

    def _compute_efn_mdr_move_count(self):
        for session in self:
            session.efn_mdr_move_count = len(session.efn_mdr_move_ids)

    # ------------------------------------------------------------------------------------------------ outlet
    def _efn_outlet_analytic(self):
        self.ensure_one()
        return self.config_id.warehouse_id.sudo().efn_analytic_account_id

    def _efn_stamp_outlet(self, lines, warn=True):
        outlet = self._efn_outlet_analytic()
        stamped = stamp_outlet(lines, outlet)
        if not outlet and warn and self.company_id.efn_outlet_analytic_plan_id:
            has_pnl = lines.filtered(lambda line: line.account_id.internal_group in ("income", "expense"))
            if has_pnl:
                self.message_post(
                    body=_(
                        "Gudang POS ini belum dipetakan ke akun analitik Outlet: laba-rugi sesi tidak tercatat per "
                        "outlet. Jalankan Akuntansi > Konfigurasi > Siapkan Analitik Outlet."
                    )
                )
        return stamped

    def _create_account_move(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        data = super()._create_account_move(balancing_account, amount_to_balance, bank_payment_method_diffs)
        if self.move_id:
            self._efn_stamp_outlet(self.move_id.line_ids)
        return data

    # --------------------------------------------------------------------------------------------------- MDR
    def _create_bank_payment_moves(self, data):
        data = super()._create_bank_payment_moves(data)
        self._efn_create_mdr_moves()
        return data

    def _efn_create_mdr_moves(self):
        self.ensure_one()
        session = self.sudo().with_company(self.company_id)
        company = session.company_id
        date = session.move_id.date or fields.Date.context_today(session)
        Rate = session.env["efn.mdr.rate"]
        payments = session.order_ids.filtered(lambda order: order.state != "cancel").payment_ids
        for method in session.payment_method_ids.filtered(lambda m: m.type == "bank"):
            rate = Rate._efn_rate_for(method, date)
            method_payments = payments.filtered(lambda p, m=method: p.payment_method_id == m)
            if not rate or not method_payments:
                continue
            # Neto metode (refund mengurangi MDR, Q-G60-7); biaya tetap hanya per transaksi penjualan (amount > 0).
            amount = sum(method_payments.mapped("amount"))
            if session.currency_id != company.currency_id:
                amount = session.currency_id._convert(amount, company.currency_id, company, date)
            count = len(method_payments.filtered(lambda p: p.amount > 0))
            mdr = rate._efn_compute(amount, count)
            if company.currency_id.is_zero(mdr):
                continue
            try:
                with session.env.cr.savepoint():
                    session._efn_create_mdr_move(method, rate, mdr, date)
            except Exception as exc:  # noqa: BLE001 - savepoint di-rollback; MDR tidak boleh memblok tutup sesi
                reason = exc.args[0] if isinstance(exc, UserError) and exc.args else type(exc).__name__
                _logger.warning(
                    "EFN G60: estimasi MDR sesi %s metode %s dilewati (%s)", session.id, method.id, type(exc).__name__
                )
                session.message_post(
                    body=_("Estimasi MDR %(method)s tidak dibuat: %(reason)s", method=method.name, reason=reason)
                )

    def _efn_mdr_credit_account(self, method):
        """Akun outstanding yang benar-benar dipakai payment sesi untuk metode ini (CE memakai default chart bila
        metode tanpa akun outstanding)."""
        payment = self.env["account.payment"].search(
            [("pos_session_id", "=", self.id), ("pos_payment_method_id", "=", method.id)], limit=1
        )
        return payment.outstanding_account_id or method.outstanding_account_id

    def _efn_mdr_journal(self):
        return self.company_id._efn_mdr_journal()

    def _efn_create_mdr_move(self, method, rate, mdr, date):
        journal = self._efn_mdr_journal()
        credit_account = self._efn_mdr_credit_account(method)
        if not journal or not credit_account:
            raise UserError(_("jurnal MDR (Pengaturan Akuntansi) atau akun outstanding metode belum dikonfigurasi."))
        label = _("MDR estimasi %(session)s %(method)s", session=self.name, method=method.name)
        move = self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": journal.id,
                "company_id": self.company_id.id,
                "date": date,
                "ref": label,
                "efn_mdr_pos_session_id": self.id,
                "line_ids": [
                    Command.create({"name": label, "account_id": rate.account_id.id, "balance": mdr}),
                    Command.create({"name": label, "account_id": credit_account.id, "balance": -mdr}),
                ],
            }
        )
        self._efn_stamp_outlet(move.line_ids, warn=False)
        move._post()
        return move
