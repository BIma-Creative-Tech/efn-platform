"""E-31 + E-41: RPC refund POS (alokasi ke metode asal; persetujuan Store Manager untuk retur tanpa struk).

Metode publik terdaftar di `RPC_ALLOWLIST` (`efn_core/tests/test_rpc_surface.py`). Semua diawali
`_efn_refund_check_caller` = grup POS user + `check_access('read')` config + company user + sesi POS terbuka.

- `efn_refund_origin_allocation` (E-31): order asal dibaca dengan hak user (record rule) lalu dicek company-nya.
  Hanya mengembalikan id metode & nominal (tanpa data pelanggan).
- `efn_return_approval_approvers` (E-41): daftar `{id, name}` Store Manager yang sah untuk config ini (user internal
  aktif, grup `point_of_sale.group_pos_manager`, company config ∈ company user, PIN diatur, bukan pemanggil).
- `efn_return_approval_request` (E-41, docs/04 §12.4, L-51): verifikasi PIN di SERVER (hash pbkdf2 di
  `res.users`, tidak pernah dimuat ke browser), approver ≠ kasir, kunci baris approver (serialisasi percobaan
  paralel) + kunci 15 menit setelah 5 PIN salah; sukses -> `efn.pos.return.approval` sekali pakai (TTL 5 menit)
  terikat config + sesi + uuid order + kasir + nominal maks. Respons tanpa token/hash/data pelanggan.
"""

import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.efn_core.scope import touch_row

from .efn_pos_return_approval import APPROVAL_TTL_SECONDS, LOCK_MINUTES
from .return_pin import PIN_MAX_INPUT, verify_pin

_logger = logging.getLogger(__name__)

_ORDER_REF_RE = re.compile(r"^[0-9A-Za-z\-]{8,64}$")
MAX_APPROVAL_AMOUNT = 10**10
REASON_MIN, REASON_MAX = 3, 200
MAX_APPROVERS = 100


