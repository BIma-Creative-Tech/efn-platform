"""Aturan audit per model (docs/08 §5.1, F-17; ADR-031).

Nama field = kontrak `efn_setup_audit/data/efn_audit_rule_data.xml`.

Aturan dibaca hook `base` (models/base.py) lewat `_efn_get_rule`, yang di-cache `ormcache`
sebagai **data polos** (namedtuple berisi frozenset nama field), bukan recordset — sehingga
overhead write pada model tanpa aturan hanya satu lookup cache. Cache dibersihkan setiap
create/write/unlink aturan (`registry.clear_cache()`, disinyalkan ke worker lain oleh registry),
jadi aturan baru langsung aktif tanpa restart.
"""

from collections import namedtuple

from odoo import _, api, fields, models, tools
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

# Denylist global (tidak pernah dicatat walau dipilih eksplisit di aturan).
DENY_SUBSTRINGS = ("password", "secret", "token", "api_key", "totp", "signature")
DENY_FIELDS = frozenset({("res.partner", "efn_member_hash")})
# Field teknis yang tidak ikut "semua field" (tracked kosong).
MAGIC_FIELDS = frozenset({"id", "display_name", "create_uid", "create_date", "write_uid", "write_date"})
NON_ELIGIBLE_TYPES = frozenset({"one2many", "binary"})
NON_ELIGIBLE_PREFIXES = ("message_", "activity_")
NON_ELIGIBLE_NAMES = frozenset({"website_message_ids"})
# Model log sendiri tidak pernah diaudit (rekursi); model aturan SELALU diaudit (hard-coded).
SELF_RULE_MODEL = "efn.audit.rule"
LOG_MODELS = frozenset({"efn.audit.log", "efn.audit.log.line"})

RuleData = namedtuple(
    "RuleData",
    "rule_id log_create log_write log_unlink tracked masked filter_domain capture_unlink",
)


def is_denied(model_name, fname, field):
    """True bila field masuk denylist global (nama sensitif, binary, member_hash)."""
    lname = fname.lower()
    if any(part in lname for part in DENY_SUBSTRINGS):
        return True
    if field is not None and field.type == "binary":
        return True
    return (model_name, fname) in DENY_FIELDS


def is_eligible(fname, field):
    """Field yang ikut bila `tracked_field_ids` kosong (stored, bukan o2m/binary/teknis)."""
    return (
        field.store
        and field.type not in NON_ELIGIBLE_TYPES
        and fname not in MAGIC_FIELDS
        and fname not in NON_ELIGIBLE_NAMES
        and not fname.startswith(NON_ELIGIBLE_PREFIXES)
    )


def eligible_fields(model):
    return frozenset(
        fname
        for fname, field in model._fields.items()
        if is_eligible(fname, field) and not is_denied(model._name, fname, field)
    )


