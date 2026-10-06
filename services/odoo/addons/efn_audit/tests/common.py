from odoo import Command
from odoo.tests.common import TransactionCase


class EfnAuditCase(TransactionCase):
    """Helper: aturan uji ditulis/dibuat dalam transaksi test (rollback otomatis). Bila
    efn_setup_audit sudah memasang aturan untuk model yang sama, aturan itu yang diubah."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rule = cls.env["efn.audit.rule"].sudo().with_context(active_test=False)
        cls.Log = cls.env["efn.audit.log"].sudo()

    def setUp(self):
        super().setUp()
        # Cache ormcache aturan tidak boleh bocor ke test lain setelah rollback.
        self.addCleanup(self.env.registry.clear_cache)

    def _fields(self, model, *names):
        return [self.env["ir.model.fields"]._get(model, name).id for name in names]

    def _set_rule(self, model, tracked=(), masked=(), excluded=(), **vals):
        values = {
            "name": f"Uji {model}",
            "active": True,
            "log_create": True,
            "log_write": True,
            "log_unlink": True,
            "capture_unlink_values": False,
            "filter_domain": False,
            "retention_days": 0,
            "tracked_field_ids": [Command.set(self._fields(model, *tracked))],
            "masked_field_ids": [Command.set(self._fields(model, *masked))],
            "excluded_field_ids": [Command.set(self._fields(model, *excluded))],
        }
        values.update(vals)
        rule = self.Rule.search([("model_name", "=", model)], limit=1)
        if rule:
            rule.write(values)
        else:
            rule = self.Rule.create(dict(values, model_id=self.env["ir.model"]._get_id(model)))
        return rule

    def _logs(self, record, operation=None):
        domain = [("model_name", "=", record._name), ("res_id", "in", record.ids)]
        if operation:
            domain.append(("operation", "=", operation))
        return self.Log.search(domain)

    def _line(self, log, fname):
        return log.line_ids.filtered(lambda line: line.field_name == fname)
