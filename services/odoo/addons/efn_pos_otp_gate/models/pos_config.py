"""RPC OTP untuk POS (dipanggil frontend via `pos.data.call("pos.config", ...)`).

Server Odoo yang memanggil BFF (kontrak docs/04_SERVICE_CONTRACTS.md §3.1 v2); service key
BFF tidak pernah menyentuh browser. Semua respons ke browser generik dan tidak
membocorkan apakah nomor terdaftar.

SEC-07: `member_ref` TIDAK lagi diketik kasir. Frontend hanya mengirim `partner_id`
pelanggan yang dipilih pada order; server membaca nomor HP dari record `res.partner`
(data master, hak akses user POS) dan menormalisasinya ke `^62...`. Saat consume,
`member_ref` diambil lagi dari partner FINAL order (lihat pos_order.py), sehingga
mengganti pelanggan setelah OTP membuat BFF menjawab `409 member_mismatch` -> review.
"""

import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.efn_core.models.efn_bff_client import (
    EfnBffError,
    mask_phone,
    partner_member_ref,
)

_logger = logging.getLogger(__name__)

OTP_REQUEST_PATH = "/v1/pos/otp/request"
OTP_VERIFY_PATH = "/v1/pos/otp/verify"

_ORDER_REF_RE = re.compile(r"^[0-9A-Za-z\-]{8,64}$")
_OTP_CODE_RE = re.compile(r"^\d{6}$")
DEFAULT_TOKEN_TTL = 600


