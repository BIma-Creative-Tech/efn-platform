"""efn_intercompany (G28): PO antar-PT -> SO, faktur -> tagihan draft, harga transfer, pembatalan berantai."""

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnIntercompany(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.buyer = cls.company_data["company"]
        cls.seller = cls.setup_other_company(name="PT penjual uji")["company"]
        cls.env.user.company_ids |= cls.seller
        for company in (cls.buyer, cls.seller):
            user = new_test_user(
                cls.env,
                login=f"efn_ic_{company.id}",
                groups="base.group_user,efn_intercompany.group_ic_technical",
                company_id=company.id,
                company_ids=[Command.set(company.ids)],
            )
            company.write(
                {
                    "efn_ic_user_id": user.id,
                    "efn_ic_so_from_po": True,
                    "efn_ic_auto_confirm_so": True,
                    "efn_ic_bill_from_invoice": True,
                }
            )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Paket Ayam (uji IC)",
                "type": "consu",
                "is_storable": True,
                "list_price": 50000,
                "invoice_policy": "order",
                "purchase_method": "purchase",
                "company_id": False,
                "taxes_id": [Command.clear()],
                "supplier_taxes_id": [Command.clear()],
            }
        )
        cls.product.with_company(cls.seller).standard_price = 20000

    def _po(self, qty=10, price=30000):
        return (
            self.env["purchase.order"]
            .with_company(self.buyer)
            .create(
                {
                    "partner_id": self.seller.partner_id.id,
                    "company_id": self.buyer.id,
                    "order_line": [
                        Command.create(
                            {"product_id": self.product.id, "product_qty": qty, "price_unit": price, "tax_ids": []}
                        )
                    ],
                }
            )
        )

    def test_po_creates_confirmed_so_in_seller(self):
        po = self._po()
        self.assertEqual(po.efn_ic_company_id, self.seller)
        po.button_confirm()
        so = po.efn_ic_sale_id
        self.assertTrue(so)
        self.assertEqual(so.company_id, self.seller)
        self.assertEqual(so.partner_id, self.buyer.partner_id)
        self.assertEqual(so.state, "sale")
        self.assertEqual(so.client_order_ref, po.name)
        self.assertEqual(so.create_uid, self.seller.efn_ic_user_id)
        self.assertEqual(so.order_line.price_unit, 30000)
        self.assertEqual(so.order_line.product_uom_qty, 10)
        # Idempoten: approve ulang tidak membuat SO kedua.
        po.button_approve()
        self.assertEqual(self.env["sale.order"].search_count([("efn_ic_purchase_id", "=", po.id)]), 1)

    def test_transfer_price_cost_plus(self):
        self.env["efn.ic.transfer.price"].create(
            {"company_id": self.seller.id, "buyer_company_id": self.buyer.id, "markup_pct": 15, "note": "memo uji"}
        )
        po = self._po(price=99999)
        po.button_confirm()
        self.assertEqual(po.order_line.price_unit, 23000)
        self.assertEqual(po.efn_ic_sale_id.order_line.price_unit, 23000)

    def test_transfer_price_category_rule_wins(self):
        # CE 19: categ_id produk boleh kosong -> beri kategori eksplisit.
        self.product.categ_id = self.env["product.category"].create({"name": "Paket CK (uji IC)"})
        TP = self.env["efn.ic.transfer.price"]
        TP.create({"company_id": self.seller.id, "buyer_company_id": self.buyer.id, "markup_pct": 10})
        TP.create(
            {
                "company_id": self.seller.id,
                "buyer_company_id": self.buyer.id,
                "markup_pct": 50,
                "categ_id": self.product.categ_id.id,
            }
        )
        po = self._po()
        po.button_confirm()
        self.assertEqual(po.order_line.price_unit, 30000)

    def test_transfer_price_validation(self):
        with self.assertRaises(ValidationError):
            self.env["efn.ic.transfer.price"].create(
                {"company_id": self.seller.id, "buyer_company_id": self.seller.id, "markup_pct": 5}
            )

    def test_invoice_creates_draft_bill_linked_to_po(self):
        po = self._po(qty=4, price=25000)
        po.button_confirm()
        so = po.efn_ic_sale_id.with_company(self.seller)
        invoice = so._create_invoices()
        invoice.action_post()
        bill = invoice.efn_ic_bill_ids
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill.state, "draft")
        self.assertEqual(bill.move_type, "in_invoice")
        self.assertEqual(bill.company_id, self.buyer)
        self.assertEqual(bill.partner_id, self.seller.partner_id)
        self.assertEqual(bill.invoice_line_ids.purchase_line_id, po.order_line)
        self.assertEqual(bill.amount_total, invoice.amount_total)
        self.assertEqual(bill.ref, invoice.name)
        # Faktur dibatalkan -> tagihan draft ikut batal.
        invoice.button_draft()
        self.assertEqual(bill.state, "cancel")
        self.assertFalse(bill.efn_ic_source_move_id)
        invoice.action_post()
        self.assertEqual(len(invoice.efn_ic_bill_ids), 1)
        self.assertNotEqual(invoice.efn_ic_bill_ids, bill)

    def test_bill_uses_untaxed_price_when_seller_tax_included(self):
        """Pajak penjualan penjual termasuk harga, pajak pembelian pembeli tidak -> DPP tagihan = DPP faktur."""
        Tax = self.env["account.tax"]
        sale_tax = Tax.create(
            {
                "name": "PPN 11% incl (uji)",
                "amount": 11,
                "price_include_override": "tax_included",
                "company_id": self.seller.id,
            }
        )
        purchase_tax = Tax.create(
            {"name": "PPN masukan 11% (uji)", "amount": 11, "type_tax_use": "purchase", "company_id": self.buyer.id}
        )
        po = self._po(qty=10, price=11100)
        po.order_line.tax_ids = purchase_tax
        po.button_confirm()
        so = po.efn_ic_sale_id.with_company(self.seller)
        so.order_line.tax_ids = sale_tax
        invoice = so._create_invoices()
        invoice.action_post()
        bill = invoice.efn_ic_bill_ids
        self.assertEqual(invoice.amount_untaxed, 100000)
        self.assertEqual(bill.amount_untaxed, invoice.amount_untaxed)
        self.assertEqual(bill.amount_total, invoice.amount_total)

    def test_no_bill_when_buyer_disabled(self):
        self.buyer.efn_ic_bill_from_invoice = False
        po = self._po()
        po.button_confirm()
        invoice = po.efn_ic_sale_id.with_company(self.seller)._create_invoices()
        invoice.action_post()
        self.assertFalse(invoice.efn_ic_bill_ids)

    def test_regular_vendor_po_untouched(self):
        vendor = self.env["res.partner"].create({"name": "Pemasok luar"})
        po = self.env["purchase.order"].create(
            {
                "partner_id": vendor.id,
                "order_line": [Command.create({"product_id": self.product.id, "product_qty": 1, "price_unit": 1})],
            }
        )
        po.button_confirm()
        self.assertFalse(po.efn_ic_company_id)
        self.assertFalse(po.efn_ic_sale_id)

    def test_cancel_po_cancels_draft_so(self):
        self.seller.efn_ic_auto_confirm_so = False
        po = self._po()
        po.button_confirm()
        so = po.efn_ic_sale_id
        self.assertEqual(so.state, "draft")
        po.button_cancel()
        self.assertEqual(so.state, "cancel")

    def test_product_of_other_company_rejected(self):
        self.product.company_id = self.buyer
        po = self._po()
        with self.assertRaisesRegex(UserError, "product_not_shared"):
            po.button_confirm()

    def test_missing_technical_user(self):
        self.seller.efn_ic_user_id = False
        po = self._po()
        with self.assertRaisesRegex(UserError, "no_ic_user"):
            po.button_confirm()

    def test_technical_user_must_have_group(self):
        plain = new_test_user(
            self.env,
            login="efn_ic_plain",
            groups="base.group_user",
            company_id=self.seller.id,
            company_ids=[Command.set(self.seller.ids)],
        )
        with self.assertRaisesRegex(ValidationError, "ic_user_group"):
            self.seller.efn_ic_user_id = plain