class EfnAuditRule(models.Model):
    _name = "efn.audit.rule"
    _description = "EFN Audit Rule"
    _order = "model_name, id"
    # Pengecualian konvensi company_id (CLAUDE.md): aturan berlaku per MODEL untuk seluruh
    # database (satu DB multi-company); log-nya sendiri punya company_id + record rule.

    name = fields.Char(required=True)
    model_id = fields.Many2one(
        "ir.model",
        string="Model",
        required=True,
        ondelete="cascade",
        domain=[("transient", "=", False)],
    )
    model_name = fields.Char(related="model_id.model", store=True, index=True, string="Nama Model")
    active = fields.Boolean(default=True)
    log_create = fields.Boolean(string="Catat Create", default=True)
    log_write = fields.Boolean(string="Catat Write", default=True)
    log_unlink = fields.Boolean(string="Catat Delete", default=True)
    tracked_field_ids = fields.Many2many(
        "ir.model.fields",
        "efn_audit_rule_tracked_field_rel",
        "rule_id",
        "field_id",
        string="Field Dilacak",
        domain="[('model_id', '=', model_id)]",
        help="Kosong = semua field yang memenuhi syarat (stored, bukan one2many/binary/teknis).",
    )
    excluded_field_ids = fields.Many2many(
        "ir.model.fields",
        "efn_audit_rule_excluded_field_rel",
        "rule_id",
        "field_id",
        string="Field Dikecualikan",
        domain="[('model_id', '=', model_id)]",
    )
    masked_field_ids = fields.Many2many(
        "ir.model.fields",
        "efn_audit_rule_masked_field_rel",
        "rule_id",
        "field_id",
        string="Field Disamarkan (PII)",
        domain="[('model_id', '=', model_id)]",
        help="Nilai disimpan ter-mask (HP 62812****890, email a***@domain); nilai lengkap tidak disimpan.",
    )
    filter_domain = fields.Char(
        string="Filter Record",
        help="Domain Python; kosong = semua record. Dievaluasi di memori (filtered_domain).",
    )
    capture_unlink_values = fields.Boolean(string="Snapshot Saat Delete")
    retention_days = fields.Integer(
        string="Retensi (hari)",
        default=0,
        help="0 = pakai parameter efn.audit.retention_days.",
    )

    _model_uniq = models.Constraint("unique(model_id)", "Satu aturan audit per model.")

    # ------------------------------------------------------------ constraints
    @api.constrains("model_id")
    def _check_model(self):
        for rule in self:
            name = rule.model_id.model
            if name.startswith("efn.audit."):
                raise ValidationError(_("Model efn.audit.* tidak bisa diberi aturan (diaudit otomatis)."))
            if name not in self.env:
                raise ValidationError(_("Model %s tidak ada di registry.", name))
            model = self.env[name]
            if model._transient or model._abstract:
                raise ValidationError(_("Model transient/abstrak tidak bisa diaudit: %s", name))

    @api.constrains("model_id", "tracked_field_ids", "excluded_field_ids", "masked_field_ids")
    def _check_fields_model(self):
        for rule in self:
            wrong = (rule.tracked_field_ids | rule.excluded_field_ids | rule.masked_field_ids).filtered(
                lambda f, r=rule: f.model_id != r.model_id
            )
            if wrong:
                raise ValidationError(_("Field aturan harus milik model %s.", rule.model_id.model))

    @api.constrains("filter_domain", "model_id")
    def _check_filter_domain(self):
        for rule in self.filtered("filter_domain"):
            try:
                domain = safe_eval(rule.filter_domain)
                if not isinstance(domain, (list, tuple)):
                    raise ValueError("bukan list")
                model = self.env[rule.model_id.model].sudo()
                # Validasi nama field/operator tanpa mengeksekusi query.
                model._search(list(domain))
            except Exception as exc:  # noqa: BLE001 - pesan ringkas untuk admin
                raise ValidationError(_("Filter domain tidak valid: %s", exc)) from exc

    @api.constrains("retention_days")
    def _check_retention(self):
        if any(rule.retention_days < 0 for rule in self):
            raise ValidationError(_("Retensi tidak boleh negatif."))

    # ------------------------------------------------------------------- CRUD
    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        self.env.registry.clear_cache()
        return rules

    def write(self, vals):
        res = super().write(vals)
        self.env.registry.clear_cache()
        return res

    def unlink(self):
        res = super().unlink()
        self.env.registry.clear_cache()
        return res

    # ------------------------------------------------------------------ cache
    @api.model
    @tools.ormcache("model_name")
    def _efn_get_rule(self, model_name):
        """Data aturan polos untuk `model_name`, atau None. Dipanggil hook `base` di setiap
        create/write/unlink — jangan kembalikan recordset (cache lintas transaksi)."""
        if model_name in LOG_MODELS or model_name not in self.env:
            return None
        model = self.env[model_name]
        if model_name == SELF_RULE_MODEL:
            # Perubahan aturan audit selalu dicatat (tidak bisa dimatikan lewat data).
            return RuleData(False, True, True, True, eligible_fields(model), frozenset(), "", True)
        rule = self.sudo().search([("model_name", "=", model_name), ("active", "=", True)], limit=1)
        if not rule:
            return None
        if rule.tracked_field_ids:
            tracked = {
                f.name
                for f in rule.tracked_field_ids
                if f.name in model._fields and not is_denied(model_name, f.name, model._fields[f.name])
            }
        else:
            tracked = set(eligible_fields(model))
        tracked -= set(rule.excluded_field_ids.mapped("name"))
        return RuleData(
            rule.id,
            rule.log_create,
            rule.log_write,
            rule.log_unlink,
            frozenset(tracked),
            frozenset(rule.masked_field_ids.mapped("name")),
            rule.filter_domain or "",
            rule.capture_unlink_values,
        )
