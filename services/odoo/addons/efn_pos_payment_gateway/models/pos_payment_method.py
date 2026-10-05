"""Terminal pembayaran POS `efn_gateway` (QRIS lewat BFF, docs/04 §8.3, docs/08 §4.1).

Pola mengikuti `pos_adyen` 19.0 (diverifikasi): `_get_payment_terminal_selection`, RPC dari
frontend lewat `pos.data.silentCall("pos.payment.method", <metode>, [[id], ...])`, tolak
`AccessDenied` bila bukan user POS. Tidak ada field API key/secret di Odoo: provider & secret
hanya di BFF; Odoo memakai service key BFF (`efn.bff.client`, HTTPS + CA internal).

Semua respons ke browser berbentuk dict `{ok, ...}`; tidak pernah membawa service key, token,
atau detail error internal.
"""

import base64
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, AccessError, ValidationError

from odoo.addons.efn_core.models.efn_bff_client import EfnBffError

from .efn_pos_payment_intent import BFF_ID_RE, FINAL_STATUSES, PAYMENTS_PATH, STATUS_KEYS, parse_bff_datetime

_logger = logging.getLogger(__name__)

TERMINAL_KEY = "efn_gateway"
UUID_RE = re.compile(r"^[0-9A-Za-z\-]{8,64}$")
DEFAULT_MAX_AMOUNT = 20_000_000
DEFAULT_CURRENCY = "IDR"


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [(TERMINAL_KEY, "EFN Payment Gateway (QRIS)")]

    # ------------------------------------------------------------- constraint
    @api.model
    def _efn_gateway_currency_name(self):
        """Mata uang yang diizinkan (kontrak: IDR). Parameter hanya untuk DB uji non-IDR."""
        value = self.env["ir.config_parameter"].sudo().get_param("efn.pos.gateway_currency", DEFAULT_CURRENCY)
        return (value or DEFAULT_CURRENCY).strip().upper()

    @api.constrains("use_payment_terminal", "journal_id", "company_id", "split_transactions", "payment_method_type")
    def _check_efn_gateway_config(self):
        currency_name = self._efn_gateway_currency_name()
        for method in self.filtered(lambda m: m.use_payment_terminal == TERMINAL_KEY):
            if "payment_method_type" in method._fields and method.payment_method_type != "terminal":
                raise ValidationError(_("Metode EFN Payment Gateway harus berintegrasi 'Terminal'."))
            if not method.company_id or not method.company_id.efn_brand_code:
                raise ValidationError(
                    _("Metode EFN Payment Gateway wajib punya company dengan Kode Company EFN.")
                )
            if not method.journal_id or method.journal_id.type != "bank":
                raise ValidationError(_("Metode EFN Payment Gateway wajib memakai jurnal bertipe Bank."))
            if method.split_transactions:
                raise ValidationError(_("Metode EFN Payment Gateway tidak boleh 'Identify Customer'."))
            journal_currency = method.journal_id.currency_id
            if journal_currency and journal_currency.name != currency_name:
                raise ValidationError(
                    _("Mata uang jurnal EFN Payment Gateway harus %(cur)s.", cur=currency_name)
                )

    # ---------------------------------------------------------------- helpers
    def _efn_gateway_check_caller(self, config):
        """User POS + metode ini milik config yang sesinya terbuka (pola pos_adyen)."""
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessDenied()
        if self.use_payment_terminal != TERMINAL_KEY:
            raise AccessDenied()
        if not config or not config.exists():
            raise AccessDenied()
        try:
            config.check_access("read")
        except AccessError:
            raise AccessDenied() from None
        if self not in config.payment_method_ids:
            raise AccessDenied()
        session = config.current_session_id
        if not session or session.state != "opened":
            return False
        return True

    @api.model
    def _efn_error(self, code, message, **extra):
        return {"ok": False, "error_code": code, "message": message, **extra}

    def _efn_intent_for_rpc(self, intent_id):
        """Intent milik metode ini + config yang bisa diakses user dengan sesi terbuka."""
        self.ensure_one()
        if isinstance(intent_id, bool) or not isinstance(intent_id, int) or intent_id <= 0:
            raise AccessDenied()
        intent = self.env["efn.pos.payment.intent"].sudo().browse(intent_id).exists()
        if not intent or intent.payment_method_id != self:
            raise AccessDenied()
        opened = self._efn_gateway_check_caller(intent.pos_config_id.with_env(self.env))
        return intent, opened

    @api.model
    def _efn_qr_image(self, qr_string):
        """PNG QR sebagai data URL untuk QRPopup standar. Gagal -> None (QR tetap di layar
        pelanggan via qr_string)."""
        try:
            png = self.env["ir.actions.report"].barcode("QR", qr_string, width=320, height=320)
        except Exception as exc:  # noqa: BLE001 - render QR tidak boleh menggagalkan pembayaran
            _logger.warning("EFN gateway: render QR gagal (%s)", type(exc).__name__)
            return None
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    @staticmethod
    def _efn_json_amount(amount):
        return int(amount) if float(amount).is_integer() else round(float(amount), 2)

    def _efn_intent_payload(self, intent, include_qr=True):
        data = {
            "ok": True,
            "intent_id": intent.id,
            "status": intent.status,
            "amount": intent.amount,
            "expires_at": fields.Datetime.to_string(intent.expires_at) + "Z" if intent.expires_at else False,
        }
        if intent.status == "paid":
            data["transaction_id"] = intent.bff_payment_id
        if include_qr and intent.status == "pending" and intent.qr_string:
            data["qr_string"] = intent.qr_string
            data["qr_image"] = self._efn_qr_image(intent.qr_string)
        return data

    # -------------------------------------------------------------------- RPC
    def efn_gateway_create(self, order_uuid, payment_uuid, amount, pos_config_id):
        """Buat (atau kembalikan) intent QRIS untuk satu baris pembayaran POS.

        Idempoten: (order_uuid, payment_uuid) yang sama dengan intent `pending`/`paid` dan
        nominal sama -> intent yang ada dikembalikan tanpa memanggil BFF. Setelah intent final
        non-paid (dibatalkan/kedaluwarsa), percobaan berikutnya memakai key `...:<n>`.
        """
        self.ensure_one()
        if isinstance(pos_config_id, bool) or not isinstance(pos_config_id, int):
            raise AccessDenied()
        config = self.env["pos.config"].browse(pos_config_id)
        if not self._efn_gateway_check_caller(config):
            return self._efn_error("session_closed", _("Sesi POS belum dibuka."))
        if not isinstance(order_uuid, str) or not UUID_RE.match(order_uuid):
            return self._efn_error("invalid_order", _("Order tidak valid."))
        if not isinstance(payment_uuid, str) or not UUID_RE.match(payment_uuid):
            return self._efn_error("invalid_payment", _("Baris pembayaran tidak valid."))
        try:
            amount_value = float(amount)
        except (TypeError, ValueError):
            return self._efn_error("invalid_amount", _("Nominal tidak valid."))
        company = config.company_id
        currency = config.currency_id or company.currency_id
        currency_name = self._efn_gateway_currency_name()
        if currency.name != currency_name:
            return self._efn_error("invalid_currency", _("QRIS hanya untuk transaksi %(cur)s.", cur=currency_name))
        amount_value = currency.round(amount_value)
        max_raw = self.env["ir.config_parameter"].sudo().get_param("efn.pos.gateway_max_amount", DEFAULT_MAX_AMOUNT)
        try:
            max_amount = float(max_raw)
        except (TypeError, ValueError):
            max_amount = DEFAULT_MAX_AMOUNT
        if currency.compare_amounts(amount_value, 0.0) <= 0 or currency.compare_amounts(amount_value, max_amount) > 0:
            return self._efn_error("invalid_amount", _("Nominal QRIS di luar batas yang diizinkan."))
        company_code = company.efn_brand_code
        if not company_code:
            _logger.error("EFN gateway: company #%s belum punya efn_brand_code", company.id)
            return self._efn_error("company_not_configured", _("Konfigurasi company belum lengkap."))

        Intent = self.env["efn.pos.payment.intent"].sudo()
        last = Intent.search(
            [("pos_order_uuid", "=", order_uuid), ("payment_uuid", "=", payment_uuid)],
            order="attempt desc, id desc",
            limit=1,
        )
        if last and last.pos_config_id != config:
            raise AccessDenied()
        if last and last.status in ("pending", "paid"):
            if currency.compare_amounts(last.amount, amount_value) != 0:
                return self._efn_error(
                    "pending_exists", _("Masih ada QRIS aktif dengan nominal berbeda. Batalkan dulu.")
                )
            return self._efn_intent_payload(last)
        attempt = (last.attempt + 1) if last else 1
        idempotency_key = f"pos:{order_uuid}:{payment_uuid}"
        if attempt > 1:
            idempotency_key = f"{idempotency_key}:{attempt}"

        payload = {
            "order_ref": order_uuid,
            "amount": self._efn_json_amount(amount_value),
            "currency": currency_name,
            "company_code": company_code,
            "pos_config_id": config.id,
            "method": "qris",
            "idempotency_key": idempotency_key,
        }
        try:
            response = self.env["efn.bff.client"].post(
                PAYMENTS_PATH, payload, log_context={"order_ref": order_uuid}
            )
        except EfnBffError:
            return self._efn_error("bff_unavailable", _("Layanan pembayaran tidak tersedia. Gunakan metode lain."))
        data = response.data
        payment_id = data.get("payment_id")
        status = data.get("status")
        qr_string = data.get("qr_string")
        if (
            response.status not in (200, 201)
            or not isinstance(payment_id, str)
            or not BFF_ID_RE.match(payment_id)
            or status not in STATUS_KEYS
        ):
            _logger.warning("EFN gateway: create ditolak BFF (HTTP %s) order %s", response.status, order_uuid)
            return self._efn_error("payment_create_failed", _("Gagal membuat QRIS. Gunakan metode lain."))
        if status == "pending" and (not isinstance(qr_string, str) or not qr_string):
            return self._efn_error("payment_create_failed", _("Gagal membuat QRIS. Gunakan metode lain."))

        existing = Intent.search([("bff_payment_id", "=", payment_id)], limit=1)
        if existing:
            # BFF idempoten per key: payment yang sama dikembalikan lagi (retry jaringan).
            if existing.pos_order_uuid != order_uuid or existing.payment_method_id != self:
                _logger.error("EFN gateway: BFF mengembalikan payment_id milik order lain")
                return self._efn_error("payment_create_failed", _("Gagal membuat QRIS. Gunakan metode lain."))
            return self._efn_intent_payload(existing)

        intent = Intent.create(
            {
                "company_id": company.id,
                "pos_config_id": config.id,
                "payment_method_id": self.id,
                "pos_order_uuid": order_uuid,
                "payment_uuid": payment_uuid,
                "attempt": attempt,
                "amount": amount_value,
                "currency_id": currency.id,
                "bff_payment_id": payment_id,
                "provider": str(data.get("provider") or "")[:32] or False,
                "status": "pending",
                "qr_string": qr_string if isinstance(qr_string, str) else False,
                "expires_at": parse_bff_datetime(data.get("expires_at")),
                "idempotency_key": idempotency_key,
            }
        )
        if status != "pending":
            intent._efn_apply_remote({"status": status})
        return self._efn_intent_payload(intent)

    def efn_gateway_status(self, intent_id):
        """Status intent (polling frontend tiap 2 s). BFF tidak terjangkau -> ok False,
        status lokal terakhir (frontend terus mencoba sampai batas waktu)."""
        intent, _opened = self._efn_intent_for_rpc(intent_id)
        if intent.status not in FINAL_STATUSES:
            try:
                intent._efn_refresh_status()
            except EfnBffError:
                return self._efn_error(
                    "bff_unavailable", _("Status pembayaran belum dapat diperiksa."), status=intent.status
                )
        return self._efn_intent_payload(intent, include_qr=False)

    def efn_gateway_cancel(self, intent_id):
        """Batalkan intent. Bila ternyata sudah `paid`, status paid dikembalikan (tidak dibatalkan)."""
        intent, _opened = self._efn_intent_for_rpc(intent_id)
        if intent.status not in FINAL_STATUSES:
            try:
                intent._efn_cancel_remote()
            except EfnBffError:
                return self._efn_error(
                    "bff_unavailable", _("Pembatalan belum dapat diproses. Coba lagi."), status=intent.status
                )
        payload = self._efn_intent_payload(intent, include_qr=False)
        payload["ok"] = intent.status in ("cancelled", "expired", "failed")
        return payload
