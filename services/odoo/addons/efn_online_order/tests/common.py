"""Fixture E-28: company akuntansi uji (AccountTestInvoicingCommon), kanal TST + user teknis, kanal TSB di company
lain. Semua angka (harga, tarif pajak, pembulatan) = data uji, bukan konfigurasi produksi (ADR-027: tanpa tarif di
kode modul)."""

import uuid

from odoo.fields import Command
from odoo.tests import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

PICKUP_CODE = "ABCD2345"


class EfnOnlineCommon(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Fixture dibuat sebagai superuser; skenario akses diuji eksplisit dengan with_user().
        cls.env = cls.env(su=True)
        cls.env["ir.config_parameter"].sudo().set_param("efn_online.pickup_code_kdf_rounds", "1000")
        if "sale_ok" in cls.env["loyalty.program"]._fields:
            # Program loyalty lain di DB (demo POS) tidak boleh memengaruhi SO uji (temuan #37 diuji eksplisit).
            cls.env["loyalty.program"].with_context(active_test=False).search([]).write({"sale_ok": False})
        cls.company = cls.company_data["company"]
        cls.currency = cls.company.currency_id
        cls.setup_channel_fixture()

    @classmethod
    def _make_channel(cls, company, code, login, account_data):
        income = account_data["default_account_revenue"]
        expense = account_data["default_account_expense"]
        tax = cls.env["account.tax"].create(
            {
                "name": f"PPN uji {code}",
                "invoice_label": "PPN 11%",
                "amount": 11.0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "company_id": company.id,
            }
        )
        rounding = cls.env["account.cash.rounding"].create(
            {"name": f"Rp1 {code}", "rounding": 1.0, "strategy": "add_invoice_line", "rounding_method": "HALF-UP"}
        )
        rounding.with_company(company).write({"profit_account_id": income.id, "loss_account_id": expense.id})
        journal = cls.env["account.journal"].create(
            {"name": f"Titipan Online {code}", "type": "bank", "code": f"T{code[:3]}", "company_id": company.id}
        )
        pricelist = cls.env["product.pricelist"].create(
            {"name": f"Online {code}", "currency_id": company.currency_id.id, "company_id": company.id}
        )
        partner = cls.env["res.partner"].create({"name": f"Pelanggan Super-App {code}", "company_id": company.id})
        warehouse = cls.env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        user = new_test_user(
            cls.env,
            login=login,
            groups="base.group_user,efn_online_order.group_online_channel",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )
        channel = cls.env["efn.online.channel"].create(
            {
                "name": f"Kanal {code}",
                "code": code,
                "company_id": company.id,
                "pricelist_id": pricelist.id,
                "partner_id": partner.id,
                "cash_rounding_id": rounding.id,
                "journal_titipan_id": journal.id,
                "user_ids": [Command.set(user.ids)],
                "outlet_ids": [
                    Command.create({"code": "TOKO-T1", "public_name": "Toko Uji", "warehouse_id": warehouse.id})
                ],
            }
        )
        return channel, user, tax, warehouse, income

    @classmethod
    def setup_channel_fixture(cls):
        cls.channel, cls.tech_user, cls.tax, cls.warehouse, income = cls._make_channel(
            cls.company, "TST", "efn_online_tst", cls.company_data
        )
        cls.outlet = cls.channel.outlet_ids
        cls.pricelist = cls.channel.pricelist_id
        Product = cls.env["product.product"].with_company(cls.company)
        common = {
            "type": "consu",
            "taxes_id": [Command.set(cls.tax.ids)],
            "company_id": cls.company.id,
            "categ_id": cls.product_category.id,
            "property_account_income_id": income.id,
        }
        cls.p_rice = Product.create(
            {
                **common,
                "name": "Beras Online",
                "is_storable": True,
                "list_price": 9999.0,
                "efn_online_channel_ids": [Command.set(cls.channel.ids)],
                "efn_online_description": "Beras 5 kg",
            }
        )
        cls.p_bag = Product.create(
            {
                **common,
                "name": "Kantong Online",
                "list_price": 500.0,
                "efn_online_channel_ids": [Command.set(cls.channel.ids)],
            }
        )
        cls.p_hidden = Product.create({**common, "name": "Tidak Online", "list_price": 100.0})
        cls.env["stock.quant"]._update_available_quantity(cls.p_rice, cls.warehouse.lot_stock_id, 10.0)

        cls.company_data_2 = cls.setup_other_company(name="company_2_online")
        cls.company_2 = cls.company_data_2["company"]
        cls.channel_2, cls.tech_user_2, _tax2, _wh2, _inc2 = cls._make_channel(
            cls.company_2, "TSB", "efn_online_tsb", cls.company_data_2
        )

    # ------------------------------------------------------------------ helpers
    def channel_api(self, user=None):
        return self.env["efn.online.channel"].with_user(user or self.tech_user)

    def order_api(self, user=None):
        return self.env["efn.online.order"].with_user(user or self.tech_user)

    def quote(self, lines=None, user=None):
        lines = lines if lines is not None else [{"product_id": self.p_rice.id, "qty": 3}]
        return self.channel_api(user).efn_online_quote("TST", "TOKO-T1", "pickup", lines)

    def place(self, lines=None, quote_hash=None, part=None, key=None, code=PICKUP_CODE):
        lines = lines if lines is not None else [{"product_id": self.p_rice.id, "qty": 3}]
        if quote_hash is None:
            quote_hash = self.quote(lines)["quote_hash"]
        part = part or str(uuid.uuid4())
        payload = {
            "idempotency_key": key or f"online:place:{part}",
            "platform_order_ref": part,
            "checkout_ref": str(uuid.uuid4()),
            "outlet_code": "TOKO-T1",
            "fulfilment": "pickup",
            "lines": lines,
            "quote_hash": quote_hash,
            "pickup_code": code,
        }
        result = self.order_api().efn_online_place("TST", payload)
        return part, payload, result

    def record(self, part):
        return self.env["efn.online.order"].sudo().search([("platform_order_ref", "=", part)])

    def pay(self, part, amount=None, payment_ref=None, key=None):
        order = self.record(part)
        payment_ref = payment_ref or str(uuid.uuid4())
        return self.order_api().efn_online_mark_paid(
            "TST",
            part,
            {
                "payment_ref": payment_ref,
                "amount_paid": order.amount_payable if amount is None else amount,
                "paid_at": "2026-10-09T10:00:00Z",
                "idempotency_key": key or f"online:paid:{part}:{payment_ref}",
            },
        )