class PosConfig(models.Model):
    _inherit = "pos.config"

    efn_otp_required_for_promo = fields.Boolean(
        compute="_compute_efn_pos_flags",
        help="Cermin parameter efn.pos.otp_required_for_promo (untuk frontend POS).",
    )
    efn_promo_disabled_when_offline = fields.Boolean(
        compute="_compute_efn_pos_flags",
        help="Cermin parameter efn.pos.promo_disabled_when_offline (untuk frontend POS).",
    )

    def _compute_efn_pos_flags(self):
        client = self.env["efn.bff.client"]
        otp_required = client._efn_get_bool_param("efn.pos.otp_required_for_promo", True)
        disabled_offline = client._efn_get_bool_param("efn.pos.promo_disabled_when_offline", True)
        for config in self:
            config.efn_otp_required_for_promo = otp_required
            config.efn_promo_disabled_when_offline = disabled_offline

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        # Daftar kosong = semua field ikut dimuat (perilaku pos.load.mixin); jangan dipersempit.
        if fields_list:
            extra = ["efn_otp_required_for_promo", "efn_promo_disabled_when_offline"]
            fields_list = [*fields_list, *[f for f in extra if f not in fields_list]]
        return fields_list

    # ----------------------------------------------------------------- helpers
    def _efn_check_otp_caller(self):
        """Hanya user POS dengan sesi terbuka pada config ini yang boleh memicu OTP."""
        self.ensure_one()
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(_("Anda tidak berhak memakai fitur OTP POS."))
        session = self.current_session_id
        if not session or session.state != "opened":
            raise UserError(_("Sesi POS belum dibuka."))

    @api.model
    def _efn_error(self, code, message):
        return {"ok": False, "error_code": code, "message": message}

    @api.model
    def _efn_member_error_message(self, code):
        messages = {
            "partner_missing": _("Pilih pelanggan (member) pada order terlebih dahulu."),
            "no_phone": _("Pelanggan belum punya nomor HP. Lengkapi nomor HP pelanggan dulu."),
            "foreign_phone": _(
                "Nomor HP pelanggan bukan nomor Indonesia (+62). Promo ber-OTP tidak tersedia."
            ),
            "invalid_phone": _("Nomor HP pelanggan tidak valid. Perbaiki data pelanggan."),
        }
        return messages.get(code, messages["invalid_phone"])

    def _efn_resolve_partner(self, partner_id):
        """Partner yang dipilih kasir, dibaca dengan hak akses user POS (record rule berlaku)."""
        if isinstance(partner_id, bool) or not isinstance(partner_id, int) or partner_id <= 0:
            return self.env["res.partner"]
        partner = self.env["res.partner"].browse(partner_id).exists()
        if not partner:
            return partner
        try:
            partner.check_access("read")
        except AccessError:
            return self.env["res.partner"]
        return partner

    # --------------------------------------------------------------------- RPC
    def efn_otp_request(self, order_ref, amount, partner_id):
        """Minta OTP ke BFF untuk order POS ini, ke nomor HP pelanggan order.

        :param str order_ref: `uuid` order POS.
        :param float amount: total order (Rupiah).
        :param int partner_id: id `res.partner` pelanggan yang dipilih di order. Nomor HP
            dibaca server dari record partner (tidak ada input nomor bebas dari kasir).
        :returns: dict ``{"ok": True, "member_masked", "expires_in"}`` atau
            ``{"ok": False, "error_code", "message"}``.
        """
        self._efn_check_otp_caller()
        partner = self._efn_resolve_partner(partner_id)
        member, member_error = partner_member_ref(partner)
        if not member:
            return self._efn_error(member_error, self._efn_member_error_message(member_error))
        if not isinstance(order_ref, str) or not _ORDER_REF_RE.match(order_ref):
            return self._efn_error("invalid_order", _("Order tidak valid."))
        try:
            amount_value = float(amount or 0.0)
        except (TypeError, ValueError):
            return self._efn_error("invalid_amount", _("Nominal order tidak valid."))
        if amount_value < 0:
            return self._efn_error("invalid_amount", _("Nominal order tidak valid."))
        company_code = self.company_id.efn_brand_code
        if not company_code:
            _logger.error("EFN OTP: company #%s belum punya efn_brand_code", self.company_id.id)
            return self._efn_error("company_not_configured", _("Konfigurasi company belum lengkap."))

        masked = mask_phone(member)
        payload = {
            "member_ref": member,
            "order_ref": order_ref,
            # Rupiah tanpa desimal (contoh kontrak: 125000).
            "amount": int(round(amount_value)),
            "company_code": company_code,
            "pos_config_id": self.id,
        }
        try:
            response = self.env["efn.bff.client"].post(
                OTP_REQUEST_PATH, payload, log_context={"member": masked, "order_ref": order_ref}
            )
        except EfnBffError:
            return self._efn_error("bff_unavailable", _("Layanan OTP tidak tersedia. Lanjutkan tanpa promo."))

        if response.status == 429:
            return self._efn_error("rate_limited", _("Terlalu banyak permintaan OTP. Coba lagi nanti."))
        error = response.data.get("error")
        error_code = error.get("code") if isinstance(error, dict) else None
        if response.status == 400 and error_code == "invalid_phone":
            return self._efn_error("invalid_phone", self._efn_member_error_message("invalid_phone"))
        challenge_id = response.data.get("challenge_id")
        if response.status not in (200, 202) or not isinstance(challenge_id, str) or not challenge_id:
            return self._efn_error("otp_request_failed", _("Gagal meminta OTP."))

        Challenge = self.env["efn.pos.otp.challenge"].sudo()
        # Challenge lama yang belum selesai untuk order yang sama dianggap gugur.
        Challenge.search(
            [("config_id", "=", self.id), ("order_ref", "=", order_ref), ("state", "=", "requested")]
        ).write({"state": "failed", "failure_reason": "superseded"})
        Challenge.create(
            {
                "company_id": self.company_id.id,
                "config_id": self.id,
                "order_ref": order_ref,
                "partner_id": partner.id,
                "member_ref_masked": masked,
                "amount": int(round(amount_value)),
                "bff_challenge_id": challenge_id,
            }
        )
        expires_in = response.data.get("expires_in")
        return {
            "ok": True,
            "member_masked": masked,
            "expires_in": expires_in if isinstance(expires_in, int) else 180,
        }

    def efn_otp_verify(self, order_ref, code):
        """Verifikasi OTP; bila sukses token disimpan server-side (tidak dikirim ke browser).

        :returns: ``{"ok": True, "member_masked"}`` atau
            ``{"ok": False, "reason"|"error_code", "attempts_left"?, "message"}``.
        """
        self._efn_check_otp_caller()
        if not isinstance(order_ref, str) or not _ORDER_REF_RE.match(order_ref):
            return self._efn_error("invalid_order", _("Order tidak valid."))
        code = (code or "").strip() if isinstance(code, str) else ""
        if not _OTP_CODE_RE.match(code):
            return {"ok": False, "reason": "invalid", "message": _("Kode OTP harus 6 digit.")}

        Challenge = self.env["efn.pos.otp.challenge"]
        challenge = Challenge._efn_find_latest(self, order_ref, ["requested"])
        if not challenge:
            return self._efn_error("no_challenge", _("Minta OTP terlebih dahulu."))

        payload = {
            "challenge_id": challenge.bff_challenge_id,
            "order_ref": order_ref,
            "code": code,
        }
        try:
            response = self.env["efn.bff.client"].post(
                OTP_VERIFY_PATH,
                payload,
                log_context={"member": challenge.member_ref_masked, "order_ref": order_ref},
            )
        except EfnBffError:
            return self._efn_error("bff_unavailable", _("Layanan OTP tidak tersedia. Lanjutkan tanpa promo."))

        data = response.data
        token = data.get("discount_token")
        if response.status == 200 and data.get("ok") is True and isinstance(token, str) and token:
            expires_in = data.get("expires_in")
            ttl = expires_in if isinstance(expires_in, int) and expires_in > 0 else DEFAULT_TOKEN_TTL
            challenge.write(
                {
                    "state": "verified",
                    "discount_token": token,
                    "token_expires_at": fields.Datetime.now() + timedelta(seconds=ttl),
                    # challenge_id sudah tidak diperlukan.
                    "bff_challenge_id": False,
                }
            )
            return {"ok": True, "member_masked": challenge.member_ref_masked}

        reason = data.get("reason") if data.get("reason") in ("invalid", "expired", "locked") else "invalid"
        attempts_left = data.get("attempts_left")
        if not isinstance(attempts_left, int):
            attempts_left = 0
        if reason in ("expired", "locked") or attempts_left <= 0:
            challenge.write({"state": "failed", "failure_reason": reason})
        messages = {
            "invalid": _("Kode OTP salah."),
            "expired": _("Kode OTP kedaluwarsa. Minta OTP baru."),
            "locked": _("Terlalu banyak percobaan. Minta OTP baru."),
        }
        return {
            "ok": False,
            "reason": reason,
            "attempts_left": attempts_left,
            "message": messages[reason],
        }
