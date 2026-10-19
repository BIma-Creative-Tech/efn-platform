"""Fixture E-45: kanal online 'counter' + user teknis + produk ber-stok per company uji. Semua angka (harga, pajak 11 %,
pembulatan Rp1) = data uji (ADR-027: tanpa tarif di kode modul)."""

import uuid

from odoo.fields import Command
from odoo.tests import new_test_user

PICKUP_CODE = "ABCD2345"
OUTLET = "TOKO-C1"


def set_test_params(env):
    params = env["ir.config_parameter"].sudo()
    params.set_param("efn_online.pickup_code_kdf_rounds", "1000")
    params.set_param("efn_online.counter_code_kdf_rounds", "1000")
    if "sale_ok" in env["loyalty.program"]._fields:
        env["loyalty.program"].sudo().with_context(active_test=False).search([]).write({"sale_ok": False})


def make_counter_channel(env, company, warehouse, code, login):
    """Kanal ``code`` (qris,counter) di ``company`` dengan outlet ``TOKO-C1`` di ``warehouse``.

    :returns: dict ``channel``, ``tech`` (user teknis), ``product`` (produk online ber-stok 50).
    """
    env = env(su=True)
    Account = env["account.account"].with_company(company)
    income = Account.search([("company_ids", "in", company.ids), ("account_type", "=", "income")], limit=1)
    expense = Account.search([("company_ids", "in", company.ids), ("account_type", "=", "expense")], limit=1)
    tax = env["account.tax"].create(
        {
            "name": f"PPN uji {code}",
            "invoice_label": "PPN 11%",
            "amount": 11.0,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "company_id": company.id,
        }
    )
    rounding = env["account.cash.rounding"].create(
        {"name": f"Rp1 {code}", "rounding": 1.0, "strategy": "add_invoice_line", "rounding_method": "HALF-UP"}
    )
    rounding.with_company(company).write({"profit_account_id": income.id, "loss_account_id": expense.id})
    titipan = env["account.journal"].create(
        {"name": f"Titipan Online {code}", "type": "bank", "code": f"T{code[:3]}", "company_id": company.id}
    )
    pricelist = env["product.pricelist"].create(
        {"name": f"Online {code}", "currency_id": company.currency_id.id, "company_id": company.id}
    )
    partner = env["res.partner"].create({"name": f"Pelanggan Super-App {code}", "company_id": company.id})
    tech = new_test_user(
        env,
        login=login,
        groups="base.group_user,efn_online_order.group_online_channel",
        company_id=company.id,
        company_ids=[Command.set(company.ids)],
    )
    channel = env["efn.online.channel"].create(
        {
            "name": f"Kanal {code}",
            "code": code,
            "company_id": company.id,
            "pricelist_id": pricelist.id,
            "partner_id": partner.id,
            "cash_rounding_id": rounding.id,
            "journal_titipan_id": titipan.id,
            "payment_modes": "qris,counter",
            "user_ids": [Command.set(tech.ids)],
            "outlet_ids": [Command.create({"code": OUTLET, "public_name": "Toko Kasir", "warehouse_id": warehouse.id})],
        }
    )
    product = (
        env["product.product"]
        .with_company(company)
        .create(
            {
                "name": f"Beras Online {code}",
                "type": "consu",
                "is_storable": True,
                "list_price": 9999.0,
                "taxes_id": [Command.set(tax.ids)],
                "company_id": company.id,
                "property_account_income_id": income.id,
                "efn_online_channel_ids": [Command.set(channel.ids)],
            }
        )
    )
    env["stock.quant"]._update_available_quantity(product, warehouse.lot_stock_id, 50.0)
    return {"channel": channel, "tech": tech, "product": product}


def place_counter(env, fixture, counter_code, qty=1, pickup_code=PICKUP_CODE, payment_mode="counter", outlet=OUTLET):
    """Place pesanan lewat metode BFF (user teknis). Kembalikan (part, payload, hasil)."""
    channel, tech = fixture["channel"], fixture["tech"]
    lines = [{"product_id": fixture["product"].id, "qty": qty}]
    quote = env["efn.online.channel"].with_user(tech).efn_online_quote(channel.code, outlet, "pickup", lines)
    part = str(uuid.uuid4())
    payload = {
        "idempotency_key": f"online:place:{part}",
        "platform_order_ref": part,
        "checkout_ref": str(uuid.uuid4()),
        "outlet_code": outlet,
        "fulfilment": "pickup",
        "lines": lines,
        "quote_hash": quote["quote_hash"],
        "pickup_code": pickup_code,
        "payment_mode": payment_mode,
    }
    if counter_code is not None:
        payload["counter_code"] = counter_code
    result = env["efn.online.order"].with_user(tech).efn_online_place(channel.code, payload)
    return part, payload, result


def online_record(env, part):
    return env["efn.online.order"].sudo().search([("platform_order_ref", "=", part)])
