"""G47 (fit-gap docs/fit-gap/G47_INSIGHT_SM.md §3.2, §8): field cakupan data res.users teraudit sebagai model kontrol;
privilege Manager terpisah sehingga form user Odoo 19 tidak menghapus grup kedua."""

from itertools import combinations

from odoo import Command
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_audit.models.audit_policy import CONTROL_SPECS

from .common import EfnAuditCase


def widget_roundtrip(user):
    """Emulasi penyimpanan widget `res_user_group_ids_field` Odoo 19 (web/static/src/webclient/
    res_user_group_ids_field/res_user_group_ids_field.js, diverifikasi 21 Nov 2026): satu nilai per privilege
    (`findLast` grup terpilih), grup tanpa privilege = checkbox, lalu `Command.set` daftar itu."""
    selected = set(user.group_ids.ids)
    keep = []
    for privilege in user.env["res.groups.privilege"].sudo().search([]):
        chosen = [gid for gid in privilege.group_ids.ids if gid in selected]
        if chosen:
            keep.append(chosen[-1])
    keep += [g.id for g in user.group_ids if not g.privilege_id]
    user.write({"group_ids": [Command.set(keep)]})


def independent_pairs(privilege):
    """Pasangan grup dalam satu privilege yang tidak saling implied (tidak bisa dipegang bersamaan lewat form)."""
    groups = privilege.group_ids
    return [(a, b) for a, b in combinations(groups, 2) if a not in b.all_implied_ids and b not in a.all_implied_ids]


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditG47(EfnAuditCase):
    def test_control_spec_lists_scope_fields(self):
        fnames = CONTROL_SPECS["res.users"][0]
        self.assertIn("efn_outlet_warehouse_ids", fnames)
        self.assertIn("efn_insight_categ_ids", fnames)
        tracked = self.Rule._efn_get_rule("res.users").tracked
        users = self.env["res.users"]
        for fname in ("efn_outlet_warehouse_ids", "efn_insight_categ_ids"):
            # Field modul yang belum terpasang dilewati (tanpa galat).
            self.assertEqual(fname in tracked, fname in users._fields, fname)

    def _assert_scope_field_logged(self, fname, comodel_record):
        if fname not in self.env["res.users"]._fields:
            self.skipTest(f"{fname} tidak terpasang")
        user = new_test_user(self.env, login=f"efn_audit_g47_{fname}", groups="base.group_user")
        user.write({fname: [Command.link(comodel_record.id)]})
        line = self._logs(user, "write").line_ids.filtered(lambda r: r.field_name == fname)
        self.assertEqual(len(line), 1)
        log = line.log_id
        self.assertTrue(line.new_value.startswith("ditambah: "))
        self.assertIn(f",{comodel_record.id}", line.new_ref)
        self.assertEqual(log.res_name, f"res.users,{user.id}", "tanpa nama user (PERSONAL_MODELS)")
        user.write({fname: [Command.unlink(comodel_record.id)]})
        removed = self._line(self._logs(user, "write").sorted("id")[-1], fname)
        self.assertTrue(removed.old_value.startswith("dihapus: "))

    def test_outlet_warehouse_change_logged(self):
        if "stock.warehouse" not in self.env:
            self.skipTest("stock tidak terpasang")
        warehouse = self.env["stock.warehouse"].search([], limit=1)
        if not warehouse:
            self.skipTest("tanpa gudang")
        self._assert_scope_field_logged("efn_outlet_warehouse_ids", warehouse)

    def test_insight_categ_change_logged(self):
        if "product.category" not in self.env:
            self.skipTest("product tidak terpasang")
        categ = self.env["product.category"].create({"name": "Kategori audit G47"})
        self._assert_scope_field_logged("efn_insight_categ_ids", categ)

    # ------------------------------------------------------------------ privilege (widget Odoo 19)
    def test_manager_privilege_split(self):
        manager = self.env.ref("efn_audit.group_manager")
        personal = self.env.ref("efn_audit.group_auditor_personal")
        auditor = self.env.ref("efn_audit.group_auditor")
        self.assertNotEqual(manager.privilege_id, personal.privilege_id)
        self.assertEqual(personal.privilege_id, auditor.privilege_id)
        for privilege in (manager.privilege_id, personal.privilege_id):
            self.assertFalse(independent_pairs(privilege), privilege.name)

    def test_manager_and_personal_survive_form_save(self):
        user = new_test_user(
            self.env,
            login="efn_audit_g47_two",
            groups="base.group_user,efn_audit.group_manager,efn_audit.group_auditor_personal",
        )
        manager = self.env.ref("efn_audit.group_manager")
        personal = self.env.ref("efn_audit.group_auditor_personal")
        widget_roundtrip(user)
        self.assertIn(manager, user.group_ids)
        self.assertIn(personal, user.group_ids)
        self.assertTrue(user.has_group("efn_audit.group_auditor"))
