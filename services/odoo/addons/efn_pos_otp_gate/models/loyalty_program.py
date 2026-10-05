from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Gelombang 3b (fit-gap E-2): OTP hanya bermakna untuk promo yang dipicu kasir/pelanggan.
# gift_card/ewallet = alat bayar; loyalty = poin (D-05/ADR-020: poin member hanya di ledger
# BFF, bukan loyalty.program Odoo); next_order_coupons = kupon terbit otomatis.
EFN_OTP_PROGRAM_TYPES = ("promotion", "buy_x_get_y", "promo_code", "coupons")


class LoyaltyProgram(models.Model):
    _inherit = "loyalty.program"

    efn_requires_otp = fields.Boolean(
        string="Wajib OTP Pelanggan (EFN)",
        help="Bila aktif (dan parameter efn.pos.otp_required_for_promo = True), reward program ini "
        "di POS hanya bisa dipakai setelah pelanggan memverifikasi OTP WhatsApp lewat BFF. "
        "Server menolak order berbayar yang membawa reward ini tanpa discount_token valid. "
        "Program ini otomatis tidak tersedia saat POS offline (ADR-017 opsi a). "
        "Hanya untuk jenis Promotion, Buy X Get Y, Promo Code, Coupons; wajib dibatasi ke "
        "Point of Sale kasir (bukan kiosk self-order).",
    )

    @api.constrains("efn_requires_otp", "program_type", "pos_ok", "pos_config_ids")
    def _check_efn_requires_otp(self):
        for program in self.filtered("efn_requires_otp"):
            if program.program_type not in EFN_OTP_PROGRAM_TYPES:
                raise ValidationError(
                    _(
                        "Wajib OTP hanya untuk program Promotion, Buy X Get Y, Promo Code, atau "
                        "Coupons (bukan '%(type)s').",
                        type=program.program_type,
                    )
                )
            if not program.pos_ok:
                continue
            # Kosong = berlaku di SEMUA POS (termasuk kiosk) -> wajib eksplisit.
            if not program.pos_config_ids:
                raise ValidationError(
                    _(
                        "Program wajib OTP harus dibatasi ke Point of Sale kasir tertentu "
                        "(isi 'Point of Sale'); kiosk self-order tidak punya alur OTP."
                    )
                )
            configs = program.pos_config_ids
            if "self_ordering_mode" in configs._fields:  # pos_self_order terpasang
                kiosks = configs.filtered(lambda c: c.self_ordering_mode == "kiosk")
                if kiosks:
                    raise ValidationError(
                        _(
                            "Program wajib OTP tidak boleh ditautkan ke kiosk self-order: %(names)s.",
                            names=", ".join(kiosks.mapped("name")),
                        )
                    )

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        # Daftar kosong berarti "semua field"; jangan ubah menjadi hanya field kita.
        if fields_list and "efn_requires_otp" not in fields_list:
            fields_list = [*fields_list, "efn_requires_otp"]
        return fields_list
