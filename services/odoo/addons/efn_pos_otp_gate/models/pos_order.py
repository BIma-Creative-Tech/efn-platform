"""Defense in depth: server memvalidasi reward ber-OTP saat order POS di-sync.

ADR-017 (OTP saat POS offline, Proposed) — opsi (a) + aturan #2.

Hook (diverifikasi terhadap source Odoo 19.0 `point_of_sale/models/pos_order.py`):
`sync_from_ui` -> `_process_order(order, existing_order)` -> `_process_saved_order(draft)`.
Pada `_process_saved_order` baris order sudah tertulis, sehingga kita bisa memeriksa
`pos.order.line.reward_id` (field dari pos_loyalty).

Perilaku:
- Order DRAFT (mis. sync meja restoran): reward ber-OTP tanpa OTP terverifikasi DIHAPUS
  dari order. Token tidak di-consume di tahap draft.
- Order BERBAYAR tanpa OTP terverifikasi sama sekali untuk order ini (frontend sudah
  memblokir jalur ini, jadi hanya terjadi pada klien yang dimodifikasi/bug): DITOLAK
  (UserError -> seluruh transaksi sync di-rollback).
- Order BERBAYAR dengan OTP terverifikasi: token di-consume ke BFF
  (`/v1/pos/discount-token/consume`) SETELAH pemrosesan standar.
  * sukses -> `efn_otp_gate_state = consumed`.
  * gagal (expired/already_used/order_mismatch/invalid) atau BFF tidak terjangkau ->
    order TETAP disimpan dan ditandai `review` untuk supervisor (ADR-017 #2: "tidak
    dibatalkan otomatis"). Pembayaran sudah dihitung dari total setelah diskon, sehingga
    reward tidak bisa dibuang diam-diam; dan menolak sync akan membuat order terjebak di
    antrean sync POS.

SEC-07 (v2 kontrak §3.1): consume mengirim `member_ref` dari nomor HP `partner_id` FINAL
order (dibaca server). Partner diganti setelah OTP -> BFF `409 member_mismatch` -> review.
Order final tanpa partner ber-HP Indonesia -> review `member_missing` tanpa consume.

SEC-07 sisa (docs/04 §8.8, Gelombang 5): consume juga mengirim `amount_total` order final;
BFF `409 amount_mismatch` (total di luar toleransi dari nominal saat OTP) -> review.

SEC-12: baris yang butuh OTP ditentukan server, TIDAK dari flag klien `is_reward_line`.
Sebuah baris dianggap "reward ber-OTP" bila (a) `reward_id.program_id.efn_requires_otp`,
apa pun nilai `is_reward_line`; ATAU (b) `product_id` adalah `discount_line_product_id`
dari reward program ber-OTP (termasuk program diarsipkan) walau klien mengosongkan
`reward_id`. Baris refund (qty < 0 dengan `refunded_orderline_id`) dikecualikan.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.efn_core.models.efn_bff_client import (
    EfnBffError,
    mask_phone,
    partner_member_ref,
)

_logger = logging.getLogger(__name__)

TOKEN_CONSUME_PATH = "/v1/pos/discount-token/consume"
CONSUME_REJECT_REASONS = (
    "already_used",
    "order_mismatch",
    "member_mismatch",
    "amount_mismatch",
    "expired",
    "invalid",
)


class PosOrder(models.Model):
    _inherit = "pos.order"

    efn_otp_verified = fields.Boolean(
        string="OTP Promo Terverifikasi",
        copy=False,
        help="Diset frontend POS sebagai penanda UI; server MENIMPA nilainya berdasarkan "
        "efn.pos.otp.challenge (tidak dipercaya dari browser).",
    )
    efn_otp_member_masked = fields.Char(string="Member OTP (mask)", copy=False)
    efn_otp_gate_state = fields.Selection(
        [
            ("consumed", "Token promo dipakai"),
            ("review", "Perlu review supervisor"),
        ],
        string="Status OTP Promo",
        copy=False,
        index="btree_not_null",
        readonly=True,
    )
    efn_otp_gate_note = fields.Char(string="Catatan OTP Promo", copy=False, readonly=True)

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        # Daftar kosong = semua field dimuat (pos.load.mixin); jangan dipersempit.
        if fields_list:
            extra = ["efn_otp_verified", "efn_otp_member_masked"]
            fields_list = [*fields_list, *[f for f in extra if f not in fields_list]]
        return fields_list

    # ----------------------------------------------------------------- helpers
    def _efn_otp_gate_enabled(self):
        return self.env["efn.bff.client"]._efn_get_bool_param("efn.pos.otp_required_for_promo", True)

    @api.model
    def _efn_otp_reward_products(self):
        """Produk diskon milik reward dari program ber-OTP (termasuk yang diarsipkan)."""
        rewards = (
            self.env["loyalty.reward"]
            .sudo()
            .with_context(active_test=False)
            .search([("program_id.efn_requires_otp", "=", True)])
        )
        return rewards.discount_line_product_id

    def _efn_get_otp_gated_lines(self):
        """Baris yang butuh OTP, ditentukan server (SEC-12) — flag klien tidak dipercaya."""
        self.ensure_one()
        if not self._efn_otp_gate_enabled():
            return self.env["pos.order.line"]
        gated_products = self._efn_otp_reward_products()

        def is_gated(line):
            if line.refunded_orderline_id and line.qty < 0:
                return False
            if line.reward_id.program_id.efn_requires_otp:
                return True
            return bool(line.product_id and line.product_id in gated_products)

        lines = self.lines.sudo()
        gated = lines.filtered(is_gated)
        spoofed = gated.filtered(lambda line: not line.is_reward_line or not line.reward_id)
        if spoofed:
            _logger.warning(
                "EFN OTP gate: order %s membawa %d baris diskon program ber-OTP dengan flag "
                "reward klien kosong (SEC-12); diperlakukan sebagai reward ber-OTP",
                self.uuid,
                len(spoofed),
            )
        return gated

    def _efn_strip_unverified_rewards(self):
        """Order draft: hapus reward ber-OTP bila tidak ada OTP terverifikasi untuk order ini."""
        self.ensure_one()
        gated_lines = self._efn_get_otp_gated_lines()
        if not gated_lines:
            return
        challenge = self.env["efn.pos.otp.challenge"]._efn_find_valid_verified(self.config_id, self.uuid)
        if challenge:
            return
        _logger.warning(
            "EFN OTP gate: %d baris reward tanpa OTP dihapus dari order draft %s",
            len(gated_lines),
            self.uuid,
        )
        gated_lines.unlink()
        self.write({"efn_otp_verified": False, "efn_otp_member_masked": False})

    def _efn_mark_review(self, challenge, reason, member_masked=None):
        _logger.warning("EFN OTP gate: order %s ditandai review (%s)", self.uuid, reason)
        challenge.write({"state": "failed", "failure_reason": reason, "discount_token": False})
        self.write(
            {
                "efn_otp_gate_state": "review",
                "efn_otp_gate_note": reason,
                "efn_otp_verified": False,
                "efn_otp_member_masked": member_masked or challenge.member_ref_masked,
            }
        )

    def _efn_enforce_otp_gate(self):
        """Order berbayar: consume discount_token ke BFF, tolak, atau tandai review."""
        self.ensure_one()
        gated_lines = self._efn_get_otp_gated_lines()
        if not gated_lines:
            if self.efn_otp_verified:
                # Penanda dari browser tanpa reward ber-OTP: tidak bermakna, netralkan.
                self.write({"efn_otp_verified": False})
            return

        challenge = self.env["efn.pos.otp.challenge"]._efn_find_latest(
            self.config_id, self.uuid, ["verified"]
        )
        if not challenge:
            _logger.warning("EFN OTP gate: order %s membawa reward ber-OTP tanpa OTP terverifikasi", self.uuid)
            raise UserError(
                _("Promo pada order ini memerlukan verifikasi OTP pelanggan. "
                  "Hapus promo atau lakukan verifikasi OTP.")
            )
        if not challenge.discount_token:
            self._efn_mark_review(challenge, "token_missing")
            return

        # SEC-07: member_ref dari partner FINAL order (data server), bukan dari challenge.
        member, member_error = partner_member_ref(self.partner_id.sudo())
        if not member:
            self._efn_mark_review(challenge, f"member_missing:{member_error}")
            return
        member_masked = mask_phone(member)
        if challenge.partner_id and challenge.partner_id != self.partner_id:
            _logger.warning(
                "EFN OTP gate: pelanggan order %s berbeda dari saat OTP diminta; BFF yang memutuskan",
                self.uuid,
            )

        payload = {
            "discount_token": challenge.discount_token,
            "order_ref": self.uuid,
            "member_ref": member,
            # docs/04 §8.8 (SEC-07 sisa): total FINAL order dari server (bukan browser),
            # Rupiah tanpa desimal seperti `amount` saat OTP diminta. BFF membandingkan
            # dengan nominal di token (toleransi POS_OTP_AMOUNT_TOLERANCE_PCT) ->
            # `409 amount_mismatch` -> order ditandai review.
            "amount_total": int(round(self.amount_total or 0.0)),
        }
        try:
            response = self.env["efn.bff.client"].post(
                TOKEN_CONSUME_PATH,
                payload,
                log_context={"member": member_masked, "order_ref": self.uuid},
            )
        except EfnBffError as exc:
            self._efn_mark_review(challenge, exc.code, member_masked)
            return

        if response.status == 200 and response.data.get("ok") is True:
            challenge.write(
                {
                    "state": "consumed",
                    "consumed_order_id": self.id,
                    "discount_token": False,
                }
            )
            self.write(
                {
                    "efn_otp_gate_state": "consumed",
                    "efn_otp_gate_note": False,
                    "efn_otp_verified": True,
                    "efn_otp_member_masked": member_masked,
                }
            )
            return

        reason = response.data.get("reason")
        error = response.data.get("error")
        if reason not in CONSUME_REJECT_REASONS and isinstance(error, dict):
            reason = error.get("code")
        if reason not in CONSUME_REJECT_REASONS:
            reason = f"consume_rejected_{response.status}"
        self._efn_mark_review(challenge, reason, member_masked)

    # -------------------------------------------------------------------- hook
    def _process_saved_order(self, draft):
        self.ensure_one()
        if draft:
            self._efn_strip_unverified_rewards()
            return super()._process_saved_order(draft)

        order_id = super()._process_saved_order(draft)
        # Consume dilakukan TERAKHIR: bila langkah standar (picking, invoice) gagal, token
        # belum terpakai.
        if self.state != "cancel":
            self._efn_enforce_otp_gate()
        return order_id
