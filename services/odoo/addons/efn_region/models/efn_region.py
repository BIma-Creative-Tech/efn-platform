"""Master wilayah kab/kota berkode Kemendagri (ADR-036 §0.7, K-36-11, Q-PII10).

- `code` = format Kemendagri `^\\d{2}\\.\\d{2}$` (provinsi.kabkota, mis. `31.71`), unik global.
- Data referensi bersama semua PT: `company_id` kosong (default). Record rule multi-company tetap ada agar wilayah
  khusus satu PT (bila suatu saat dibuat) tidak terlihat PT lain.
- Kolom yang dibaca ETL (view `efn_etl.region` milik skrip grant data-ai, BUKAN dibuat modul ini):
  `id, code, name, state_id, active, create_date, write_date`.
- Importer: `env['efn.region'].sudo()._efn_region_import_csv(teks_csv, source="Kepmendagri ...")` (shell Superadmin)
  atau impor CSV standar Odoo (Settings > Technical, admin). Tidak ada metode publik baru (gerbang SEC-58).
"""

import csv
import io
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

CODE_RE = re.compile(r"^\d{2}\.\d{2}$")
KINDS = [("kabupaten", "Kabupaten"), ("kota", "Kota")]
MAX_NAME = 128


class EfnRegion(models.Model):
    _name = "efn.region"
    _description = "EFN Wilayah Kab/Kota (Kemendagri)"
    _order = "code"
    _rec_names_search = ["code", "name"]
    _check_company_auto = True

    code = fields.Char(string="Kode Kemendagri", required=True, index=True, help="Format `PP.KK`, mis. 31.71.")
    name = fields.Char(string="Nama Kab/Kota", required=True)
    kind = fields.Selection(KINDS, string="Jenis")
    province_code = fields.Char(string="Kode Provinsi", compute="_compute_province_code", store=True, index=True)
    state_id = fields.Many2one(
        "res.country.state",
        string="Provinsi",
        domain="[('country_id.code', '=', 'ID')]",
        ondelete="restrict",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Perusahaan",
        index=True,
        help="Kosong = data referensi untuk semua PT (normal).",
    )
    source = fields.Char(string="Sumber Data", help="Sumber & versi data (Q-PII10), mis. Kepmendagri yang dipakai.")

    _code_unique = models.Constraint("unique(code)", "Kode wilayah Kemendagri harus unik.")

    @api.depends("code")
    def _compute_province_code(self):
        for region in self:
            region.province_code = region.code[:2] if region.code and CODE_RE.match(region.code) else False

    @api.depends("code", "name")
    def _compute_display_name(self):
        for region in self:
            region.display_name = f"{region.code} {region.name}" if region.code else region.name

    @api.constrains("code", "name")
    def _check_code(self):
        for region in self:
            if not CODE_RE.match(region.code or ""):
                raise ValidationError(_("Kode wilayah harus berformat Kemendagri PP.KK (mis. 31.71)."))
            if len(region.name or "") > MAX_NAME:
                raise ValidationError(_("Nama wilayah terlalu panjang."))

    @api.model
    def _efn_region_import_csv(self, content, source=None):
        """Upsert dari CSV ber-header `code,name[,kind]` (UTF-8). Baris tidak valid dilewati (dihitung).

        :returns: dict jumlah {"created", "updated", "skipped"} (tanpa isi baris).
        """
        if not (self.env.is_superuser() or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Hanya administrator sistem yang dapat mengimpor wilayah."))
        if isinstance(content, bytes):
            content = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content or ""))
        if not reader.fieldnames or not {"code", "name"} <= {name.strip() for name in reader.fieldnames}:
            raise ValidationError(_("CSV wilayah wajib memiliki kolom code dan name."))
        Region = self.sudo().with_context(active_test=False)
        existing = {region.code: region for region in Region.search([])}
        counts = {"created": 0, "updated": 0, "skipped": 0}
        kinds = dict(KINDS)
        to_create = []
        for row in reader:
            row = {(key or "").strip(): (value or "").strip() for key, value in row.items()}
            code, name, kind = row.get("code", ""), row.get("name", ""), row.get("kind", "").lower() or False
            if not CODE_RE.match(code) or not name or len(name) > MAX_NAME or (kind and kind not in kinds):
                counts["skipped"] += 1
                continue
            vals = {"name": name, "kind": kind, "active": True}
            if source:
                vals["source"] = source
            region = existing.get(code)
            if region:
                region.write(vals)
                counts["updated"] += 1
            else:
                to_create.append({"code": code, **vals})
                existing[code] = True
                counts["created"] += 1
        if to_create:
            Region.create(to_create)
        return counts