class PosConfig(models.Model):
    _inherit = "pos.config"

    efn_unlinked_return_mode = fields.Char(
        compute="_compute_efn_unlinked_return_mode",
        help="Cermin parameter efn.pos.refund.unlinked_return_mode (manager_approval|cash|deny) untuk frontend POS.",
    )

    def _compute_efn_unlinked_return_mode(self):
        mode = self.env["pos.order"]._efn_unlinked_return_mode()
        for config in self:
            config.efn_unlinked_return_mode = mode

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        # Daftar kosong = semua field dimuat (pos.load.mixin); jangan dipersempit.
        if fields_list and "efn_unlinked_return_mode" not in fields_list:
            fields_list = [*fields_list, "efn_unlinked_return_mode"]
        return fields_list

    def _efn_refund_check_caller(self):
        self.ensure_one()
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(_("Anda tidak berhak memakai fitur ini."))
        if not self.env.su:
            try:
                self.check_access("read")
            except AccessError:
                raise AccessError(_("Anda tidak berhak memakai fitur ini.")) from None
            if self.company_id not in self.env.user.company_ids:
                raise AccessError(_("Anda tidak berhak memakai fitur ini."))
        session = self.current_session_id
        if not session or session.state != "opened":
            raise UserError(_("Sesi POS belum dibuka."))

    def efn_refund_origin_allocation(self, original_order_id, refund_amount):
        """Metode & nominal default refund atas order asal (E-31).

        :param int original_order_id: order asal.
        :param float refund_amount: nilai refund (positif, total order refund termasuk pajak).
        :returns: ``{"ok": True, "allocation": [{"payment_method_id", "amount", "max_amount"}]}`` atau
            ``{"ok": False, "error_code"}``.
        """
        self._efn_refund_check_caller()
        if isinstance(original_order_id, bool) or not isinstance(original_order_id, int):
            return {"ok": False, "error_code": "invalid_order"}
        if isinstance(refund_amount, bool) or not isinstance(refund_amount, (int, float)) or refund_amount < 0:
            return {"ok": False, "error_code": "invalid_amount"}
        original = self.env["pos.order"].browse(original_order_id).exists()
        if not original:
            return {"ok": False, "error_code": "invalid_order"}
        try:
            original.check_access("read")
        except AccessError:
            return {"ok": False, "error_code": "invalid_order"}
        original = original.sudo()
        if original.company_id != self.company_id:
            return {"ok": False, "error_code": "invalid_order"}
        rows = original._efn_refund_allocation(float(refund_amount))
        return {
            "ok": True,
            "allocation": [
                {"payment_method_id": method.id, "amount": amount, "max_amount": remaining}
                for method, amount, remaining in rows
            ],
        }

    # ------------------------------------------------------------ E-41 approval
    @api.model
    def _efn_approval_error(self, code):
        messages = {
            "mode_disabled": _("Persetujuan retur tanpa struk tidak aktif di POS ini."),
            "invalid_input": _("Data persetujuan tidak lengkap. Pilih Store Manager, isi PIN dan alasan."),
            "approver_invalid": _("Store Manager tidak valid untuk POS ini (tidak boleh kasir sendiri)."),
            "invalid_pin": _("PIN salah."),
            "locked": _(
                "Terlalu banyak PIN salah. Persetujuan Store Manager ini dikunci %(minutes)s menit.",
                minutes=LOCK_MINUTES,
            ),
        }
        return {"ok": False, "error_code": code, "message": messages.get(code, messages["invalid_input"])}

    def _efn_return_approval_enabled(self):
        return self.env["pos.order"]._efn_unlinked_return_mode() == "manager_approval"

    def _efn_return_approver_valid(self, user):
        """Approver sah untuk config `self`: internal aktif, grup POS manager, company config, PIN, ≠ pemanggil."""
        self.ensure_one()
        user = user.sudo()
        return bool(
            user
            and user.active
            and not user.share
            and user.id != self.env.uid
            and self.company_id in user.company_ids
            and user.efn_return_pin_hash
            # Sementara sampai W-31 (jabatan hr.job Store Manager): grup Manajer POS.
            and user.has_group("point_of_sale.group_pos_manager")
        )

    def _efn_return_approver_candidates(self):
        self.ensure_one()
        users = (
            self.env["res.users"]
            .sudo()
            .search(
                [
                    ("share", "=", False),
                    ("company_ids", "in", self.company_id.ids),
                    ("efn_return_pin_hash", "!=", False),
                    ("id", "!=", self.env.uid),
                ],
                order="name, id",
                limit=MAX_APPROVERS * 5,
            )
        )
        return users.filtered(self._efn_return_approver_valid)[:MAX_APPROVERS]

    def efn_return_approval_approvers(self):
        """Store Manager yang dapat menyetujui retur tanpa struk di POS ini (E-41).

        :returns: ``{"ok": True, "approvers": [{"id", "name"}]}`` atau ``{"ok": False, "error_code", "message"}``.
        """
        self._efn_refund_check_caller()
        if not self._efn_return_approval_enabled():
            return self._efn_approval_error("mode_disabled")
        return {
            "ok": True,
            "approvers": [{"id": user.id, "name": user.name} for user in self._efn_return_approver_candidates()],
        }

    @api.model
    def _efn_approval_parse(self, order_ref, approver_id, pin, amount, reason):
        """Validasi input klien. Mengembalikan (order_ref, approver_id, pin, amount, reason) atau None."""
        if not isinstance(order_ref, str) or not _ORDER_REF_RE.match(order_ref):
            return None
        if isinstance(approver_id, bool) or not isinstance(approver_id, int) or approver_id <= 0:
            return None
        if not isinstance(pin, str) or not pin or len(pin) > PIN_MAX_INPUT:
            return None
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            return None
        if not (0 <= amount <= MAX_APPROVAL_AMOUNT) or amount != amount:  # amount != amount -> NaN
            return None
        if not isinstance(reason, str):
            return None
        reason = " ".join(reason.split())
        if not (REASON_MIN <= len(reason) <= REASON_MAX):
            return None
        return order_ref, approver_id, pin, float(amount), reason

    def efn_return_approval_request(self, order_ref, approver_id, pin, amount, reason):
        """Store Manager menyetujui retur tanpa struk order `order_ref` dengan PIN (E-41).

        :param str order_ref: ``pos.order.uuid`` order retur.
        :param int approver_id: ``res.users`` Store Manager (dari `efn_return_approval_approvers`).
        :param str pin: PIN Store Manager (tidak disimpan, tidak di-log).
        :param float amount: nominal maksimum uang keluar (tunai) yang disetujui.
        :param str reason: alasan (3–200 karakter, tanpa data pelanggan).
        :returns: ``{"ok": True, "expires_in", "max_amount"}`` atau ``{"ok": False, "error_code", "message"}``.
        """
        self._efn_refund_check_caller()
        if not self._efn_return_approval_enabled():
            return self._efn_approval_error("mode_disabled")
        parsed = self._efn_approval_parse(order_ref, approver_id, pin, amount, reason)
        if not parsed:
            return self._efn_approval_error("invalid_input")
        order_ref, approver_id, pin, amount, reason = parsed
        approver = self.env["res.users"].sudo().browse(approver_id).exists()
        if not approver or not self._efn_return_approver_valid(approver):
            _logger.warning(
                "EFN return approval: config %s user %s memilih approver tidak sah %s",
                self.id,
                self.env.uid,
                approver_id,
            )
            return self._efn_approval_error("approver_invalid")
        Approval = self.env["efn.pos.return.approval"].sudo()
        # Serialkan percobaan paralel untuk approver yang sama (hitungan PIN salah tidak bisa dilompati).
        touch_row(self.env.cr, "res_users", approver.id)
        base_vals = {
            "company_id": self.company_id.id,
            "config_id": self.id,
            "session_id": self.current_session_id.id,
            "order_ref": order_ref,
            "cashier_user_id": self.env.uid,
            "approver_user_id": approver.id,
            "amount_max": self.currency_id.round(amount),
            "reason": reason,
        }
        if Approval._efn_is_locked(approver):
            Approval.create({**base_vals, "state": "failed", "failure_reason": "locked"})
            _logger.warning("EFN return approval: approver %s terkunci (config %s)", approver.id, self.id)
            return self._efn_approval_error("locked")
        if not verify_pin(pin, approver.efn_return_pin_hash):
            Approval.create({**base_vals, "state": "failed", "failure_reason": "wrong_pin"})
            _logger.warning(
                "EFN return approval: PIN salah approver %s oleh kasir %s (config %s)",
                approver.id,
                self.env.uid,
                self.id,
            )
            return self._efn_approval_error("locked" if Approval._efn_is_locked(approver) else "invalid_pin")
        Approval.search([("config_id", "=", self.id), ("order_ref", "=", order_ref), ("state", "=", "approved")]).write(
            {"state": "superseded"}
        )
        Approval.create(
            {
                **base_vals,
                "state": "approved",
                "expires_at": fields.Datetime.now() + timedelta(seconds=APPROVAL_TTL_SECONDS),
            }
        )
        _logger.info(
            "EFN return approval: order %s disetujui approver %s untuk kasir %s", order_ref, approver.id, self.env.uid
        )
        return {"ok": True, "expires_in": APPROVAL_TTL_SECONDS, "max_amount": base_vals["amount_max"]}
