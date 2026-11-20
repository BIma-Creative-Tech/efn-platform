"""Data & RPC POS efn_expiry_monitor (docs/04 §28.5a, fit-gap G45 §2-§3).

Data preload (``pos.config.efn_expiry_pos_data``, JSON teks, dimuat saat sesi dibuka -> ikut cache offline POS):
``{"v": 1, "enabled": bool, "override": bool, "lots": {"<product_id>": [[lot, exp_utc, removal_utc, qty], ...]},
"markdowns": [...]}``. ``lots`` = lot ber-stok (> 0) di lokasi sumber picking type POS (sama dengan CE
``get_existing_lots``) untuk produk ``tracking = lot``, dibatasi ``efn_expiry_monitor.pos_lot_limit`` baris (urut
kedaluwarsa terdekat). ``override`` = user yang membuka POS punya grup ``group_expiry_override`` (boleh override
lokal, termasuk offline). Tanpa data pelanggan.

RPC publik (``RPC_ALLOWLIST`` efn_core) — semua diawali ``_efn_expiry_check_caller`` (grup POS user +
``check_access(read)`` config + company user + sesi terbuka):
- ``efn_expiry_lot_candidates(product_id, lot_name=None)``: data lot & markdown terbaru satu produk saat online.
- ``efn_expiry_override_approvers()``: supervisor (grup override, PIN E-41 diatur, bukan pemanggil).
- ``efn_expiry_override_request(order_ref, product_id, lot_name, approver_id, pin, reason)``: PIN diverifikasi
  server (hash tidak pernah ke browser), kunci 15 menit setelah 5 PIN salah, persetujuan terikat order+lot+kasir.
"""

import json
import logging
import re
from contextlib import contextmanager
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.efn_core.scope import touch_row
from odoo.addons.efn_pos_refund_origin.models.return_pin import (
    PIN_MAX_INPUT,
    log_pin_failure,
    pin_locked,
    verify_pin,
)

from .common import (
    DEFAULT_OVERRIDE_TTL,
    DEFAULT_POS_LOT_LIMIT,
    GROUP_OVERRIDE,
    OVERRIDE_TTL_PARAM,
    POS_LOT_LIMIT_PARAM,
    is_enabled,
    param_int,
)

_logger = logging.getLogger(__name__)

_ORDER_REF_RE = re.compile(r"^[0-9A-Za-z\-]{8,64}$")
LOT_NAME_MAX = 64
REASON_MIN, REASON_MAX = 3, 200
MAX_APPROVERS = 100


POS_LOAD_KEY = "efn_expiry_pos_load"
MAX_APPROVERS_RETURNED = 20


def _dt(value):
    return fields.Datetime.to_string(value) if value else None


@contextmanager
def _pos_loading(cr):
    previous = cr.cache.get(POS_LOAD_KEY)
    cr.cache[POS_LOAD_KEY] = True
    try:
        yield
    finally:
        if previous:
            cr.cache[POS_LOAD_KEY] = previous
        else:
            cr.cache.pop(POS_LOAD_KEY, None)


class PosSession(models.Model):
    _inherit = "pos.session"

    def load_data(self, models_to_load):
        """Override CE: payload efn_expiry_pos_data hanya dihitung saat sesi memuat data (SEC-224)."""
        with _pos_loading(self.env.cr):
            return super().load_data(models_to_load)


