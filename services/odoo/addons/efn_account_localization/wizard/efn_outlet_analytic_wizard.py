"""G60-C: wizard "Siapkan Analitik Outlet" (pola wizard G54: cek tidak menulis; apply idempoten, savepoint per gudang).

Per PT: plan Outlet (data modul) disimpan di ``company.efn_outlet_analytic_plan_id`` bila kosong; untuk setiap gudang
yang dipakai ``pos.config`` PT itu (atau semua gudang bila dicentang) ditautkan akun analitik plan Outlet: akun yang
sudah tertaut -> ok; akun plan Outlet company dengan kode = kode gudang -> ditautkan; selain itu dibuat (nama = nama
gudang, kode = kode gudang, company = company gudang).
Hak: Accounting Manager + PT wizard termasuk company user (dicek server sebelum sudo).
"""

import logging

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

PLAN_XMLID = "efn_account_localization.analytic_plan_outlet"
STATUS_SELECTION = [
    ("ok", "OK"),
    ("link", "Akan ditautkan"),
    ("create", "Akan dibuat"),
    ("fixed", "Diperbaiki"),
    ("blocked", "Gagal"),
]


class EfnOutletAnalyticWizard(models.TransientModel):
    _name = "efn.outlet.analytic.wizard"
    _description = "EFN: siapkan analitik Outlet per gudang (G60)"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        domain=lambda self: [("id", "in", self.env.user.company_ids.ids)],
    )
    include_all_warehouses = fields.Boolean(
        string="Semua gudang PT",
        help="Default hanya gudang yang dipakai Point of Sale (outlet). Centang untuk menyertakan DC/CK.",
    )
    line_ids = fields.One2many("efn.outlet.analytic.wizard.line", "wizard_id", string="Hasil", readonly=True)
    summary = fields.Char(string="Ringkasan", readonly=True)

    # ---------------------------------------------------------------------------------------------------- entri
    def action_check(self):
        self.ensure_one()
        self._efn_check_access()
        self._efn_write_lines([self._efn_preview(warehouse) for warehouse in self._efn_warehouses()])
        return self._efn_reopen()

    def action_apply(self):
        self.ensure_one()
        self._efn_check_access()
        plan = self._efn_plan()
        company = self.company_id.sudo()
        if not company.efn_outlet_analytic_plan_id:
            company.efn_outlet_analytic_plan_id = plan
        lines = []
        for warehouse in self._efn_warehouses():
            try:
                with self.env.cr.savepoint():
                    lines.append(self._efn_apply_one(warehouse, plan))
            except (UserError, ValidationError, AccessError) as exc:
                self.env.invalidate_all(flush=False)
                _logger.warning("EFN G60: analitik Outlet gudang %s gagal (%s)", warehouse.id, type(exc).__name__)
                lines.append(self._line(warehouse, False, "blocked", str(exc.args[0])[:300] if exc.args else ""))
        self._efn_write_lines(lines)
        return self._efn_reopen()

    # --------------------------------------------------------------------------------------------------- helper
    def _efn_check_access(self):
        if self.env.is_superuser():
            return
        user = self.env.user
        if self.company_id not in user.company_ids:
            raise AccessError(_("Anda tidak punya akses ke company %s.", self.company_id.name))
        if not user.has_group("account.group_account_manager"):
            raise AccessError(_("Wizard analitik Outlet hanya untuk Akuntansi / Administrator."))

    def _efn_plan(self):
        plan = self.company_id.sudo().efn_outlet_analytic_plan_id or self.env.ref(PLAN_XMLID, raise_if_not_found=False)
        if not plan:
            raise UserError(_("Plan analitik Outlet tidak ditemukan (data modul efn_account_localization)."))
        return plan.sudo()

    def _efn_warehouses(self):
        company = self.company_id
        Warehouse = self.env["stock.warehouse"].sudo()
        if self.include_all_warehouses:
            return Warehouse.search([("company_id", "=", company.id)], order="id")
        configs = self.env["pos.config"].sudo().search([("company_id", "=", company.id)])
        return configs.warehouse_id.filtered(lambda w: w.company_id == company).sorted("id")

    def _efn_candidate(self, warehouse, plan):
        Account = self.env["account.analytic.account"].sudo()
        if not warehouse.code:
            return Account
        domain = [
            ("plan_id", "=", plan.id),
            ("company_id", "=", warehouse.company_id.id),
            ("code", "=", warehouse.code),
        ]
        return Account.search(domain, limit=1)

    def _efn_preview(self, warehouse):
        plan = self._efn_plan()
        account = warehouse.efn_analytic_account_id
        if account:
            return self._line(warehouse, account, "ok", _("Sudah tertaut."))
        candidate = self._efn_candidate(warehouse, plan)
        if candidate:
            return self._line(warehouse, candidate, "link", _("Akun analitik dengan kode gudang sudah ada."))
        return self._line(warehouse, False, "create", _("Akun analitik baru akan dibuat."))

    def _efn_apply_one(self, warehouse, plan):
        if warehouse.efn_analytic_account_id:
            return self._line(warehouse, warehouse.efn_analytic_account_id, "ok", _("Sudah tertaut."))
        account = self._efn_candidate(warehouse, plan)
        message = _("Akun analitik yang ada ditautkan.")
        if not account:
            account = (
                self.env["account.analytic.account"]
                .sudo()
                .create(
                    {
                        "name": warehouse.name,
                        "code": warehouse.code,
                        "plan_id": plan.id,
                        "company_id": warehouse.company_id.id,
                    }
                )
            )
            message = _("Akun analitik dibuat dan ditautkan.")
        warehouse.efn_analytic_account_id = account
        _logger.info("EFN G60: gudang %s -> akun analitik Outlet %s", warehouse.id, account.id)
        return self._line(warehouse, account, "fixed", message)

    def _line(self, warehouse, account, status, message):
        return {
            "warehouse_id": warehouse.id,
            "analytic_account_id": account.id if account else False,
            "status": status,
            "message": message,
        }

    def _efn_write_lines(self, lines):
        self.line_ids.unlink()
        self.env["efn.outlet.analytic.wizard.line"].create([{**line, "wizard_id": self.id} for line in lines])
        counts = {status: sum(1 for line in lines if line["status"] == status) for status, _label in STATUS_SELECTION}
        self.summary = _(
            "%(total)s gudang: %(ok)s ok, %(fixed)s diperbaiki, %(todo)s perlu apply, %(blocked)s gagal.",
            total=len(lines),
            ok=counts["ok"],
            fixed=counts["fixed"],
            todo=counts["link"] + counts["create"],
            blocked=counts["blocked"],
        )

    def _efn_reopen(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Siapkan Analitik Outlet"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class EfnOutletAnalyticWizardLine(models.TransientModel):
    _name = "efn.outlet.analytic.wizard.line"
    _description = "EFN: hasil wizard analitik Outlet (G60)"
    _order = "id"
    _check_company_auto = True

    wizard_id = fields.Many2one("efn.outlet.analytic.wizard", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="wizard_id.company_id", store=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Gudang", check_company=True, ondelete="cascade")
    analytic_account_id = fields.Many2one(
        "account.analytic.account", string="Akun analitik", check_company=True, ondelete="cascade"
    )
    status = fields.Selection(STATUS_SELECTION, required=True)
    message = fields.Char(string="Pesan")
