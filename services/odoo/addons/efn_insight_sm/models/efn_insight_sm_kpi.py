"""`efn.insight.sm.kpi`: definisi & ambang KPI Insight supermarket per PT (G47, docs/04 §28.13.8c, L-200/L-201).

Satu baris = (company, kunci) dengan nilai string yang divalidasi registri tertutup `kpi_registry.KPI_REGISTRY`.
Baris tidak ada / nonaktif = default registri. ETL `sm_dims` membaca kolom `id, company_id, key, value, active,
write_date` (grant per kolom, docs/04 §28.13.8). Hanya `group_insight_kpi_admin` yang boleh mengubah (ACL); setiap
perubahan teraudit `efn_audit` (aturan `audit_rule_efn_insight_sm_kpi`). Keputusan pemilik proyek 21 Nov 2026
(Q-G47-1): tanpa pembatasan SoD di kode.
"""

from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.efn_insight.lib.insight_token import SM_CODES

from . import kpi_registry as reg

_ERRORS = {
    "unknown_key": "kunci KPI tidak dikenal",
    "type": "format nilai tidak sesuai tipe kunci",
    "range": "nilai di luar rentang yang diizinkan",
    "choice": "nilai bukan salah satu pilihan yang diizinkan",
}


def _raise(code, key, raw=None):
    detail = _ERRORS.get(code, code)
    allowed = reg.allowed_text(key) if key in reg.KPI_REGISTRY else "-"
    raise ValidationError(
        _(
            "efn_insight_sm:kpi_%(code)s — %(key)s: %(detail)s (nilai %(raw)r; diizinkan: %(allowed)s).",
            code=code,
            key=key,
            detail=detail,
            raw=raw,
            allowed=allowed,
        )
    )


