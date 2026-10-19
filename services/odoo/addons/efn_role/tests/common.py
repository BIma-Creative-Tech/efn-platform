"""Fixture efn_role (G21, E-47/E-47c): company uji, grup uji tanpa xmlid, user per peran. Tidak ada rahasia/PII."""

from odoo.fields import Command
from odoo.tests import TransactionCase, new_test_user

from odoo.addons.efn_role.models.role_policy import efn_today

REASON = "Masa transisi penutupan stok akhir bulan (uji otomatis)"


class EfnRoleCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Role = env["efn.role"]
        cls.Line = env["efn.role.line"]
        cls.Rule = env["efn.sod.rule"]
        cls.Exc = env["efn.sod.exception"]
        cls.Review = env["efn.access.review"]
        cls.Groups = env["res.groups"]
        cls.today = efn_today(env)
        cls.main = env.company
        cls.gl = env["res.company"].create({"name": "PT Uji Peran GL"})
        cls.pb = env["res.company"].create({"name": "PT Uji Peran PB"})
        cls.group_user = env.ref("base.group_user")
        cls.g_role1 = cls.Groups.create({"name": "EFN Uji Peran 1"})
        cls.g_role2 = cls.Groups.create({"name": "EFN Uji Peran 2"})
        cls.g_other = cls.Groups.create({"name": "EFN Uji Non-Peran"})
        cls.g_a = cls.Groups.create({"name": "EFN Uji SoD A"})
        cls.g_b = cls.Groups.create({"name": "EFN Uji SoD B"})
        all_companies = cls.main | cls.gl | cls.pb
        cls.superadmin = cls._make_user("efn_role_sa1", ["base.group_system"], all_companies)
        cls.superadmin2 = cls._make_user("efn_role_sa2", ["base.group_system"], all_companies)
        cls.access_admin = cls._make_user("efn_role_access", ["base.group_erp_manager"], all_companies)
        cls.auditor = cls._make_user(
            "efn_role_auditor", ["efn_audit.group_auditor", "base.group_allow_export"], cls.main | cls.gl
        )
        cls.rule_ab = cls.Rule.create(
            {"code": "UJI-AB", "name": "Uji A ⟂ B", "group_a_id": cls.g_a.id, "group_b_id": cls.g_b.id}
        )

    @classmethod
    def _make_user(cls, login, xmlids=(), companies=None, extra=None):
        companies = companies or cls.main
        user = new_test_user(
            cls.env,
            login=login,
            groups=",".join(["base.group_user", *xmlids]),
            company_id=companies[0].id,
            company_ids=[Command.set(companies.ids)],
        )
        if extra:
            user.write({"group_ids": [Command.link(group.id) for group in extra]})
        return user

    def _role(self, name, groups, company=None, **vals):
        values = {"name": name, "company_id": (company or self.gl).id, "group_ids": [Command.set(groups.ids)]}
        values.update(vals)
        return self.Role.create(values)

    def _assign(self, role, user, **vals):
        return self.Line.create(dict({"role_id": role.id, "user_id": user.id}, **vals))
