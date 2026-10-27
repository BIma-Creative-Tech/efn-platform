"""Faktur PT penjual diposting -> tagihan vendor DRAFT di PT pembeli (docs/08 §5.2 langkah 3-4).

Baris tagihan dibangun dari baris PO asal (``_prepare_account_move_line``): pajak = pajak pembelian PT pembeli,
``purchase_line_id`` terisi (3-way match). Pajak faktur TIDAK disalin (PPN keluaran penjual != PPN masukan pembeli;
PB1 tidak berlaku B2B). Selisih total -> catatan + aktivitas akuntan, tagihan tetap draft.
Faktur dibatalkan/di-reset -> tagihan draft dibatalkan + tautan dilepas (posting ulang membuat tagihan baru);
tagihan yang sudah diposting hanya diberi aktivitas (tanpa reverse otomatis).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command


class AccountMove(models.Model):
    _inherit = "account.move"

    efn_ic_source_move_id = fields.Many2one(
        "account.move", string="Faktur antar-PT asal", copy=False, readonly=True, index="btree_not_null"
    )
    efn_ic_bill_ids = fields.One2many("account.move", "efn_ic_source_move_id", string="Tagihan antar-PT")

    _efn_ic_source_unique = models.Constraint(
        "UNIQUE(efn_ic_source_move_id)", "Satu faktur antar-PT hanya menghasilkan satu tagihan aktif."
    )

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted.filtered(lambda m: m.move_type == "out_invoice")._efn_ic_create_bills()
        return posted

    @api.model
    def _efn_ic_bill_lines(self, invoice, buyer):
        commands, purchases = [], self.env["purchase.order"].sudo()
        for inv_line in invoice.invoice_line_ids.filtered(lambda line: line.display_type == "product"):
            po_line = inv_line.sudo().sale_line_ids.efn_ic_purchase_line_id[:1]
            if not po_line:
                continue
            purchases |= po_line.order_id
            vals = po_line._prepare_account_move_line()
            # Harga = DPP per unit faktur (price_subtotal / qty), bukan price_unit: pajak penjualan penjual bisa
            # "termasuk harga" sedangkan pajak pembelian pembeli tidak -> tanpa ini DPP tagihan membengkak.
            unit = inv_line.price_subtotal / inv_line.quantity if inv_line.quantity else inv_line.price_unit
            vals.update({"quantity": inv_line.quantity, "price_unit": unit, "discount": 0.0})
            commands.append(Command.create(vals))
        return commands, purchases

    def _efn_ic_create_bills(self):
        Company = self.env["res.company"]
        for invoice in self:
            buyer = Company._efn_ic_company_of_partner(invoice.partner_id)
            if not buyer or buyer == invoice.company_id or not buyer.efn_ic_bill_from_invoice:
                continue
            if invoice.sudo().efn_ic_bill_ids:
                continue
            commands, purchases = self._efn_ic_bill_lines(invoice, buyer)
            if not commands:
                continue  # faktur bukan dari SO antar-PT
            try:
                env = buyer._efn_ic_env()
            except ValidationError as exc:
                raise UserError(exc.args[0]) from exc
            bill = (
                env["account.move"]
                .with_company(buyer)
                .with_context(default_move_type="in_invoice")
                .create(
                    {
                        "move_type": "in_invoice",
                        "company_id": buyer.id,
                        "partner_id": invoice.company_id.partner_id.id,
                        "ref": invoice.name,
                        "invoice_date": invoice.invoice_date,
                        "invoice_origin": ", ".join(purchases.mapped("name")),
                        "invoice_line_ids": commands,
                    }
                )
            )
            bill.sudo().efn_ic_source_move_id = invoice.id
            body = _(
                "Antar-PT: tagihan draft dari faktur %(inv)s (%(seller)s).",
                inv=invoice.name,
                seller=invoice.company_id.name,
            )
            if buyer.currency_id.compare_amounts(bill.amount_total, invoice.amount_total):
                body += " " + _(
                    "Total berbeda dengan faktur (%(bill)s vs %(inv)s): periksa pajak masukan sebelum posting.",
                    bill=bill.amount_total,
                    inv=invoice.amount_total,
                )
                bill.sudo().activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("Periksa selisih tagihan antar-PT"),
                    user_id=buyer.efn_ic_user_id.id,
                )
            bill.sudo().message_post(body=body, subtype_xmlid="mail.mt_note")
            invoice.message_post(
                body=_("Antar-PT: tagihan draft dibuat di %(buyer)s.", buyer=buyer.name), subtype_xmlid="mail.mt_note"
            )

    def button_draft(self):
        res = super().button_draft()
        self._efn_ic_cancel_bills()
        return res

    def button_cancel(self):
        res = super().button_cancel()
        self._efn_ic_cancel_bills()
        return res

    def _efn_ic_cancel_bills(self):
        invoices = self.filtered(lambda m: m.move_type == "out_invoice")
        if not invoices:
            return
        for bill in invoices.sudo().efn_ic_bill_ids:
            source = bill.efn_ic_source_move_id
            if bill.state == "draft":
                bill.button_cancel()
                bill.efn_ic_source_move_id = False
                bill.message_post(
                    body=_("Antar-PT: dibatalkan karena faktur asal %(inv)s dibatalkan/di-reset.", inv=source.name),
                    subtype_xmlid="mail.mt_note",
                )
            elif bill.state == "posted":
                bill.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("Faktur antar-PT asal %(inv)s dibatalkan: buat nota kredit", inv=source.name),
                    user_id=bill.company_id.efn_ic_user_id.id or self.env.uid,
                )