class PosConfig(models.Model):
    _inherit = "pos.config"

    efn_expiry_pos_data = fields.Text(
        compute="_compute_efn_expiry_pos_data",
        groups="point_of_sale.group_pos_user",
        help="JSON lot (kedaluwarsa/removal/qty) + markdown aktif untuk POS (efn_expiry_monitor); dimuat saat sesi "
        "dibuka sehingga FEFO, blokir kedaluwarsa, dan markdown tetap berlaku offline.",
    )

    def _compute_efn_expiry_pos_data(self):
        # SEC-224: payload berat (read_group quant ber-sudo) hanya dihitung di dalam pos.session.load_data (penanda
        # per cursor, bukan context RPC); pembacaan pos.config lain hanya mendapat {"enabled", "deferred"}.
        enabled = is_enabled(self.env)
        loading = bool(self.env.cr.cache.get(POS_LOAD_KEY))
        for config in self:
            if enabled and loading:
                config.efn_expiry_pos_data = json.dumps(config._efn_expiry_pos_payload(), separators=(",", ":"))
            else:
                config.efn_expiry_pos_data = json.dumps({"v": 1, "enabled": enabled, "deferred": enabled})

    def _efn_expiry_pos_payload(self):
        self.ensure_one()
        return {
            "v": 1,
            "enabled": True,
            # SEC-217: dihitung dari user yang membuka POS. Akun perangkat bersama / pos_hr TIDAK boleh diberi
            # group_expiry_override (semua karyawan di perangkat itu mewarisi override lokal) — lihat README.
            "override": self.env.user.has_group(GROUP_OVERRIDE),
            "lots": self.sudo()._efn_expiry_lot_rows(),
            "markdowns": self.sudo()._efn_expiry_markdowns(),
        }

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        # Daftar kosong = semua field dimuat (pos.load.mixin); jangan dipersempit.
        if fields_list and "efn_expiry_pos_data" not in fields_list:
            fields_list = [*fields_list, "efn_expiry_pos_data"]
        return fields_list

    # ------------------------------------------------------------ data
    def _efn_expiry_location(self):
        self.ensure_one()
        return self.picking_type_id.default_location_src_id

    def _efn_expiry_warehouse(self):
        self.ensure_one()
        return self.picking_type_id.warehouse_id

    def _efn_expiry_lot_rows(self, product=None, lot_name=None):
        """{product_id(str): [[lot, exp, removal, qty]]} lot ber-stok di lokasi POS (urut kedaluwarsa terdekat)."""
        self.ensure_one()
        location = self._efn_expiry_location()
        if not location:
            return {}
        domain = [
            ("location_id", "child_of", location.id),
            ("lot_id", "!=", False),
            ("quantity", ">", 0),
            ("product_id.tracking", "=", "lot"),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", self.company_id.id),
        ]
        if product:
            domain.append(("product_id", "=", product.id))
        groups = self.env["stock.quant"]._read_group(domain, groupby=["lot_id"], aggregates=["quantity:sum"])
        limit = param_int(self.env, POS_LOT_LIMIT_PARAM, DEFAULT_POS_LOT_LIMIT, 1, 200000)
        far = fields.Datetime.to_datetime("9999-12-31 00:00:00")
        groups.sort(key=lambda row: (row[0].expiration_date or far, row[0].name or ""))
        rows = {}
        for lot, qty in groups[:limit]:
            rows.setdefault(str(lot.product_id.id), []).append(
                [lot.name, _dt(lot.expiration_date), _dt(lot.removal_date), qty]
            )
        if product and lot_name:
            known = {row[0] for row in rows.get(str(product.id), [])}
            if lot_name not in known:
                # Lot dari pindaian GS1 yang tidak ber-stok di lokasi ini: tetap kirim tanggalnya (qty 0) agar blokir
                # kedaluwarsa berlaku.
                lot = self.env["stock.lot"].search(
                    [
                        ("name", "=", lot_name),
                        ("product_id", "=", product.id),
                        "|",
                        ("company_id", "=", False),
                        ("company_id", "=", self.company_id.id),
                    ],
                    limit=1,
                )
                if lot:
                    rows.setdefault(str(product.id), []).append(
                        [lot.name, _dt(lot.expiration_date), _dt(lot.removal_date), 0.0]
                    )
        return rows

    def _efn_expiry_markdowns(self, product=None):
        self.ensure_one()
        warehouse = self._efn_expiry_warehouse()
        if not warehouse:
            return []
        # Markdown yang mulai berlaku dalam 7 hari ikut dimuat (sesi yang tetap terbuka/offline melewati tengah malam);
        # POS memeriksa periode dengan tanggal perangkat.
        today = fields.Date.context_today(self)
        domain = [
            ("warehouse_id", "=", warehouse.id),
            ("state", "=", "active"),
            ("date_to", ">=", today),
            ("date_from", "<=", today + timedelta(days=7)),
        ]
        if product:
            domain.append(("product_id", "=", product.id))
        return [md._efn_pos_dict() for md in self.env["efn.lot.markdown"].sudo().search(domain)]

    # ------------------------------------------------------------ RPC
    def _efn_expiry_check_caller(self):
        self.ensure_one()
        denied = AccessError(_("Anda tidak berhak memakai fitur ini."))
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise denied
        if not self.env.su:
            try:
                self.check_access("read")
            except AccessError:
                raise denied from None
            if self.company_id not in self.env.user.company_ids:
                raise denied
        session = self.current_session_id
        if not session or session.state != "opened":
            raise UserError(_("Sesi POS belum dibuka."))

    def _efn_expiry_product(self, product_id):
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id <= 0:
            return None
        product = self.env["product.product"].sudo().browse(product_id).exists()
        if not product or (product.company_id and product.company_id != self.company_id):
            return None
        return product

    def efn_expiry_lot_candidates(self, product_id, lot_name=None):
        """Lot (kedaluwarsa/removal/qty) & markdown terbaru untuk satu produk.

        :returns: ``{"ok": True, "enabled": bool, "lots": [[lot, exp, removal, qty]], "markdowns": [...]}`` atau
            ``{"ok": False, "error_code"}``.
        """
        self._efn_expiry_check_caller()
        if not is_enabled(self.env):
            return {"ok": True, "enabled": False}
        product = self._efn_expiry_product(product_id)
        if not product:
            return {"ok": False, "error_code": "invalid_product"}
        if lot_name is not None and (not isinstance(lot_name, str) or not 0 < len(lot_name) <= LOT_NAME_MAX):
            return {"ok": False, "error_code": "invalid_lot"}
        config = self.sudo()
        rows = config._efn_expiry_lot_rows(product=product, lot_name=lot_name)
        return {
            "ok": True,
            "enabled": True,
            "lots": rows.get(str(product.id), []),
            "markdowns": config._efn_expiry_markdowns(product=product),
        }

    def _efn_override_approver_valid(self, user):
        """SEC-216: supervisor sah untuk config ini — internal aktif, bukan pemanggil, company config, PIN E-41 diatur,
        grup override, dan cakupan outlet: gudang config ∈ ``efn_outlet_warehouse_ids`` supervisor, ATAU supervisor
        tanpa outlet terdaftar (supervisor PT-wide, mis. Supervisor Ops)."""
        self.ensure_one()
        user = user.sudo()
        warehouse = self._efn_expiry_warehouse()
        outlets = user.efn_outlet_warehouse_ids if user else self.env["stock.warehouse"]
        return bool(
            user
            and user.active
            and not user.share
            and user.id != self.env.uid
            and self.company_id in user.company_ids
            and user.efn_return_pin_hash
            and user.has_group(GROUP_OVERRIDE)
            and (not outlets or warehouse in outlets)
        )

    def efn_expiry_override_approvers(self):
        """Supervisor yang boleh menyetujui penjualan lot kedaluwarsa di POS ini.

        :returns: ``{"ok": True, "approvers": [{"id", "name"}]}`` atau ``{"ok": False, "error_code", "message"}``.
        """
        self._efn_expiry_check_caller()
        if not is_enabled(self.env):
            return {"ok": False, "error_code": "disabled", "message": _("Fitur kedaluwarsa POS tidak aktif.")}
        group = self.env.ref(GROUP_OVERRIDE)
        users = (
            self.env["res.users"]
            .sudo()
            .search(
                [
                    ("share", "=", False),
                    ("group_ids", "in", group.ids),
                    ("company_ids", "in", self.company_id.ids),
                    ("efn_return_pin_hash", "!=", False),
                    ("id", "!=", self.env.uid),
                ],
                order="name, id",
                limit=MAX_APPROVERS * 5,
            )
        )
        # SEC-216: hanya supervisor dengan cakupan outlet config (atau PT-wide), maks. 20, data minimal {id, name}.
        approvers = users.filtered(self._efn_override_approver_valid)[:MAX_APPROVERS_RETURNED]
        return {"ok": True, "approvers": [{"id": user.id, "name": user.name} for user in approvers]}

    @api.model
    def _efn_override_error(self, code):
        messages = {
            "disabled": _("Fitur kedaluwarsa POS tidak aktif."),
            "invalid_input": _("Data persetujuan tidak lengkap. Pilih supervisor, isi PIN dan alasan."),
            "approver_invalid": _("Supervisor tidak valid untuk POS ini (tidak boleh kasir sendiri)."),
            "invalid_pin": _("PIN salah."),
            "locked": _("Terlalu banyak PIN salah. Supervisor ini dikunci 15 menit."),
            "cashier_locked": _("Terlalu banyak PIN salah dari kasir ini. Coba lagi dalam 1 jam."),
        }
        return {"ok": False, "error_code": code, "message": messages.get(code, messages["invalid_input"])}

    def efn_expiry_override_request(self, order_ref, product_id, lot_name, approver_id, pin, reason):
        """Supervisor menyetujui penjualan lot kedaluwarsa ``lot_name`` di order ``order_ref`` dengan PIN.

        :returns: ``{"ok": True, "expires_in"}`` atau ``{"ok": False, "error_code", "message"}``.
        """
        self._efn_expiry_check_caller()
        if not is_enabled(self.env):
            return self._efn_override_error("disabled")
        product = self._efn_expiry_product(product_id)
        if (
            not product
            or not isinstance(order_ref, str)
            or not _ORDER_REF_RE.match(order_ref)
            or not isinstance(lot_name, str)
            or not 0 < len(lot_name) <= LOT_NAME_MAX
            or isinstance(approver_id, bool)
            or not isinstance(approver_id, int)
            or approver_id <= 0
            or not isinstance(pin, str)
            or not pin
            or len(pin) > PIN_MAX_INPUT
            or not isinstance(reason, str)
        ):
            return self._efn_override_error("invalid_input")
        reason = " ".join(reason.split())
        if not REASON_MIN <= len(reason) <= REASON_MAX:
            return self._efn_override_error("invalid_input")
        approver = self.env["res.users"].sudo().browse(approver_id).exists()
        if not approver or not self._efn_override_approver_valid(approver):
            # Bukan kegagalan PIN (format EFN_POS_PIN_FAIL hanya wrong_pin/locked/cashier_locked, alert devsecops).
            _logger.warning("EFN expiry override: config %s user %s approver tidak sah", self.id, self.env.uid)
            return self._efn_override_error("approver_invalid")
        Approval = self.env["efn.expiry.pos.approval"].sudo()
        touch_row(self.env.cr, "res_users", approver.id)
        base_vals = {
            "company_id": self.company_id.id,
            "config_id": self.id,
            "session_id": self.current_session_id.id,
            "order_ref": order_ref,
            "product_id": product.id,
            "lot_name": lot_name,
            "cashier_user_id": self.env.uid,
            "approver_user_id": approver.id,
            "reason": reason,
        }
        # SEC-216: penghitung BERSAMA dengan retur tanpa struk E-41 (approver 5/15 menit, kasir 10/60 menit).
        locked, lock_code = pin_locked(self.env, approver, self.env.user)
        if locked:
            Approval.create({**base_vals, "state": "failed", "failure_reason": "locked"})
            log_pin_failure(_logger, "expiry_override", self.id, self.env.uid, approver.id, lock_code)
            return self._efn_override_error(lock_code)
        if not verify_pin(pin, approver.efn_return_pin_hash):
            Approval.create({**base_vals, "state": "failed", "failure_reason": "wrong_pin"})
            log_pin_failure(_logger, "expiry_override", self.id, self.env.uid, approver.id, "wrong_pin")
            locked, lock_code = pin_locked(self.env, approver, self.env.user)
            return self._efn_override_error(lock_code if locked else "invalid_pin")
        ttl = param_int(self.env, OVERRIDE_TTL_PARAM, DEFAULT_OVERRIDE_TTL, 60, 86400)
        Approval.create(
            {**base_vals, "state": "approved", "expires_at": fields.Datetime.now() + timedelta(seconds=ttl)}
        )
        _logger.info("EFN expiry override: order %s lot disetujui approver %s", order_ref, approver.id)
        return {"ok": True, "expires_in": ttl}
