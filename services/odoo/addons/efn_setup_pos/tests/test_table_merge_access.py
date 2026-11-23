"""Gabung/pisah meja oleh kasir POS User (keputusan pemilik proyek 23 Nov 2026: gabung meja + split bill dipakai).

CE 19 ``pos_restaurant`` memberi hak tulis ``restaurant.table`` hanya ke POS Manager, sedangkan layar meja menulis
``parent_id`` dengan hak user sesi (``set_parent_id`` saat gabung, ``write({"parent_id": False})`` saat pisah dan
setelah bayar). Test ini memastikan kasir bisa gabung/pisah meja di lantai POS bersesi terbuka, dan tidak bisa mengubah
field lain atau meja di luar POS-nya.
"""

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "efn")
class TestTableMergeAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.ref("efn_setup_company.company_imd")
        cls.company = company
        warehouse = (
            cls.env["stock.warehouse"]
            .with_company(company)
            .create({"name": "Gudang uji meja", "code": "TMRG", "company_id": company.id})
        )
        cls.config = (
            cls.env["pos.config"]
            .with_company(company)
            .create(
                {
                    "name": "Resto uji gabung meja",
                    "company_id": company.id,
                    "picking_type_id": warehouse.pos_type_id.id,
                    "module_pos_restaurant": True,
                }
            )
        )
        cls.floor = cls.config.floor_ids
        Table = cls.env["restaurant.table"]
        cls.t1 = cls.floor.table_ids
        cls.t2 = Table.create({"floor_id": cls.floor.id, "table_number": 2})
        other_floor = cls.env["restaurant.floor"].create({"name": "Lantai POS lain"})
        cls.foreign = Table.create({"floor_id": other_floor.id, "table_number": 9})
        cls.cashier = new_test_user(
            cls.env,
            login="efn_table_merge_cashier",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )

    def _open_session(self):
        self.env["pos.session"].create({"config_id": self.config.id, "user_id": self.cashier.id})

    def _as_cashier(self, tables):
        return tables.with_user(self.cashier).with_company(self.company)

    def test_cashier_merges_and_unlinks_tables(self):
        self._open_session()
        self._as_cashier(self.t2).set_parent_id(self.t1.id, self.config.id)
        self.assertEqual(self.t2.parent_id, self.t1)
        self._as_cashier(self.t2).write({"parent_id": False})
        self.assertFalse(self.t2.parent_id)

    def test_cashier_cannot_touch_other_fields_or_foreign_tables(self):
        self._open_session()
        with self.assertRaises(AccessError):
            self._as_cashier(self.t2).write({"seats": 8})
        with self.assertRaises(AccessError):
            self._as_cashier(self.t2).set_parent_id(self.foreign.id, self.config.id)
        with self.assertRaises(AccessError):
            self._as_cashier(self.foreign).write({"parent_id": False})

    def test_no_open_session_keeps_standard_acl(self):
        with self.assertRaises(AccessError):
            self._as_cashier(self.t2).set_parent_id(self.t1.id, self.config.id)