@tagged("post_install", "-at_install", "efn")
class TestEfnSaleCancelWithoutPurchaseRights(AccountTestInvoicingCommon):
    """Regresi G28: manajer penjualan tanpa grup Pembelian membatalkan SO yang move-nya tertaut baris PO (MTO)."""

    def test_sales_manager_cancels_so_linked_to_po(self):
        company = self.company_data["company"]
        env = self.env(su=True)
        product = env["product.product"].create({"name": "Barang uji batal", "type": "consu", "is_storable": True})
        vendor = env["res.partner"].create({"name": "Vendor uji batal"})
        po = env["purchase.order"].create(
            {
                "partner_id": vendor.id,
                "company_id": company.id,
                "order_line": [Command.create({"product_id": product.id, "product_qty": 2, "price_unit": 1000})],
            }
        )
        po.button_confirm()
        so = env["sale.order"].create(
            {
                "partner_id": self.partner_a.id,
                "company_id": company.id,
                "order_line": [Command.create({"product_id": product.id, "product_uom_qty": 2, "price_unit": 2000})],
            }
        )
        so.action_confirm()
        so.picking_ids.move_ids.created_purchase_line_ids = po.order_line
        manager = new_test_user(
            self.env,
            login="efn_ic_sales_mgr",
            groups="base.group_user,sales_team.group_sale_manager",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )
        self.assertFalse(manager.has_group("purchase.group_purchase_user"))
        so.with_user(manager).action_cancel()
        self.assertEqual(so.state, "cancel")
        self.assertTrue(po.activity_ids, "aktivitas peringatan CE di PO tetap dibuat")
