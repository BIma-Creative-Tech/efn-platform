"""Tautan SO <-> pesanan online + guard pembatalan, perubahan state, dan pengeluaran barang SO online.

Tidak ada metode publik baru (gerbang SEC-58): hanya override privat/ORM (``_action_cancel``, ``write``,
``stock.move._action_done``, ``pos.order.line.create``).
- SEC-95 (r10): batal bagian berbayar hanya Sales manager + alasan. UI CE 19: tombol *Cancel* SO hanya dialog
  konfirmasi JS generik (tanpa cek faktur/grup); faktur ter-posting tidak dibatalkan CE (hanya draf).
- SEC-104 (re-review G17, L-19): jalur internal E-28 (konfirmasi/batal dari saga) ditandai lewat ``cr.cache``
  (``internal_scope``), BUKAN context -> ``efn_online_internal`` dari context RPC tidak berpengaruh.
  ``write({'state': ...})`` langsung pada SO online di luar jalur itu ditolak.
- SEC-105: guard serah terima di level ``stock.move._action_done``: move ber-``sale_line_id`` SO online menuju
  lokasi pelanggan/transit hanya boleh selesai di dalam ``_efn_handover`` (setelah kode valid). Menangkap ubah
  tipe operasi, pindah move ke picking manual, backorder, batch. ``pos_sale`` (bila terpasang): SO online tidak
  dimuat di POS dan baris POS yang menautkan SO online ditolak (settle/uang muka).
BFF tidak pernah memakai ``get_portal_url``/``access_token`` SO (route portal ``/my/orders``, temuan #38).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .efn_online_common import (
    CANCEL_SCOPE,
    CONFIRM_SCOPE,
    HANDOVER_SCOPE,
    in_any_scope,
    in_scope,
    internal_scope,
)
from .efn_online_order import _efn_is_customer_move


class SaleOrder(models.Model):
    _inherit = "sale.order"

    efn_online_order_id = fields.Many2one(
        "efn.online.order", string="Pesanan online", readonly=True, index="btree_not_null", copy=False
    )

    efn_online_cancel_note = fields.Char(
        string="Alasan batal (online)",
        copy=False,
        groups="sales_team.group_sale_manager",
        help="Wajib (min. 10 karakter) untuk membatalkan pesanan online yang sudah dibayar (SEC-95). "
        "Hanya tercatat di Odoo; tidak dikirim ke BFF.",
    )

    def _efn_online_orders(self):
        return self.filtered(lambda so: so.sudo().efn_online_order_id)

    def _action_cancel(self):
        online_by_so = {}
        external = self._efn_online_orders().filtered(lambda so: not in_scope(self.env, CANCEL_SCOPE, so.ids))
        for order in external:
            # SEC-95: cek grup memakai user ASLI (bukan sudo) sebelum hook berjalan dengan sudo.
            note = order.sudo().efn_online_cancel_note
            online = order.sudo().efn_online_order_id
            online.with_user(self.env.user)._efn_ops_cancel_check(note)
            online_by_so[order.id] = (online, note)
        with internal_scope(self.env, CANCEL_SCOPE, external.ids):
            result = super()._action_cancel()
        for online, note in online_by_so.values():
            online._efn_ops_cancelled(reason_note=note, actor=self.env.user)
        return result

    def write(self, vals):
        if "state" in vals:
            for order in self._efn_online_orders():
                allowed = (vals["state"] == "cancel" and in_scope(self.env, CANCEL_SCOPE, order.ids)) or (
                    vals["state"] == "sale" and in_scope(self.env, CONFIRM_SCOPE, order.ids)
                )
                if not allowed:
                    raise UserError(
                        _("Status pesanan online %(name)s hanya berubah lewat alur kanal online.", name=order.name)
                    )
        return super().write(vals)


# SEC-110 (re-review akhir r10): field yang menentukan guard serah terima tidak boleh diubah di luar jalur internal
# E-28 (penanda cr.cache, bukan context). Field computed-stored di move (lokasi tujuan, tipe operasi) ikut berubah
# bila picking diubah -> picking juga dijaga.
MOVE_GUARDED_FIELDS = ("sale_line_id", "location_dest_id", "location_final_id", "picking_type_id", "picking_id")
MOVE_LINE_GUARDED_FIELDS = ("location_dest_id", "picking_id", "move_id")
PICKING_GUARDED_FIELDS = ("location_dest_id", "picking_type_id")


def _efn_changed(records, vals, fnames):
    """Record yang nilainya benar-benar berubah untuk salah satu field (tulis nilai sama = no-op, diizinkan)."""
    changed = records.browse()
    fields_in_vals = [f for f in fnames if f in vals]
    if not fields_in_vals:
        return changed
    for record in records.sudo():
        for fname in fields_in_vals:
            new = vals[fname]
            new_id = new.id if hasattr(new, "id") else (new or False)
            if (record[fname].id or False) != new_id:
                changed |= record
                break
    return changed


def _efn_guard_online_orders(env, orders, what):
    orders = orders.sudo().filtered("efn_online_order_id")
    if orders and not in_any_scope(env, orders.ids):
        raise UserError(
            env._(
                "%(what)s pesanan online %(name)s tidak dapat diubah (hanya lewat alur kanal online / serah terima).",
                what=what,
                name=", ".join(orders.mapped("name")),
            )
        )


class StockMove(models.Model):
    _inherit = "stock.move"

    def write(self, vals):
        changed = _efn_changed(self, vals, MOVE_GUARDED_FIELDS)
        if changed:
            orders = changed.sudo().sale_line_id.order_id
            if vals.get("sale_line_id"):
                # Memasang move asing ke baris SO online juga dijaga.
                orders |= self.env["sale.order.line"].sudo().browse(vals["sale_line_id"]).order_id
            _efn_guard_online_orders(self.env, orders, _("Pergerakan stok"))
        return super().write(vals)

    def _action_done(self, cancel_backorder=False):
        """SEC-105: barang SO online ke pelanggan hanya keluar lewat wizard serah terima (kode ambil)."""
        for move in self:
            order = move.sudo().sale_line_id.order_id
            if (
                order.efn_online_order_id
                and _efn_is_customer_move(move.sudo())
                and not in_scope(self.env, HANDOVER_SCOPE, order.ids)
            ):
                raise UserError(
                    _(
                        "Barang pesanan online %(name)s hanya dapat dikeluarkan lewat 'Serahkan (kode ambil)'.",
                        name=order.name,
                    )
                )
        return super()._action_done(cancel_backorder=cancel_backorder)


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    @api.model_create_multi
    def create(self, vals_list):
        """SEC-105: ``pos_sale`` (settle/uang muka SO di POS) tidak boleh menautkan SO online."""
        so_ids = {vals.get("sale_order_origin_id") for vals in vals_list if vals.get("sale_order_origin_id")}
        if so_ids and self.env["sale.order"].sudo().browse(list(so_ids)).filtered("efn_online_order_id"):
            raise UserError(_("Pesanan online tidak dapat diproses di POS; serahkan lewat 'Serahkan (kode ambil)'."))
        return super().create(vals_list)


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    def write(self, vals):
        changed = _efn_changed(self, vals, MOVE_LINE_GUARDED_FIELDS)
        if changed:
            orders = changed.sudo().move_id.sale_line_id.order_id
            if vals.get("move_id"):
                orders |= self.env["stock.move"].sudo().browse(vals["move_id"]).sale_line_id.order_id
            _efn_guard_online_orders(self.env, orders, _("Rincian operasi"))
        return super().write(vals)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def write(self, vals):
        changed = _efn_changed(self, vals, PICKING_GUARDED_FIELDS)
        if changed:
            sudo = changed.sudo()
            _efn_guard_online_orders(self.env, sudo.sale_id | sudo.move_ids.sale_line_id.order_id, _("Pengiriman"))
        return super().write(vals)
