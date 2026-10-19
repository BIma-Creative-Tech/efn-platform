"""Fixture E-27 (efn_pos_bff_order): POS restoran PT F&B uji, pajak PB1 + SC dine-in lewat fiscal position preset,
user teknis QR, BFF tiruan (requests.post di-mock). Tidak ada rahasia/PII nyata (nilai uji)."""

import json
import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

from odoo import Command, fields
from odoo.tests import new_test_user

from odoo.addons.point_of_sale.tests.common import TestPoSCommon

POST = "odoo.addons.efn_core.models.efn_bff_client.requests.post"  # objek requests sama untuk outbox
SECRET = "q" * 48  # HMAC uji (bukan rahasia)
# Token uji berbentuk fts1 (bukan token sungguhan). Tidak boleh muncul di DB/log.
FAKE_TOKEN = "fts1.QUJDREVGR0hJSktMTU5PUA.1760000000.c2lnbmF0dXJlLXVqaS1idWthbi1yYWhhc2lh"
QR_URL = f"https://pb.example.test/meja#t={FAKE_TOKEN}"


def response(status, data=None):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = data if data is not None else {"ok": True, "status": "applied"}
    return mock


class EfnQrCommon(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.config = cls.basic_config
        cls.company = cls.config.company_id
        others = env["res.company"].search([("efn_brand_code", "in", ("PB", "BCF")), ("id", "!=", cls.company.id)])
        others.write({"efn_brand_code": False})
        cls.company.efn_brand_code = "PB"

        Tax = env["account.tax"]
        common = {"type_tax_use": "sale", "amount_type": "percent", "company_id": cls.company.id}
        cls.tax_pb1 = Tax.create({**common, "name": "PB1 10% (uji QR)", "invoice_label": "PB1", "amount": 10.0})
        cls.tax_sc = Tax.create(
            {
                **common,
                "name": "Service Charge 5% (uji QR)",
                "invoice_label": "Service",
                "amount": 5.0,
                "sequence": 5,
                "include_base_amount": True,
                "original_tax_ids": [Command.set(cls.tax_pb1.ids)],
            }
        )
        cls.tax_pb1_dine = Tax.create(
            {
                **common,
                "name": "PB1 Dine-in 10% (uji QR)",
                "invoice_label": "PB1",
                "amount": 10.0,
                "sequence": 10,
                "is_base_affected": True,
                "original_tax_ids": [Command.set(cls.tax_pb1.ids)],
            }
        )
        cls.fp_dine = env["account.fiscal.position"].create(
            {
                "name": "Dine-in uji QR",
                "company_id": cls.company.id,
                "auto_apply": False,
                "tax_ids": [Command.set((cls.tax_sc | cls.tax_pb1_dine).ids)],
            }
        )
        cls.preset = env["pos.preset"].create(
            {
                "name": "Makan di tempat (uji QR)",
                "fiscal_position_id": cls.fp_dine.id,
                "service_at": "table",
                "available_in_self": False,
            }
        )
        cls.categ_bar = env["pos.category"].create({"name": "QR Bar Kopi"})
        cls.categ_other = env["pos.category"].create({"name": "QR Lain"})

        Attribute = env["product.attribute"]
        cls.attr_sugar = Attribute.create(
            {
                "name": "Gula (uji QR)",
                "create_variant": "no_variant",
                "display_type": "radio",
                "value_ids": [Command.create({"name": "Normal"}), Command.create({"name": "Kurang"})],
            }
        )
        cls.attr_extra = Attribute.create(
            {
                "name": "Ekstra (uji QR)",
                "create_variant": "no_variant",
                "display_type": "multi",
                "value_ids": [Command.create({"name": "Shot espresso", "default_extra_price": 6000.0})],
            }
        )
        cls.attr_custom = Attribute.create(
            {
                "name": "Permintaan (uji QR)",
                "create_variant": "no_variant",
                "display_type": "radio",
                "value_ids": [Command.create({"name": "Lainnya", "is_custom": True})],
            }
        )

        def template(name, price, categ, **extra):
            return env["product.template"].create(
                {
                    "name": name,
                    "list_price": price,
                    "available_in_pos": True,
                    "taxes_id": [Command.set(cls.tax_pb1.ids)],
                    "pos_categ_ids": [Command.set(categ.ids)],
                    **extra,
                }
            )

        cls.tmpl_latte = template(
            "Latte (uji QR)",
            100000.0,
            cls.categ_bar,
            attribute_line_ids=[
                Command.create({"attribute_id": attr.id, "value_ids": [Command.set(attr.value_ids.ids)]})
                for attr in (cls.attr_sugar, cls.attr_extra, cls.attr_custom)
            ],
        )
        cls.latte = cls.tmpl_latte.product_variant_id
        cls.croissant = template("Croissant (uji QR)", 25000.0, cls.categ_other).product_variant_id
        cls.hidden = template("Menu tersembunyi (uji QR)", 20000.0, cls.categ_other).product_variant_id
        cls.hidden.product_tmpl_id.self_order_available = False
        cls.not_pos = template("Bukan POS (uji QR)", 20000.0, cls.categ_other).product_variant_id
        cls.not_pos.product_tmpl_id.available_in_pos = False
        ptavs = cls.tmpl_latte.attribute_line_ids.product_template_value_ids
        cls.ptav_normal = ptavs.filtered(lambda v: v.name == "Normal")
        cls.ptav_less = ptavs.filtered(lambda v: v.name == "Kurang")
        cls.ptav_shot = ptavs.filtered(lambda v: v.name == "Shot espresso")
        cls.ptav_custom = ptavs.filtered(lambda v: v.is_custom)

        cls.floor = env["restaurant.floor"].create({"name": "Teras", "pos_config_ids": [Command.link(cls.config.id)]})
        cls.table = env["restaurant.table"].create({"table_number": 12, "floor_id": cls.floor.id, "seats": 4})
        cls.table2 = env["restaurant.table"].create({"table_number": 13, "floor_id": cls.floor.id, "seats": 2})
        cls.station = env["efn.kds.station"].create(
            {
                "name": "Bar (uji QR)",
                "company_id": cls.company.id,
                "config_ids": [Command.link(cls.config.id)],
                "category_ids": [Command.link(cls.categ_bar.id)],
            }
        )
        cls.config.write(
            {
                "module_pos_restaurant": True,
                "use_presets": True,
                "available_preset_ids": [Command.set(cls.preset.ids)],
                "default_preset_id": cls.preset.id,
                "efn_qr_preset_id": cls.preset.id,
                "efn_qr_enabled": True,
            }
        )
        cls.tech = new_test_user(
            env,
            login="efn_qr_tech_uji",
            groups="base.group_user,efn_pos_bff_order.group_qr_bff",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.cashier = new_test_user(
            env,
            login="efn_qr_cashier_uji",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

    def setUp(self):
        super().setUp()
        tmp = tempfile.mkdtemp()
        self.paths = {}
        for name, content in (("hmac", SECRET), ("key", "svc-key-uji"), ("ca", "dummy-ca")):
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            self.paths[name] = path
        env_patch = patch.dict(
            os.environ,
            {
                "EFN_FNB_EVENT_HMAC_FILE": self.paths["hmac"],
                "EFN_BFF_API_KEY_FILE": self.paths["key"],
                "EFN_CA_FILE": self.paths["ca"],
                "EFN_BFF_URL": "https://api-gateway:3000",
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.sent = []
        bus_cls = type(self.env["bus.bus"])
        sent = self.sent

        def fake_sendone(_bus, target, notification_type, message):
            sent.append((target, notification_type, message))

        bus_patch = patch.object(bus_cls, "_sendone", autospec=True, side_effect=fake_sendone)
        bus_patch.start()
        self.addCleanup(bus_patch.stop)
        self.bff_calls = []
        self.open_new_session()

    # ------------------------------------------------------------------ BFF tiruan
    def _fake_post(self, url, **kwargs):
        if url.endswith("/v1/pos/fnb/table-sessions"):
            body = kwargs.get("json") or {}
            self.bff_calls.append(("open", body))
            session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, body.get("idempotency_key", "x")))
            return response(
                201,
                {
                    "table_session_id": session_id,
                    "qr_url": QR_URL,
                    "expires_at": fields.Datetime.to_string(
                        fields.Datetime.add(fields.Datetime.now(), hours=4)
                    ).replace(" ", "T")
                    + "Z",
                },
            )
        self.bff_calls.append(("event", kwargs.get("data")))
        return response(200)

    # ------------------------------------------------------------------ helper
    def _anchor(self, table=None, lines=None):
        """Order pelayan (draft) di meja, disinkron seperti frontend POS."""
        data = self.create_ui_order_data(lines or [], payments=[])
        data["lines"] = [list(command) for command in data["lines"]]
        data.update({"state": "draft", "table_id": (table or self.table).id})
        result = self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].browse(result["pos.order"][0]["id"])

    def _open(self, table=None, anchor=None, reissue=False):
        table = table or self.table
        anchor = anchor or self._anchor(table)
        with patch(POST, side_effect=self._fake_post):
            result = self.config.efn_qr_session_open(table.id, anchor.uuid, reissue)
        session = self.env["efn.pos.table.session"].search(
            [("config_id", "=", self.config.id), ("table_id", "=", table.id), ("state", "=", "open")]
        )
        return result, session, anchor

    def _submission(self, lines, key=None, ref=None):
        ref = ref or str(uuid.uuid4())
        return {"idempotency_key": key or f"fnb:submit:{ref}", "submission_ref": ref, "lines": lines}

    def _submit(self, session, lines, user=None, **kw):
        return (
            self.env["efn.pos.qr.order"]
            .with_user(user or self.tech)
            .efn_qr_submit(
                config_id=self.config.id,
                table_session_ref=session.bff_session_id,
                submission=self._submission(lines, **kw),
            )
        )

    def _qr_order(self, result):
        return self.env["efn.pos.qr.order"].search([("submission_ref", "=", result["submission_ref"])])

    def _sync_existing(self, order, **extra):
        """Sinkron ulang order yang sudah ada (seperti frontend) tanpa mengubah baris."""
        data = {
            "uuid": order.uuid,
            "session_id": order.session_id.id,
            "state": "draft",
            "lines": [],
            "payment_ids": [],
            "amount_total": order.amount_total,
            "amount_tax": order.amount_tax,
            "amount_paid": 0.0,
            "amount_return": 0.0,
            "fiscal_position_id": order.fiscal_position_id.id,
            "pricelist_id": order.pricelist_id.id,
            "preset_id": order.preset_id.id,
            "table_id": order.table_id.id,
        }
        data.update(extra)
        self.env["pos.order"].sync_from_ui([data])
        return order

    def _send_to_kitchen(self, order):
        """Pelayan menekan *Order* (frontend standar mengisi last_order_preparation_change)."""
        change = {
            "lines": {
                line.uuid: {"uuid": line.uuid, "product_id": line.product_id.id, "quantity": line.qty}
                for line in order.lines
            },
            "metadata": {"serverDate": fields.Datetime.to_string(fields.Datetime.now())},
        }
        return self._sync_existing(order, last_order_preparation_change=json.dumps(change))

    def _pay(self, order, partner=None):
        data = self.create_ui_order_data([], payments=[(self.cash_pm1, order.amount_total)], uuid=order.uuid)
        if partner is not None:
            data["partner_id"] = partner.id
        data.update(
            {
                "state": "paid",
                "lines": [],
                "amount_total": order.amount_total,
                "amount_tax": order.amount_tax,
                "amount_paid": order.amount_total,
                "amount_return": 0.0,
                "fiscal_position_id": order.fiscal_position_id.id,
                "pricelist_id": order.pricelist_id.id,
                "preset_id": order.preset_id.id,
                "table_id": order.table_id.id,
            }
        )
        data["payment_ids"] = [list(command) for command in data["payment_ids"]]
        self.env["pos.order"].sync_from_ui([data])
        return order

    def _outbox(self, session=None):
        domain = [("table_session_id", "=", session.id)] if session else []
        return self.env["efn.pos.qr.event.outbox"].sudo().search(domain, order="seq")

    def _send_outbox(self, status=200):
        """Jalankan outbox event QR dengan BFF tiruan (semua event pending dijawab ``status``).

        Dua pass seperti dua putaran cron: close ``paid`` dibekukan di pass pertama (L-83), dikirim di pass kedua."""
        with patch(POST, return_value=response(status)) as post:
            self.env["efn.pos.qr.event.outbox"]._efn_process_due()
            self.env["efn.pos.qr.event.outbox"]._efn_process_due()
        return post

    def _refund(self, order, qty):
        """Refund ``qty`` unit baris (tunggal) order seperti layar refund POS (proporsional total & pajak)."""
        line = order.lines
        currency = order.currency_id
        total = currency.round(order.amount_total * qty / line.qty)
        tax = currency.round(order.amount_tax * qty / line.qty)
        data = self.create_ui_order_data(
            [
                {
                    "product": line.product_id,
                    "quantity": -qty,
                    "refunded_orderline_id": line.id,
                    "price_unit": line.price_unit,
                }
            ],
            payments=[(self.cash_pm1, -total)],
        )
        data["lines"] = [list(command) for command in data["lines"]]
        data["payment_ids"] = [list(command) for command in data["payment_ids"]]
        data.update(
            {
                "amount_total": -total,
                "amount_tax": -tax,
                "amount_paid": -total,
                "amount_return": 0.0,
                "fiscal_position_id": order.fiscal_position_id.id,
                "pricelist_id": order.pricelist_id.id,
            }
        )
        self.env["pos.order"].sync_from_ui([data])
        # sync_from_ui juga mengembalikan order ASAL refund (refunded_orders) -> cari refund lewat uuid kiriman.
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])], limit=1)

    def _qr_reverses(self, order):
        """Outbox reverse loyalty atas ``order`` (None bila efn_pos_loyalty_ledger tidak terpasang)."""
        if "efn.pos.loyalty.outbox" not in self.env:
            return None
        return (
            self.env["efn.pos.loyalty.outbox"]
            .sudo()
            .search([("original_order_id", "=", order.id), ("kind", "=", "reverse")], order="id")
        )

    def _paid_qr_order(self, qty=4, partner=None):
        """Sesi meja + order pelayan kosong + satu kiriman QR ``qty`` croissant yang dibayar (sesi tetap open)."""
        _r, session, anchor = self._open()
        result = self._submit(session, [{"product_id": self.croissant.id, "qty": qty, "attribute_value_ids": []}])
        order = self._qr_order(result).order_id
        self._pay(order, partner=partner)
        return session, anchor, order
