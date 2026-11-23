"""Gabung/pisah meja oleh kasir POS User (keputusan pemilik proyek 23 Nov 2026: gabung meja + split bill dipakai).

CE 19 ``pos_restaurant`` memberi hak tulis ``restaurant.table`` hanya ke POS Manager, sedangkan layar meja menulis
``parent_id`` dengan hak user sesi (``set_parent_id`` saat gabung, ``write({"parent_id": False})`` saat pisah dan
setelah bayar). Test ini memastikan kasir bisa gabung/pisah meja di lantai POS bersesi ``opened``, dan tidak bisa
mengubah field lain, meja POS lain, atau POS company lain (SECURITY_REVIEW_2026-11-23_pos-table-merge SEC-02..04).
"""

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "efn")
class TestTableMergeAccess(TransactionCase):
    @classmethod
    def _resto(cls, company, code):
        warehouse = (
            cls.env["stock.warehouse"]
            .with_company(company)
            .create({"name": f"Gudang uji meja {code}", "code": code, "company_id": company.id})
        )
        config = (
            cls.env["pos.config"]
            .with_company(company)
            .create(
                {
                    "name": f"Resto uji gabung meja {code}",
                    "company_id": company.id,
                    "picking_type_id": warehouse.pos_type_id.id,
                    "module_pos_restaurant": True,
                }
            )
        )
        table = cls.env["restaurant.table"].create({"floor_id": config.floor_ids.id, "table_number": 2})
        return config, config.floor_ids.table_ids - table, table

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("efn_setup_company.company_imd")
        cls.other_company = cls.env.ref("efn_setup_company.company_cup")
        cls.config, cls.t1, cls.t2 = cls._resto(cls.company, "TMRI")
        cls.other_config, cls.o1, cls.o2 = cls._resto(cls.other_company, "TMRC")
        cls.cashier = new_test_user(
            cls.env,
            login="efn_table_merge_cashier",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager = new_test_user(
            cls.env,
            login="efn_table_merge_manager",
            groups="base.group_user,point_of_sale.group_pos_manager",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

    def _open_session(self, config):
        session = self.env["pos.session"].create({"config_id": config.id, "user_id": self.cashier.id})
        session.set_opening_control(0, "")
        self.assertEqual(session.state, "opened")
        return session

    def _as(self, tables, user=None):
        return tables.with_user(user or self.cashier).with_company(self.company)

    def test_cashier_merges_and_unlinks_tables(self):
        self._open_session(self.config)
        self._as(self.t2).set_parent_id(self.t1.id, self.config.id)
        self.assertEqual(self.t2.parent_id, self.t1)
        self._as(self.t2).write({"parent_id": False})
        self.assertFalse(self.t2.parent_id)

    def test_cashier_cannot_touch_other_fields(self):
        self._open_session(self.config)
        with self.assertRaises(AccessError):
            self._as(self.t2).write({"seats": 8})
        with self.assertRaises(AccessError):
            self._as(self.t2).write({"parent_id": False, "seats": 2})

    def test_other_company_pos_rejected(self):
        self._open_session(self.config)
        self._open_session(self.other_config)
        with self.assertRaises(AccessError, msg="meja POS company lain"):
            self._as(self.o2).set_parent_id(self.o1.id, self.other_config.id)
        with self.assertRaises(AccessError, msg="meja POS company lain (pisah)"):
            self._as(self.o2).write({"parent_id": False})
        with self.assertRaises(AccessError, msg="induk di POS company lain"):
            self._as(self.t2).set_parent_id(self.o1.id, self.config.id)
        with self.assertRaises(AccessError, msg="config_id milik company lain"):
            self._as(self.t2).set_parent_id(self.t1.id, self.other_config.id)

    def test_session_not_opened_keeps_standard_acl(self):
        with self.assertRaises(AccessError, msg="tanpa sesi"):
            self._as(self.t2).set_parent_id(self.t1.id, self.config.id)
        session = self._open_session(self.config)
        session.sudo().write({"state": "closing_control"})
        with self.assertRaises(AccessError, msg="sesi closing_control"):
            self._as(self.t2).set_parent_id(self.t1.id, self.config.id)

    def test_manager_uses_standard_rights(self):
        self._as(self.t2, self.manager).set_parent_id(self.t1.id, self.config.id)
        self.assertEqual(self.t2.parent_id, self.t1)