class EfnInsightSmKpi(models.Model):
    _name = "efn.insight.sm.kpi"
    _description = "EFN Insight Supermarket: definisi & ambang KPI per PT"
    _order = "company_id, key"
    _rec_name = "key"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", string="PT", required=True, index=True, default=lambda self: self.env.company
    )
    key = fields.Selection(selection="_selection_key", string="Kunci", required=True)
    value = fields.Char(string="Nilai", required=True)
    note = fields.Char(string="Catatan", help="Maks 128 karakter. Alasan perubahan (tanpa data pribadi).")
    active = fields.Boolean(default=True)
    # Bantuan UI (tidak tersimpan, tidak masuk grant ETL).
    value_type = fields.Char(string="Tipe", compute="_compute_registry_info")
    default_value = fields.Char(string="Default", compute="_compute_registry_info")
    allowed_values = fields.Char(string="Diizinkan", compute="_compute_registry_info")
    is_default = fields.Boolean(string="Sama dengan default", compute="_compute_registry_info")

    _efn_kpi_company_key_uniq = models.Constraint(
        "UNIQUE(company_id, key)",
        "efn_insight_sm:kpi_duplicate — satu baris per PT per kunci (arsipkan/aktifkan kembali, jangan buat baru).",
    )

    @api.model
    def _selection_key(self):
        return [(key, f"{key} — {spec.label}") for key, spec in reg.KPI_REGISTRY.items()]

    @api.depends("key", "value", "active")
    def _compute_registry_info(self):
        for rec in self:
            spec = reg.KPI_REGISTRY.get(rec.key)
            if not spec:
                rec.value_type = rec.default_value = rec.allowed_values = False
                rec.is_default = False
                continue
            default = reg.default_literal(rec.key)
            rec.value_type = spec.type
            rec.default_value = default
            rec.allowed_values = reg.allowed_text(rec.key)
            # Semantik C-1 (fit-gap G47 §4.3): nonaktif ATAU nilai = literal default.
            rec.is_default = (not rec.active) or rec.value == default

    # ------------------------------------------------------------------ normalisasi & proteksi
    @api.model
    def _efn_canonical(self, key, raw):
        try:
            return reg.parse(key, raw)[1]
        except ValueError as exc:
            _raise(str(exc), key, raw)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("key") and isinstance(vals.get("value"), str):
                vals["value"] = self._efn_canonical(vals["key"], vals["value"])
        return super().create(vals_list)

    def write(self, vals):
        if "key" in vals or "company_id" in vals:
            for rec in self:
                if ("key" in vals and vals["key"] != rec.key) or (
                    "company_id" in vals and vals["company_id"] != rec.company_id.id
                ):
                    raise UserError(
                        _(
                            "efn_insight_sm:kpi_readonly — kunci dan PT tidak bisa diubah setelah dibuat. Arsipkan "
                            "baris ini dan pakai baris kunci yang benar."
                        )
                    )
        if isinstance(vals.get("value"), str) and self:
            by_value = defaultdict(lambda: self.browse())
            for rec in self:
                by_value[self._efn_canonical(rec.key, vals["value"])] |= rec
            if len(by_value) > 1:
                for canonical, recs in by_value.items():
                    super(EfnInsightSmKpi, recs).write(dict(vals, value=canonical))
                return True
            vals = dict(vals, value=next(iter(by_value)))
        return super().write(vals)

    def unlink(self):
        companies = self.company_id
        res = super().unlink()
        # Baris dihapus = kembali ke default -> pasangan ambang tetap wajib konsisten.
        self._efn_check_pairs_for(companies)
        return res

    # ------------------------------------------------------------------ validasi
    @api.constrains("key", "value")
    def _check_efn_value(self):
        for rec in self:
            if rec.key not in reg.KPI_REGISTRY:
                _raise("unknown_key", rec.key or "?", rec.value)
            try:
                reg.parse(rec.key, rec.value)
            except ValueError as exc:
                _raise(str(exc), rec.key, rec.value)

    @api.constrains("company_id")
    def _check_efn_company(self):
        # Hanya PT supermarket (SM_CODES) yang dimuat ETL; baris untuk PT lain tidak bermakna.
        for rec in self:
            if rec.company_id.sudo().efn_brand_code not in SM_CODES:
                raise ValidationError(
                    _(
                        "efn_insight_sm:kpi_company — definisi KPI Insight supermarket hanya untuk PT berkode "
                        "%(codes)s.",
                        codes=", ".join(sorted(SM_CODES)),
                    )
                )

    @api.constrains("note")
    def _check_efn_note(self):
        for rec in self:
            if rec.note and len(rec.note) > reg.NOTE_MAX:
                raise ValidationError(_("efn_insight_sm:kpi_note — catatan maksimal %(n)s karakter.", n=reg.NOTE_MAX))

    @api.constrains("key", "value", "active", "company_id")
    def _check_efn_pairs(self):
        self._efn_check_pairs_for(self.company_id)

    @api.model
    def _efn_effective_raw(self, company):
        """{kunci: nilai string} baris aktif PT (sudo: validasi lintas baris tidak boleh bergantung record rule)."""
        rows = self.sudo().with_context(active_test=True).search([("company_id", "=", company.id)])
        return {row.key: row.value for row in rows}

    @api.model
    def _efn_check_pairs_for(self, companies):
        for company in companies:
            violations = reg.pair_violations(self._efn_effective_raw(company))
            if violations:
                lower, upper, low, up = violations[0]
                raise ValidationError(
                    _(
                        "efn_insight_sm:kpi_pair — %(company)s: %(lower)s (%(low)s) harus lebih kecil dari %(upper)s "
                        "(%(up)s). Ubah nilai pasangan dengan urutan yang menjaga lower < upper.",
                        company=company.display_name,
                        lower=lower,
                        low=reg.format_number(low),
                        upper=upper,
                        up=reg.format_number(up),
                    )
                )

    # ------------------------------------------------------------------ seed
    @api.model
    def _efn_seed_defaults(self, companies=None):
        """Buat baris default (39 kunci) untuk company `efn_brand_code ∈ SM_CODES` yang belum punya. Idempoten.

        Dipanggil `post_init_hook` dan `res.company` create/write (kode SM ditetapkan). Baris yang sudah ada (aktif,
        arsip, atau diubah) tidak disentuh; baris tidak ada tetap = default (docs/04 §28.13.8c).
        """
        Company = self.env["res.company"].sudo()
        if companies is None:
            companies = Company.search([("efn_brand_code", "in", sorted(SM_CODES))])
        companies = companies.filtered(lambda c: c.efn_brand_code in SM_CODES)
        model = self.sudo().with_context(active_test=False)
        vals_list = []
        for company in companies:
            existing = set(model.search([("company_id", "=", company.id)]).mapped("key"))
            vals_list += [
                {"company_id": company.id, "key": key, "value": reg.default_literal(key)}
                for key in reg.KPI_REGISTRY
                if key not in existing
            ]
        return model.create(vals_list) if vals_list else model.browse()
