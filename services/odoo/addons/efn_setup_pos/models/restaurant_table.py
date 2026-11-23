"""Gabung/pisah meja oleh kasir POS User (keputusan pemilik proyek 23 Nov 2026; fit-gap efn-odoo-functional).

Gap CE 19: ``pos_restaurant/security/ir.model.access.csv`` memberi ``restaurant.table`` baca saja ke
``point_of_sale.group_pos_user``, tetapi layar meja menulis ``parent_id`` dengan hak user sesi:
``set_parent_id`` (seret meja ke meja = gabung, ``floor_screen.js``), ``write({'parent_id': False})`` (pisah meja,
``floor_screen.js``; setelah bayar, ``order_payment_validation.js``). Akibatnya kasir mendapat AccessError.

Elevasi sempit: hanya field ``parent_id``, hanya oleh POS User, hanya untuk meja (dan meja induk) di lantai milik POS
company user yang sesinya ``opened`` (dan, untuk ``set_parent_id``, POS ``config_id`` itu sendiri). Perubahan lain
(posisi, kursi, denah) tetap butuh POS Manager. Review: docs/reviews/SECURITY_REVIEW_2026-11-23_pos-table-merge.md.
"""

from odoo import models


class RestaurantTable(models.Model):
    _inherit = "restaurant.table"

    def _efn_cashier_may_link(self, parent=None, config=None):
        """True bila kasir (POS User, bukan manager) boleh menulis ``parent_id`` meja ini dengan ``sudo``.

        Syarat: setiap meja (termasuk induk) ada di lantai POS restoran milik company user yang sesinya ``opened``
        (SEC-03); bila ``config`` diberikan (``set_parent_id``), POS itu wajib salah satunya (SEC-02: data balikan
        ``_load_pos_data_read`` dibaca di bawah ``sudo`` sesuai config).
        """
        env = self.env
        if env.su or env.user.has_group("point_of_sale.group_pos_manager"):
            return False  # hak standar sudah cukup / tidak perlu elevasi
        if not env.user.has_group("point_of_sale.group_pos_user"):
            return False
        tables = (self | parent).sudo() if parent else self.sudo()
        if not tables:
            return False
        configs = tables.floor_id.pos_config_ids.filtered(
            lambda c: c.company_id in env.user.company_ids and c.module_pos_restaurant
        )
        if config is not None:
            configs &= config.sudo()
        open_configs = (
            env["pos.session"].sudo().search([("config_id", "in", configs.ids), ("state", "=", "opened")]).config_id
        )
        return all(t.floor_id.pos_config_ids & open_configs for t in tables)

    def set_parent_id(self, parent_id, config_id):
        parent = self.browse(parent_id).exists()
        config = self.env["pos.config"].browse(config_id).exists()
        if self._efn_cashier_may_link(parent, config):
            return super(RestaurantTable, self.sudo()).set_parent_id(parent_id, config_id)
        return super().set_parent_id(parent_id, config_id)

    def write(self, vals):
        if set(vals) == {"parent_id"} and not vals["parent_id"] and self._efn_cashier_may_link():
            return super(RestaurantTable, self.sudo()).write(vals)
        return super().write(vals)
