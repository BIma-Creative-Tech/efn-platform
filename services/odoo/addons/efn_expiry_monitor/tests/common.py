"""Fixture bersama test efn_expiry_monitor (PT uji, DC, 3 outlet, kategori berambang + anak yang mewarisi)."""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user


class EfnExpiryCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env["ir.config_parameter"].sudo().set_param("efn_expiry_monitor.enabled", "true")
        env["ir.config_parameter"].sudo().set_param("efn_assortment.enforce", "true")
        cls.company = env["res.company"].create({"name": "PT Uji Expiry"})
        Warehouse = env["stock.warehouse"]
        cls.dc = Warehouse.create(
            {"name": "DC expiry uji", "code": "EDC1", "company_id": cls.company.id, "efn_is_dc": True}
        )
        cls.outlet_a = Warehouse.create({"name": "Outlet A expiry", "code": "EOA1", "company_id": cls.company.id})
        cls.outlet_b = Warehouse.create({"name": "Outlet B expiry", "code": "EOB1", "company_id": cls.company.id})
        cls.outlet_c = Warehouse.create({"name": "Outlet C expiry", "code": "EOC1", "company_id": cls.company.id})
        Category = env["product.category"]
        cls.categ_root = Category.create(
            {
                "name": "Uji Fresh (induk)",
                "efn_near_expiry_days": 10,
                "efn_markdown_days": 3,
                "efn_min_receipt_days": 5,
            }
        )
        # Kategori daun kosong -> mewarisi induk (masukan efn-odoo-functional G45).
        cls.categ = Category.create({"name": "Uji Yoghurt", "parent_id": cls.categ_root.id})
        cls.categ_plain = Category.create({"name": "Uji Tanpa Ambang"})
        Product = env["product.product"]
        cls.product = Product.create(
            {
                "name": "Yoghurt expiry (uji)",
                "is_storable": True,
                "categ_id": cls.categ.id,
                "tracking": "lot",
                "use_expiration_date": True,
                "list_price": 20000.0,
            }
        )
        cls.product_plain = Product.create(
            {
                "name": "Produk tanpa ambang (uji)",
                "is_storable": True,
                "categ_id": cls.categ_plain.id,
                "tracking": "lot",
                "use_expiration_date": True,
                "list_price": 10000.0,
            }
        )
        cls.store_head = new_test_user(
            env, login="efn_exp_head_uji", groups="stock.group_stock_user", company_id=cls.company.id
        )
        cls.store_head.sudo().efn_outlet_warehouse_ids = [(6, 0, cls.outlet_a.ids)]
        cls.clerk = new_test_user(
            env, login="efn_exp_clerk_uji", groups="stock.group_stock_user", company_id=cls.company.id
        )
        # Markdown Manager tanpa efn_outlet_warehouse_ids (rule "semua outlet di company").
        cls.manager = new_test_user(
            env,
            login="efn_exp_mgr_uji",
            groups="efn_expiry_monitor.group_markdown_manager",
            company_id=cls.company.id,
        )

    @classmethod
    def make_lot(cls, name, days, product=None, **extra):
        expiry = fields.Datetime.now() + timedelta(days=days)
        vals = {
            "name": name,
            "product_id": (product or cls.product).id,
            "company_id": cls.company.id,
            "expiration_date": expiry,
            "removal_date": expiry,
        }
        vals.update(extra)
        return cls.env["stock.lot"].create(vals)

    @classmethod
    def put(cls, lot, warehouse, qty):
        cls.env["stock.quant"]._update_available_quantity(lot.product_id, warehouse.lot_stock_id, qty, lot_id=lot)
