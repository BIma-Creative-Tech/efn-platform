"""E-30 (docs/08 §9.2, kontrak docs/04 §9.9 K-18-1): metode bayar "Poin EFN" per PT.

Redeem Poin EFN = baris pembayaran (`pos.payment`) dengan metode ber-`efn_is_loyalty_points`, bukan lagi
baris produk diskon. DPP & PPN/PB1 tetap atas harga penuh (keputusan pemilik proyek P-8).
Konfigurasi akuntansi (jurnal bank "Poin EFN" tanpa rekening, `outstanding_account_id` = akun liabilitas
/kliring poin PT) diisi modul konfigurasi `efn_setup_pos` (PLACEHOLDER Finance, Q-F18-3).

Server mendeteksi pembayaran poin dari metode ini (SEC-12: bukan flag klien).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if fields_list and "efn_is_loyalty_points" not in fields_list:
            fields_list = [*fields_list, "efn_is_loyalty_points"]
        return fields_list
