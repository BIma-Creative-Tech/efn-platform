"""Bootstrap beranda EFN (port dari core/custom_home_console/models/res_users.py, ADR-049).

Perubahan dari sumber:
- Daftar aplikasi dihitung di SERVER dari ``ir.ui.menu.load_menus`` milik user pemanggil (visibilitas menu CE: grup,
  akses model action) lalu dikelompokkan secara deterministik per xmlid menu root (bukan heuristik nama kategori).
- Branding diambil dari ``env.company`` (PT aktif di switcher), bukan ``user.company_id``; tidak ada data PT lain.
- Checklist onboarding sumber dibuang (mencari produk lintas PT dengan sudo, tidak ditampilkan template).
"""

import re

from odoo import api, fields, models

from .res_company import DEFAULT_ACCENT

HOME_MENU_XMLID = "efn_web_home.menu_home_root"

# (kunci, label) dalam urutan tampil.
HOME_GROUPS = [
    ("ops", "Operasional"),
    ("finance", "Finance"),
    ("accounting", "Accounting"),
    ("insight", "Insight & Data"),
    ("admin", "Admin"),
    ("other", "Lainnya"),
]

# Pemetaan deterministik xmlid menu root -> kelompok. Menu yang modulnya belum terpasang diabaikan (env.ref aman).
ROOT_MENU_GROUPS = {
    "point_of_sale.menu_point_root": "ops",
    "stock.menu_stock_root": "ops",
    "mrp.menu_mrp_root": "ops",
    "purchase.menu_purchase_root": "ops",
    "efn_agri.menu_efn_agri_root": "ops",
    "account.menu_finance": "finance",
    "efn_online_transfer.menu_efn_online_transfer_root": "finance",
    "efn_setup_menu.menu_accounting_root": "accounting",
    "efn_insight.menu_insight": "insight",
    "efn_data_ask.menu_efn_data_ask_root": "insight",
    "efn_loyalty_accounting.menu_efn_loyalty_root": "insight",
    "efn_campaign.menu_efn_campaign_root": "insight",
    "efn_promo.menu_efn_promo_root": "insight",
    "base.menu_administration": "admin",
    "base.menu_management": "admin",
    "efn_audit.menu_efn_audit_root": "admin",
}

# Modul dapur/KDS: menu root apa pun yang mereka tambahkan kelak masuk Operasional.
MODULE_GROUPS = {
    "efn_pos_kds": "ops",
    "efn_ck_production": "ops",
    "efn_kitting": "ops",
    "efn_online_kds_bridge": "ops",
}

