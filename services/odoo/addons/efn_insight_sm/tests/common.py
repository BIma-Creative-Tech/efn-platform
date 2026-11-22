"""Fixture bersama test efn_insight_sm: PT SMD uji (DC + 2 outlet), PT F&B uji, kategori, user per persona."""

from odoo import Command
from odoo.tests import new_test_user

from odoo.addons.efn_insight.models.res_users import PERSONA_GROUPS

# G52 (docs/04 §28.15.2): grup persona milik efn_insight; kpi_admin tetap di modul ini.
PERSONA = {**PERSONA_GROUPS, "kpi_admin": "efn_insight_sm.group_insight_kpi_admin"}


def widget_roundtrip(user):
    """Emulasi simpan widget hak akses Odoo 19 (`res_user_group_ids_field.js`, diverifikasi 21 Nov 2026): satu nilai
    per privilege (`findLast` grup terpilih), grup tanpa privilege = checkbox, lalu `Command.set` daftar itu."""
    selected = set(user.group_ids.ids)
    keep = []
    for privilege in user.env["res.groups.privilege"].sudo().search([]):
        chosen = [gid for gid in privilege.group_ids.ids if gid in selected]
        if chosen:
            keep.append(chosen[-1])
    keep += [g.id for g in user.group_ids if not g.privilege_id]
    user.write({"group_ids": [Command.set(keep)]})


def setup_sm_fixture(cls):
    env = cls.env
    Company = env["res.company"]
    # Kode company unik per DB (efn_setup_company/efn_setup_supermarket bisa sudah memakainya): kosongkan dulu
    # (rollback akhir test). active_test=False: company arsip juga memegang kode unik.
    Company.with_context(active_test=False).search([("efn_brand_code", "!=", False)]).write({"efn_brand_code": False})
    Company.flush_model(["efn_brand_code"])
    cls.smd = Company.create({"name": "PT Uji Insight SMD", "efn_brand_code": "SMD"})
    cls.pb = Company.create({"name": "PT Uji Insight SM PB", "efn_brand_code": "PB"})
    cls.gl = Company.create({"name": "PT Uji Insight SM GL", "efn_brand_code": "GL"})
    Warehouse = env["stock.warehouse"]
    cls.dc = Warehouse.create({"name": "DC uji ins", "code": "IDC1", "company_id": cls.smd.id, "efn_is_dc": True})
    cls.outlet1 = Warehouse.create({"name": "Outlet uji ins 1", "code": "IOT1", "company_id": cls.smd.id})
    cls.outlet2 = Warehouse.create({"name": "Outlet uji ins 2", "code": "IOT2", "company_id": cls.smd.id})
    cls.pb_wh = Warehouse.create({"name": "Outlet uji ins PB", "code": "IPB1", "company_id": cls.pb.id})
    # Semua gudang aktif PT SMD (termasuk gudang bawaan yang dibuat stock saat company dibuat).
    cls.smd_wh_ids = sorted(Warehouse.search([("company_id", "=", cls.smd.id)]).ids)
    Categ = env["product.category"]
    cls.categ_a = Categ.create({"name": "Uji Insight SM A"})
    cls.categ_b = Categ.create({"name": "Uji Insight SM B"})

    def user(login, personas=(), companies=None, extra=()):
        groups = ["base.group_user", *(PERSONA[p] for p in personas), *extra]
        companies = companies or cls.smd
        return new_test_user(
            env,
            login=login,
            name=f"Uji {login}",
            groups=",".join(groups),
            company_id=companies[0].id,
            company_ids=[Command.set(companies.ids)],
        )

    cls.make_user = staticmethod(user)
