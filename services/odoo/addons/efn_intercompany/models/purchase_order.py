"""PO antar-PT -> SO di PT penjual + harga transfer cost-plus (docs/08 §5.2 langkah 1-2, W-15)."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    efn_ic_company_id = fields.Many2one(
        "res.company", string="PT penjual (antar-PT)", compute="_compute_efn_ic_company_id", store=True
    )
    efn_ic_sale_id = fields.Many2one("sale.order", string="SO antar-PT", copy=False, readonly=True)
    efn_ic_sale_name = fields.Char(string="SO antar-PT", compute="_compute_efn_ic_sale_name")

    _efn_ic_sale_unique = models.Constraint("UNIQUE(efn_ic_sale_id)", "Satu SO antar-PT per PO.")

    @api.depends("partner_id", "company_id")
    def _compute_efn_ic_company_id(self):
        Company = self.env["res.company"]
        for order in self:
            seller = Company._efn_ic_company_of_partner(order.partner_id)
            order.efn_ic_company_id = seller if seller and seller != order.company_id else False

    def _compute_efn_ic_sale_name(self):
        # Nama dibaca sudo: user PT pembeli tidak punya akses dokumen PT penjual.
        for order in self:
            sale = order.sudo().efn_ic_sale_id
            order.efn_ic_sale_name = f"{sale.name} ({sale.company_id.name})" if sale else False

    def button_confirm(self):
        self.filtered(lambda o: o.state in ("draft", "sent") and o.efn_ic_company_id)._efn_ic_apply_transfer_price()
        return super().button_confirm()

    def button_approve(self, force=False):
        res = super().button_approve(force=force)
        self.filtered(lambda o: o.state == "purchase")._efn_ic_create_sale()
        return res

    def button_cancel(self):
        res = super().button_cancel()
        for order in self.filtered("efn_ic_sale_id"):
            sale = order.sudo().efn_ic_sale_id
            if sale.state == "draft":
                sale.with_env(order.efn_ic_company_id._efn_ic_env())._action_cancel()
                sale.message_post(body=_("Antar-PT: dibatalkan karena PO %(po)s dibatalkan.", po=order.name))
            elif sale.state == "sale":
                sale.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("PO antar-PT %(po)s dibatalkan pembeli", po=order.name),
                    user_id=order.efn_ic_company_id.efn_ic_user_id.id or self.env.uid,
                )
        return res

    def _efn_ic_apply_transfer_price(self):
        TP = self.env["efn.ic.transfer.price"]
        for order in self:
            for line in order.order_line.filtered(lambda line: not line.display_type and line.product_id):
                price, rule = TP._efn_price_for(order.efn_ic_company_id, order.company_id, line.product_id)
                if price is None:
                    continue
                if line.product_uom_id and line.product_uom_id != line.product_id.uom_id:
                    price = line.product_id.uom_id._compute_price(price, line.product_uom_id)
                line.write({"price_unit": price, "efn_ic_transfer_price_id": rule.id})

    def _efn_ic_create_sale(self):
        for order in self:
            seller = order.efn_ic_company_id
            if not seller or not seller.efn_ic_so_from_po or order.efn_ic_sale_id:
                continue
            if order.currency_id != seller.currency_id:
                raise UserError(_("efn_intercompany:currency — mata uang PO harus sama dengan PT penjual."))
            try:
                env = seller._efn_ic_env()
            except ValidationError as exc:
                raise UserError(exc.args[0]) from exc
            po_lines = order.order_line.filtered(lambda line: not line.display_type and line.product_id)
            commands = []
            for line in po_lines:
                product = line.product_id
                if product.company_id and product.company_id != seller:
                    raise UserError(
                        _(
                            "efn_intercompany:product_not_shared — produk %(product)s milik %(owner)s; produk antar-PT "
                            "harus produk bersama (company kosong) atau milik PT penjual.",
                            product=product.display_name,
                            owner=product.company_id.name,
                        )
                    )
                commands.append(
                    Command.create(
                        {
                            "product_id": product.id,
                            "product_uom_qty": line.product_qty,
                            "product_uom_id": line.product_uom_id.id,
                            "price_unit": line.price_unit,
                            "efn_ic_purchase_line_id": line.id,
                        }
                    )
                )
            if not commands:
                continue
            vals = {
                "company_id": seller.id,
                "partner_id": order.company_id.partner_id.id,
                "client_order_ref": order.name,
                "origin": order.name,
                "efn_ic_purchase_id": order.id,
                "order_line": commands,
            }
            if seller.sudo().efn_ic_warehouse_id:
                vals["warehouse_id"] = seller.sudo().efn_ic_warehouse_id.id
            sale = env["sale.order"].with_company(seller).create(vals)
            # Harga dikunci ke harga PO (bukan pricelist PT penjual).
            for sale_line, po_line in zip(sale.order_line, po_lines, strict=True):
                if sale_line.price_unit != po_line.price_unit:
                    sale_line.price_unit = po_line.price_unit
            order.sudo().efn_ic_sale_id = sale.id
            if seller.efn_ic_auto_confirm_so:
                sale.action_confirm()
            order.message_post(
                body=_("Antar-PT: SO %(so)s dibuat di %(seller)s.", so=sale.name, seller=seller.name),
                subtype_xmlid="mail.mt_note",
            )


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    efn_ic_transfer_price_id = fields.Many2one(
        "efn.ic.transfer.price", string="Aturan harga transfer", readonly=True, copy=False
    )
