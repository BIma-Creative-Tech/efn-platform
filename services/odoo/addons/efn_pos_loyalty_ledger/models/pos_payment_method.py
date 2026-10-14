"""E-30 (docs/08 §9.2, kontrak docs/04 §9.9 K-18-1): metode bayar "Poin EFN" per PT.

Redeem Poin EFN = baris pembayaran (`pos.payment`) dengan metode ber-`efn_is_loyalty_points`, bukan lagi
baris produk diskon. DPP & PPN/PB1 tetap atas harga penuh (keputusan pemilik proyek P-8).
Konfigurasi akuntansi (jurnal bank "Poin EFN" tanpa rekening, `outstanding_account_id` = akun liabilitas
/kliring poin PT) diisi modul konfigurasi `efn_setup_pos` (PLACEHOLDER Finance, Q-F18-3).

Server mendeteksi pembayaran poin dari metode ini (SEC-12: bukan flag klien).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    efn_is_loyalty_points = fields.Boolean(
        string="Metode Poin EFN",
        copy=False,
        help="Metode bayar penukaran Poin EFN (ledger BFF). Hanya bisa dipakai lewat tombol 'Tukar Poin EFN' "
        "(OTP + reservasi BFF); tidak bisa dipilih manual kasir. Jurnal bank tanpa terminal, satu per company.",
    )

    @api.constrains(
        "efn_is_loyalty_points",
        "journal_id",
        "use_payment_terminal",
        "payment_method_type",
        "split_transactions",
        "company_id",
        "active",
    )
    def _check_efn_loyalty_points_method(self):
        for method in self.filtered("efn_is_loyalty_points"):
            if method.journal_id.type != "bank" or method.is_cash_count:
                raise ValidationError(_("Metode Poin EFN wajib memakai jurnal bertipe Bank (bukan kas)."))
            if method.use_payment_terminal or method.payment_method_type != "none":
                raise ValidationError(_("Metode Poin EFN tidak boleh memakai terminal pembayaran atau QR."))
            if method.split_transactions:
                raise ValidationError(_("Metode Poin EFN tidak boleh memisahkan transaksi per pelanggan."))
            if not method.active:
                continue
            others = self.search_count(
                [
                    ("id", "!=", method.id),
                    ("efn_is_loyalty_points", "=", True),
                    ("company_id", "=", method.company_id.id),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if others:
                raise ValidationError(_("Hanya boleh ada satu metode Poin EFN aktif per company."))

    @api.model_create_multi
    def create(self, vals_list):
        """SEC-119: metode baru ber-flag Poin EFN hanya dibuat admin (Settings) atau data modul."""
        if any(vals.get("efn_is_loyalty_points") for vals in vals_list):
            self._efn_check_points_flag_admin()
        return super().create(vals_list)

    def write(self, vals):
        """SEC-119 (L-25g): flag Poin EFN hanya diubah admin (Settings, tercatat log), dan tidak bisa diubah oleh
        siapa pun (dua arah) bila metode sudah punya pembayaran; metode Poin EFN berpembayaran juga tidak bisa
        diarsipkan atau diganti jurnalnya (deteksi server & rekonsiliasi ETL retroaktif bergantung pada flag ini).
        Hanya superuser (migrasi/data modul) yang dikecualikan."""
        if not self.env.user._is_superuser():
            flag_changed = self.filtered(
                lambda m: (
                    "efn_is_loyalty_points" in vals
                    and bool(vals["efn_is_loyalty_points"]) != bool(m.efn_is_loyalty_points)
                )
            )
            if flag_changed:
                self._efn_check_points_flag_admin()
            for method in self:
                points_change = method.efn_is_loyalty_points and (
                    ("active" in vals and not vals["active"])
                    or ("journal_id" in vals and vals["journal_id"] != method.journal_id.id)
                )
                if (method in flag_changed or points_change) and method._efn_has_payments():
                    raise UserError(
                        _(
                            "Metode %(name)s sudah dipakai pembayaran; penanda Poin EFN, jurnal, dan status aktifnya "
                            "tidak dapat diubah (SEC-119).",
                            name=method.name,
                        )
                    )
            if flag_changed:
                _logger.warning(
                    "EFN Poin: user %s mengubah penanda Poin EFN metode %s -> %s (SEC-119)",
                    self.env.uid,
                    flag_changed.ids,
                    bool(vals["efn_is_loyalty_points"]),
                )
        return super().write(vals)

    def _efn_check_points_flag_admin(self):
        user = self.env.user
        if not (user._is_superuser() or user.has_group("base.group_system")):
            raise AccessError(_("Penanda metode Poin EFN hanya dapat diatur admin (SEC-119)."))

    def _efn_has_payments(self):
        self.ensure_one()
        return bool(self.env["pos.payment"].sudo().search_count([("payment_method_id", "=", self.id)], limit=1))

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if fields_list and "efn_is_loyalty_points" not in fields_list:
            fields_list = [*fields_list, "efn_is_loyalty_points"]
        return fields_list
