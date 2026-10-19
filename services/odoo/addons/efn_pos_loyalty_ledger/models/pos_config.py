"""Konfigurasi & RPC Poin EFN untuk POS (docs/08 §6.3 B/F + §9.2 E-30, kontrak docs/04 §9 v6 / §9.9).

G18 (E-30, K-18-1): redeem = baris pembayaran metode "Poin EFN" (`pos.payment.method.efn_is_loyalty_points`)
senilai `value_idr`, bukan lagi produk diskon. Config mengekspos id metode itu ke frontend.

RPC dipanggil frontend via `pos.data.call("pos.config", ...)` dengan pola `efn_otp_request`:
user POS dengan sesi terbuka pada config ini, partner dibaca server (record rule berlaku),
`member_ref` dinormalisasi `efn_core.partner_member_ref`, log hanya nomor ter-mask. Service key
BFF, `discount_token`, dan `reservation_id` BFF tidak pernah dikirim ke browser.
"""

import logging
import re
import threading
import time
from datetime import UTC, datetime

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from odoo.addons.efn_core.models.efn_bff_client import EfnBffError, mask_phone, partner_member_ref
from odoo.addons.efn_pos_otp_gate.models.efn_pos_otp_challenge import PURPOSE_REDEEM

_logger = logging.getLogger(__name__)

BALANCE_PATH = "/v1/pos/loyalty/balance"
PARAMS_PATH = "/v1/pos/loyalty/params"
RESERVE_PATH = "/v1/pos/loyalty/redeem/reserve"
RELEASE_PATH = "/v1/pos/loyalty/redeem/release"