# Fallback: kata kunci pada nama kategori modul (bahasa Inggris) pemilik xmlid menu root.
CATEGORY_KEYWORDS = [
    ("accounting", "finance"),
    ("invoic", "finance"),
    ("point of sale", "ops"),
    ("inventory", "ops"),
    ("manufactur", "ops"),
    ("purchase", "ops"),
    ("supply chain", "ops"),
    ("sales", "ops"),
    ("administration", "admin"),
    ("technical", "admin"),
]

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ResUsers(models.Model):
    _inherit = "res.users"

    efn_home_pinned_action_ids = fields.Many2many(
        comodel_name="ir.actions.actions",
        relation="res_users_efn_home_pinned_action_rel",
        column1="user_id",
        column2="action_id",
        string="Pintasan Beranda",
        help="Action yang dipasang di beranda EFN user ini (di atas kartu aplikasi).",
    )
    efn_home_density = fields.Selection(
        selection=[("comfort", "Lega"), ("compact", "Ringkas")],
        default="comfort",
        string="Kerapatan Beranda",
    )

    @api.model
    def efn_home_bootstrap(self):
        """Satu payload RPC untuk mount beranda: user, branding PT aktif, aplikasi terlihat, pintasan, aktivitas.

        Hanya membaca data user pemanggil dan ``env.company`` dengan hak user itu sendiri (tanpa sudo, tanpa argumen
        dari klien; sudo hanya untuk nama pintasan milik user ini) -> tidak ada data/branding PT lain
        (RPC_ALLOWLIST efn_core).
        """
        user = self.env.user
        company = self.env.company
        accent = company.efn_brand_accent_color or ""
        if not HEX_COLOR_RE.match(accent):
            accent = DEFAULT_ACCENT

        # sudo hanya untuk membaca id/nama/tipe action yang dipasang admin di record user INI (ACL ir.actions.actions
        # bisa terbatas); membuka action tetap lewat /web/action/load dengan hak user.
        pinned = [{"id": act.id, "name": act.name, "type": act.type} for act in user.sudo().efn_home_pinned_action_ids]

        activities = []
        # mail.activity._search memfilter aktivitas yang dokumennya tidak terbaca di PT aktif (record rule).
        for act in self.env["mail.activity"].search(
            [("user_id", "=", user.id)], order="date_deadline asc, id desc", limit=5
        ):
            activities.append(
                {
                    "id": act.id,
                    "summary": act.summary or act.activity_type_id.name or "",
                    "res_model": act.res_model,
                    "res_id": act.res_id,
                    "date_deadline": act.date_deadline.isoformat() if act.date_deadline else None,
                }
            )

        return {
            "user": {"id": user.id, "name": user.name, "density": user.efn_home_density or "comfort"},
            "company": {
                "id": company.id,
                "name": company.name,
                "accent": accent,
                "has_home_logo": bool(company.efn_brand_logo_home),
                "has_logo": bool(company.logo),
                "announcement_html": str(company.efn_home_announcement_html or "")
                if company.efn_home_announcement_active
                else "",
            },
            "groups": [{"key": key, "label": label} for key, label in HOME_GROUPS],
            "apps": self._efn_home_apps(),
            "pinned": pinned,
            "recent_activities": activities,
        }

    @api.model
    def _efn_home_apps(self):
        """[{id, name, xmlid, group}] menu root yang terlihat oleh user pemanggil, urut sequence menu."""
        menus = self.env["ir.ui.menu"].load_menus(False)
        root = menus.get("root") or {}
        home = self.env.ref(HOME_MENU_XMLID, raise_if_not_found=False)
        root_map = {}
        for xmlid, group in ROOT_MENU_GROUPS.items():
            rec = self.env.ref(xmlid, raise_if_not_found=False)
            if rec and rec._name == "ir.ui.menu":
                root_map[rec.id] = group

        entries = []
        for menu_id in root.get("children") or []:
            menu = menus.get(menu_id) or menus.get(str(menu_id))
            if not menu or (home and menu["id"] == home.id):
                continue
            entries.append(menu)

        modules = {(m.get("xmlid") or "").split(".")[0] for m in entries if m["id"] not in root_map and m.get("xmlid")}
        categories = self._efn_home_module_categories(modules - set(MODULE_GROUPS))

        apps = []
        for menu in entries:
            module = (menu.get("xmlid") or "").split(".")[0]
            group = root_map.get(menu["id"]) or MODULE_GROUPS.get(module)
            if not group:
                text = categories.get(module, "")
                group = next((grp for needle, grp in CATEGORY_KEYWORDS if needle in text), "other")
            apps.append({"id": menu["id"], "name": menu["name"], "xmlid": menu.get("xmlid") or "", "group": group})
        return apps

    @api.model
    def _efn_home_module_categories(self, modules):
        """{nama modul: 'induk kategori + kategori' huruf kecil, en_US}. Metadata modul terpasang, bukan data PT."""
        if not modules:
            return {}
        result = {}
        records = (
            self.env["ir.module.module"]
            .sudo()
            .with_context(lang="en_US")
            .search([("name", "in", sorted(modules)), ("state", "=", "installed")])
        )
        for mod in records:
            cat = mod.category_id
            result[mod.name] = " ".join(filter(None, [cat.parent_id.name, cat.name])).lower()
        return result
