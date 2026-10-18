"""Data bayar di kasir per PT (docs/08 §11.8 no. 1): produk layanan *Pelunasan Pesanan Online* (tanpa pajak, akun
pendapatan = akun kliring) + jurnal kliring (pembayaran faktur SO, metode pembayaran masuk/keluar memakai akun kliring
yang sama) -> kliring bersih 0.

Semua nilai = PLACEHOLDER (Finance mengganti akun/kode/nama per PT, configurable; ADR-027: tanpa tarif di kode).
Dibuat lazy oleh ``_efn_online_counter_ensure`` (kanal diberi metode 'counter'). Tidak ada metode publik (SEC-58).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Command

_logger = logging.getLogger(__name__)

CLEARING_ACCOUNT_NAME = "Kliring Bayar di Kasir (PLACEHOLDER)"
CLEARING_ACCOUNT_CODE_BASE = 119990
CLEARING_JOURNAL_NAME = "Kliring Bayar di Kasir (PLACEHOLDER)"
CLEARING_JOURNAL_CODES = ("KKSR", "KKSR2", "KKSR3", "KKS4", "KKS5")
PRODUCT_NAME = "Pelunasan Pesanan Online"
PRODUCT_DEFAULT_CODE = "EFN-PELUNASAN-ONLINE"


class ResCompany(models.Model):
    _inherit = "res.company"

    efn_online_counter_product_id = fields.Many2one(
        "product.product",
        string="Produk pelunasan pesanan online",
        copy=False,
        domain="[('company_id', '=', id)]",
        help="E-45: produk layanan non-stok tanpa pajak; akun pendapatannya = akun kliring bayar di kasir.",
    )
    efn_online_counter_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal kliring bayar di kasir",
        copy=False,
        domain="[('type', '=', 'bank'), ('company_id', '=', id)]",
        help="E-45: faktur SO pesanan bayar di kasir dibayar di jurnal ini (akun pembayaran = akun kliring).",
    )

    # ------------------------------------------------------------------ setup
    @api.constrains("efn_online_counter_product_id", "efn_online_counter_journal_id")
    def _check_efn_online_counter(self):
        for company in self.sudo():
            product = company.efn_online_counter_product_id
            journal = company.efn_online_counter_journal_id
            if product and (product.company_id != company or product.type != "service"):
                raise ValidationError(_("Produk pelunasan harus produk layanan milik company ini."))
            if product and product.with_company(company).taxes_id.filtered(lambda t, c=company: t.company_id == c):
                raise ValidationError(_("Produk pelunasan pesanan online tidak boleh berpajak (pajak hanya di SO)."))
            if journal and (journal.company_id != company or journal.type != "bank"):
                raise ValidationError(_("Jurnal kliring harus jurnal Bank milik company ini."))

    def _efn_online_counter_ready(self):
        self.ensure_one()
        company = self.sudo()
        return bool(company.efn_online_counter_product_id and company.efn_online_counter_journal_id)

    def _efn_online_counter_has_chart(self):
        self.ensure_one()
        return bool(self.env["account.account"].sudo().search_count([("company_ids", "in", self.ids)], limit=1))

    def _efn_online_counter_ensure(self, raise_if_no_chart=False):
        """Buat data PLACEHOLDER yang belum ada untuk setiap company. Kembalikan company yang siap."""
        ready = self.browse()
        for company in self.sudo():
            if company._efn_online_counter_ready():
                ready |= company
                continue
            if not company._efn_online_counter_has_chart():
                if raise_if_no_chart:
                    raise ValidationError(
                        _(
                            "Company %(name)s belum punya chart of accounts; bayar di kasir belum bisa diaktifkan.",
                            name=company.name,
                        )
                    )
                continue
            account = company._efn_online_counter_create_account()
            journal = company.efn_online_counter_journal_id or company._efn_online_counter_create_journal(account)
            product = company.efn_online_counter_product_id or company._efn_online_counter_create_product(account)
            company.write({"efn_online_counter_journal_id": journal.id, "efn_online_counter_product_id": product.id})
            _logger.warning(
                "EFN bayar di kasir: data kliring PLACEHOLDER dibuat untuk company #%s (akun %s, jurnal %s); "
                "Finance wajib meninjau akun sebelum go-live",
                company.id,
                account.code,
                journal.code,
            )
            ready |= company
        return ready

    def _efn_online_counter_account(self):
        """Akun kliring = akun pendapatan produk pelunasan (sumber kebenaran), atau kosong."""
        self.ensure_one()
        product = self.sudo().efn_online_counter_product_id
        if not product:
            return self.env["account.account"]
        return product.with_company(self).product_tmpl_id.property_account_income_id

    def _efn_online_counter_create_account(self):
        self.ensure_one()
        existing = self._efn_online_counter_account()
        if existing:
            return existing
        Account = self.env["account.account"].sudo().with_company(self)
        code = None
        for offset in range(100):
            candidate = str(CLEARING_ACCOUNT_CODE_BASE + offset)
            if not Account.search_count([("code", "=", candidate), ("company_ids", "in", self.ids)], limit=1):
                code = candidate
                break
        if not code:
            raise ValidationError(_("Tidak ada kode akun kosong untuk akun kliring bayar di kasir."))
        return Account.create(
            {
                "name": CLEARING_ACCOUNT_NAME,
                "code": code,
                "account_type": "asset_current",
                "reconcile": True,
                "company_ids": [Command.set(self.ids)],
            }
        )

    def _efn_online_counter_create_journal(self, account):
        self.ensure_one()
        Journal = self.env["account.journal"].sudo().with_company(self)
        used = set(Journal.search([("company_id", "=", self.id)]).mapped("code"))
        code = next((c for c in CLEARING_JOURNAL_CODES if c not in used), None)
        if not code:
            raise ValidationError(_("Kode jurnal kliring bayar di kasir sudah terpakai semua."))
        journal = Journal.create({"name": CLEARING_JOURNAL_NAME, "type": "bank", "code": code, "company_id": self.id})
        lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
        if not journal.inbound_payment_method_line_ids or not journal.outbound_payment_method_line_ids:
            raise ValidationError(_("Jurnal kliring tanpa metode pembayaran masuk/keluar (manual)."))
        # Pembayaran faktur (masuk) dan refund (keluar) memakai akun kliring -> lawan baris POS di akun yang sama.
        lines.write({"payment_account_id": account.id})
        return journal

    def _efn_online_counter_create_product(self, account):
        self.ensure_one()
        product = (
            self.env["product.product"]
            .sudo()
            .with_company(self)
            .create(
                {
                    "name": PRODUCT_NAME,
                    "default_code": PRODUCT_DEFAULT_CODE,
                    "type": "service",
                    "list_price": 0.0,
                    "sale_ok": False,
                    "purchase_ok": False,
                    "available_in_pos": False,
                    "taxes_id": [Command.clear()],
                    "supplier_taxes_id": [Command.clear()],
                    "company_id": self.id,
                }
            )
        )
        product.product_tmpl_id.with_company(self).property_account_income_id = account
        return product