_ORDER_REF_RE = re.compile(r"^[0-9A-Za-z\-]{8,64}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_POINTS = 1_000_000
RESERVE_REJECT_REASONS = (
    "insufficient_points",
    "below_minimum",
    "exceeds_max_pct",
    "token_invalid",
    "member_mismatch",
    "amount_mismatch",
    "reservation_exists",
)
# Alasan yang membuat token OTP tidak bisa dipakai lagi -> challenge gagal (minta OTP baru).
TOKEN_FINAL_REASONS = frozenset({"token_invalid", "member_mismatch", "amount_mismatch"})
DEFAULT_BALANCE_RATE_PER_MINUTE = 20
PARAMS_CACHE_SECONDS = 300
DEFAULT_REDEEM_MAX_PCT = 50  # = default BFF LOYALTY_REDEEM_MAX_PCT_OF_ORDER


def parse_bff_datetime(value):
    """ISO-8601 dari BFF (mis. `2026-10-07T03:15:00.000Z`) -> datetime UTC naive (format ORM)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


# Rate limit saldo per sesi POS: in-process (per worker). Jaring pengaman utama ada di BFF
# (K-8: per member & per pos_config). Kunci = (dbname, session_id).
_BALANCE_CALLS = {}
_BALANCE_LOCK = threading.Lock()
_PARAMS_CACHE = {}


class PosConfig(models.Model):
    _inherit = "pos.config"

    efn_loyalty_earn_enabled = fields.Boolean(
        string="Poin EFN: earn",
        help="Order berbayar dengan pelanggan ber-HP Indonesia dikirim ke ledger BFF untuk menambah Poin EFN.",
    )
    efn_loyalty_redeem_enabled = fields.Boolean(
        string="Poin EFN: tukar poin",
        help="Tombol 'Tukar Poin EFN' di layar pembayaran (wajib OTP pelanggan, hanya saat online). "
        "Tidak tersedia untuk kiosk self-order.",
    )
    efn_loyalty_points_method = fields.Integer(
        compute="_compute_efn_loyalty_points_method",
        help="Id metode bayar 'Poin EFN' di POS ini (0 = tidak ada; tombol tukar poin disembunyikan). "
        "Server tetap mendeteksi pembayaran poin dari metode, bukan dari klien.",
    )

    @api.depends("payment_method_ids", "payment_method_ids.efn_is_loyalty_points")
    def _compute_efn_loyalty_points_method(self):
        for config in self:
            config.efn_loyalty_points_method = config._efn_loyalty_points_method_record().id or 0

    def _efn_loyalty_points_method_record(self):
        self.ensure_one()
        return self.sudo().payment_method_ids.filtered(lambda m: m.efn_is_loyalty_points and m.active)[:1]

    @api.constrains("efn_loyalty_redeem_enabled")
    def _check_efn_loyalty_redeem_kiosk(self):
        if "self_ordering_mode" not in self._fields:
            return
        for config in self:
            if config.efn_loyalty_redeem_enabled and config.self_ordering_mode == "kiosk":
                raise ValidationError(
                    _("Tukar Poin EFN tidak boleh diaktifkan pada kiosk self-order (tidak ada alur OTP).")
                )

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if fields_list:
            extra = ["efn_loyalty_earn_enabled", "efn_loyalty_redeem_enabled", "efn_loyalty_points_method"]
            fields_list = [*fields_list, *[f for f in extra if f not in fields_list]]
        return fields_list

    # ------------------------------------------------------------- helpers
    def _efn_loyalty_partner_member(self, partner_id):
        partner = self._efn_resolve_partner(partner_id)
        member, error = partner_member_ref(partner)
        return partner, member, error

    def _efn_loyalty_rate_limited(self):
        """True bila sesi melewati batas cek saldo per menit (in-process, per worker)."""
        self.ensure_one()
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("efn.pos.loyalty.balance_rate_per_minute", DEFAULT_BALANCE_RATE_PER_MINUTE)
        )
        try:
            limit = max(int(raw), 1)
        except (TypeError, ValueError):
            limit = DEFAULT_BALANCE_RATE_PER_MINUTE
        key = (self.env.cr.dbname, self.current_session_id.id)
        now = time.monotonic()
        with _BALANCE_LOCK:
            calls = [stamp for stamp in _BALANCE_CALLS.get(key, []) if now - stamp < 60]
            if len(calls) >= limit:
                _BALANCE_CALLS[key] = calls
                return True
            calls.append(now)
            _BALANCE_CALLS[key] = calls
            if len(_BALANCE_CALLS) > 1000:  # cegah tumbuh tanpa batas
                for stale in [k for k, v in _BALANCE_CALLS.items() if not v or now - v[-1] > 60]:
                    _BALANCE_CALLS.pop(stale, None)
        return False

    def _efn_loyalty_params_key(self):
        """Kunci cache params: (db, kode PT config). G21 §13.1 no. 4: kebijakan aktif per PT."""
        return (self.env.cr.dbname, self.sudo().company_id.efn_brand_code or "")

    def _efn_loyalty_params(self):
        """Parameter §9.1 dari BFF, di-cache 5 menit per worker per PT.

        G21 (docs/04 §13.1 no. 4, E-44): ``GET /v1/pos/loyalty/params?company_code=<PT config>`` -> kebijakan aktif PT
        itu (versi tertinggi dengan effective_from <= now; tanpa kebijakan BFF memakai env lama). Tanpa kode PT ->
        tanpa query (perilaku lama, env BFF).
        """
        key = self._efn_loyalty_params_key()
        cached = _PARAMS_CACHE.get(key)
        if cached and time.monotonic() - cached[0] < PARAMS_CACHE_SECONDS:
            return cached[1]
        company_code = key[1]
        try:
            response = self.env["efn.bff.client"]._efn_get(
                PARAMS_PATH, params={"company_code": company_code} if company_code else None
            )
        except EfnBffError:
            return {}
        if response.status != 200:
            return {}
        params = {
            k: response.data[k]
            for k in ("point_value_idr", "redeem_min_points", "redeem_max_pct_of_order")
            if isinstance(response.data.get(k), (int, float)) and not isinstance(response.data.get(k), bool)
        }
        # K-18-4 / L-24: flag dasar earn dari BFF (faktor dihitung Odoo; BFF hanya pagar kasar).
        if isinstance(response.data.get("earn_exclude_point_payment"), bool):
            params["earn_exclude_point_payment"] = response.data["earn_exclude_point_payment"]
        _PARAMS_CACHE[key] = (time.monotonic(), params)
        return params

    def _efn_loyalty_cached_params(self):
        """Parameter §9.1 dari cache saja (tanpa panggilan BFF) -- dipakai saat sync order agar sync tidak
        bergantung/menunggu BFF. Cache diisi saat cek saldo/reserve (TTL 5 menit, per worker)."""
        cached = _PARAMS_CACHE.get(self._efn_loyalty_params_key())
        if cached and time.monotonic() - cached[0] < PARAMS_CACHE_SECONDS:
            return cached[1]
        return {}

    def _efn_loyalty_redeem_max_pct(self):
        """SEC-62: batas % dari parameter BFF (cache/GET §9.1), fallback ir.config_parameter."""
        pct = self._efn_loyalty_params().get("redeem_max_pct_of_order")
        if not isinstance(pct, (int, float)) or isinstance(pct, bool) or not 0 < pct <= 100:
            raw = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("efn.pos.loyalty.redeem_max_pct_fallback", DEFAULT_REDEEM_MAX_PCT)
            )
            try:
                pct = int(raw)
            except (TypeError, ValueError):
                pct = DEFAULT_REDEEM_MAX_PCT
        return max(1, min(100, int(pct)))

    def _efn_loyalty_check_redeem(self):
        self._efn_check_otp_caller()
        if not self.efn_loyalty_redeem_enabled:
            raise AccessError(_("Tukar Poin EFN tidak aktif untuk Point of Sale ini."))
        if not self._efn_loyalty_points_method_record():
            raise AccessError(_("Metode bayar 'Poin EFN' belum dipasang di Point of Sale ini."))

    @api.model
    def _efn_reserve_message(self, reason):
        messages = {
            "insufficient_points": _("Poin pelanggan tidak mencukupi."),
            "below_minimum": _("Jumlah poin di bawah minimum penukaran."),
            "exceeds_max_pct": _("Nilai poin melebihi batas maksimum per transaksi."),
            "token_invalid": _("Verifikasi OTP tidak berlaku lagi. Minta OTP baru."),
            "member_mismatch": _("Pelanggan tidak sesuai dengan verifikasi OTP."),
            "amount_mismatch": _("Total transaksi berubah sejak OTP. Minta OTP baru."),
            "reservation_exists": _("Transaksi ini sudah punya penukaran poin aktif."),
        }
        return messages.get(reason, _("Penukaran poin ditolak."))

    # --------------------------------------------------------------------- RPC
    def efn_loyalty_balance(self, partner_id):
        """Saldo Poin EFN pelanggan (K-8: tanpa OTP; hanya saldo). Online saja.

        :returns: ``{"ok": True, "points_balance", "point_value_idr"?, "redeem_min_points"?,
            "member_masked"}`` atau ``{"ok": False, "error_code", "message"}``.
        """
        self._efn_check_otp_caller()
        if not (self.efn_loyalty_earn_enabled or self.efn_loyalty_redeem_enabled):
            return self._efn_error("loyalty_disabled", _("Poin EFN tidak aktif untuk Point of Sale ini."))
        _partner, member, error = self._efn_loyalty_partner_member(partner_id)
        if not member:
            return self._efn_error(error, self._efn_member_error_message(error))
        if not self.company_id.efn_brand_code:
            return self._efn_error("company_not_configured", _("Konfigurasi company belum lengkap."))
        if self._efn_loyalty_rate_limited():
            return self._efn_error("rate_limited", _("Terlalu sering cek poin. Coba lagi sebentar."))
        masked = mask_phone(member)
        try:
            # §9.7 (K-8): rate limit BFF per member & per pos_config.
            response = self.env["efn.bff.client"]._efn_post(
                BALANCE_PATH,
                {"member_ref": member, "pos_config_id": self.id, "company_code": self.company_id.efn_brand_code},
                log_context={"member": masked},
            )
        except EfnBffError:
            return self._efn_error("bff_unavailable", _("Layanan poin tidak tersedia."))
        balance = response.data.get("points_balance")
        if response.status != 200 or not isinstance(balance, int) or isinstance(balance, bool):
            if response.status == 429:
                return self._efn_error("rate_limited", _("Terlalu sering cek poin. Coba lagi sebentar."))
            return self._efn_error("balance_failed", _("Gagal membaca saldo poin."))
        result = {"ok": True, "points_balance": balance, "member_masked": masked}
        result.update({k: v for k, v in self._efn_loyalty_params().items() if k != "earn_exclude_point_payment"})
        return result

    def efn_loyalty_redeem_reserve(self, order_uuid, partner_id, points, amount_total):
        """Reservasi poin di BFF setelah OTP `pos_redeem` terverifikasi (docs/04 §9.3 #2).

        :returns: ``{"ok": True, "reservation_id" (id lokal), "points", "value_idr", "expires_at"}``
            atau ``{"ok": False, "error_code", "message"}``.
        """
        self._efn_loyalty_check_redeem()
        if not isinstance(order_uuid, str) or not _ORDER_REF_RE.match(order_uuid):
            return self._efn_error("invalid_order", _("Order tidak valid."))
        if isinstance(points, bool) or not isinstance(points, int) or not 0 < points <= MAX_POINTS:
            return self._efn_error("invalid_points", _("Jumlah poin tidak valid."))
        try:
            amount_value = float(amount_total)
        except (TypeError, ValueError):
            return self._efn_error("invalid_amount", _("Nominal order tidak valid."))
        if amount_value <= 0:
            return self._efn_error("invalid_amount", _("Nominal order tidak valid."))
        partner, member, error = self._efn_loyalty_partner_member(partner_id)
        if not member:
            return self._efn_error(error, self._efn_member_error_message(error))
        Reservation = self.env["efn.pos.loyalty.reservation"]
        if Reservation._efn_find_active(self, order_uuid):
            return self._efn_error("reservation_exists", self._efn_reserve_message("reservation_exists"))
        challenge = self.env["efn.pos.otp.challenge"]._efn_find_valid_verified(self, order_uuid, purpose=PURPOSE_REDEEM)
        if not challenge or not challenge.discount_token or challenge.partner_id != partner:
            return self._efn_error("otp_required", _("Verifikasi OTP pelanggan diperlukan untuk tukar poin."))

        masked = mask_phone(member)
        payload = {
            "discount_token": challenge.discount_token,
            "order_ref": order_uuid,
            "member_ref": member,
            "points": points,
            "amount_total": int(round(amount_value)),
        }
        try:
            response = self.env["efn.bff.client"]._efn_post(
                RESERVE_PATH, payload, log_context={"member": masked, "order_ref": order_uuid}
            )
        except EfnBffError as exc:
            # Status token di BFF tidak diketahui (bisa sudah dipakai) -> OTP baru diperlukan.
            challenge.write({"state": "failed", "failure_reason": exc.code, "discount_token": False})
            return self._efn_error("bff_unavailable", _("Layanan poin tidak tersedia. Lanjutkan tanpa tukar poin."))

        data = response.data
        if response.status != 200:
            error_obj = data.get("error")
            reason = error_obj.get("code") if isinstance(error_obj, dict) else data.get("reason")
            if reason not in RESERVE_REJECT_REASONS:
                reason = "reserve_failed"
            if reason in TOKEN_FINAL_REASONS or reason == "reserve_failed":
                challenge.write({"state": "failed", "failure_reason": reason, "discount_token": False})
            return self._efn_error(reason, self._efn_reserve_message(reason))

        bff_id = data.get("reservation_id")
        value_idr = data.get("value_idr")
        reserved_points = data.get("points")
        if (
            not isinstance(bff_id, str)
            or not _UUID_RE.match(bff_id)
            or not isinstance(value_idr, int)
            or isinstance(value_idr, bool)
            or value_idr <= 0
            or reserved_points != points
        ):
            _logger.error("EFN loyalty: respons reserve BFF tidak valid untuk order %s", order_uuid)
            challenge.write({"state": "failed", "failure_reason": "reserve_bad_response", "discount_token": False})
            return self._efn_error("reserve_failed", self._efn_reserve_message("reserve_failed"))
        expires_at = parse_bff_datetime(data.get("expires_at"))
        partner._efn_mark_member()  # SEC-115: kunci HP member permanen
        reservation = Reservation.sudo().create(
            {
                "company_id": self.company_id.id,
                "config_id": self.id,
                "order_uuid": order_uuid,
                "partner_id": partner.id,
                "member_ref_masked": masked,
                "otp_challenge_id": challenge.id,
                "bff_reservation_id": bff_id,
                "points": points,
                "value_idr": value_idr,
                "redeem_max_pct": self._efn_loyalty_redeem_max_pct(),
                "expires_at": expires_at,
            }
        )
        # K-4: token pos_redeem sekali pakai (dikonsumsi BFF saat reserve).
        challenge.write({"state": "consumed", "discount_token": False})
        return {
            "ok": True,
            "reservation_id": reservation.id,
            "points": points,
            "value_idr": value_idr,
            "expires_at": fields.Datetime.to_string(expires_at) if expires_at else False,
        }

    def efn_loyalty_redeem_release(self, order_uuid):
        """Lepas reservasi aktif order ini (idempoten; K-5). BFF tidak terjangkau -> antre outbox."""
        self._efn_check_otp_caller()
        if not isinstance(order_uuid, str) or not _ORDER_REF_RE.match(order_uuid):
            return self._efn_error("invalid_order", _("Order tidak valid."))
        reservation = self.env["efn.pos.loyalty.reservation"]._efn_find_active(self, order_uuid)
        if not reservation:
            return {"ok": True}
        try:
            response = self.env["efn.bff.client"]._efn_post(
                RELEASE_PATH,
                {"reservation_id": reservation.bff_reservation_id},
                log_context={"member": reservation.member_ref_masked, "order_ref": order_uuid},
            )
            released = response.status == 200
        except EfnBffError:
            released = False
        if released:
            reservation.write({"state": "released", "note": "released_by_cashier"})
        else:
            # Tetap dianggap lepas di kasir; outbox mengulang release ke BFF.
            Outbox = self.env["efn.pos.loyalty.outbox"]
            Outbox._efn_enqueue(
                {
                    "company_id": reservation.company_id.id,
                    "kind": "redeem_release",
                    "idempotency_key": reservation._efn_release_key(),
                    "reservation_id": reservation.id,
                }
            )
            reservation.write({"state": "released", "note": "release_queued"})
            Outbox._efn_trigger()
        return {"ok": True}

    def efn_loyalty_refund_preview(self, original_order_id, refund_lines):
        """Nilai pembayaran Poin EFN negatif untuk refund order berpoin (E-30, K-18-5, Q-F18-4 proporsional).

        :param int original_order_id: order asal (dimuat di layar Orders/refund POS).
        :param list refund_lines: ``[[orderline_id_asal, qty_refund], ...]`` (qty positif).
        :returns: ``{"ok": True, "ratio", "value_idr"}`` (``value_idr`` = poin yang dikembalikan, Rupiah bulat,
            dihitung server secara kumulatif atas refund sebelumnya) atau ``{"ok": False, ...}``.
        """
        self._efn_check_otp_caller()
        if isinstance(original_order_id, bool) or not isinstance(original_order_id, int):
            return self._efn_error("invalid_order", _("Order tidak valid."))
        original = self.env["pos.order"].browse(original_order_id).exists()
        if not original:
            return self._efn_error("invalid_order", _("Order tidak valid."))
        try:
            original.check_access("read")
        except AccessError:
            return self._efn_error("invalid_order", _("Order tidak valid."))
        original = original.sudo()
        if original.company_id != self.company_id:
            return self._efn_error("invalid_order", _("Order tidak valid."))
        if not isinstance(refund_lines, list):
            return self._efn_error("invalid_lines", _("Baris refund tidak valid."))
        excluded = self.env["product.category"]._efn_loyalty_excluded_ids()
        eligible = 0.0
        goods = 0.0
        for item in refund_lines:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                return self._efn_error("invalid_lines", _("Baris refund tidak valid."))
            line_id, qty = item
            if isinstance(line_id, bool) or not isinstance(line_id, int) or not isinstance(qty, (int, float)):
                return self._efn_error("invalid_lines", _("Baris refund tidak valid."))
            line = original.lines.filtered(lambda line, line_id=line_id: line.id == line_id)
            if not line or qty <= 0 or qty > abs(line.qty):
                return self._efn_error("invalid_lines", _("Baris refund tidak valid."))
            excl, incl = original._efn_line_raw_amounts(line, qty=qty)
            goods += incl
            if original._efn_line_is_eligible(line, excluded):
                eligible += excl
        ratio = original._efn_loyalty_refund_ratio(eligible, goods)
        return {"ok": True, "ratio": ratio, "value_idr": original._efn_loyalty_expected_points_restore(goods)}
