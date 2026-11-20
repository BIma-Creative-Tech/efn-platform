"""G47 (fit-gap docs/fit-gap/G47_INSIGHT_SM.md §8): Markdown Manager & Supervisor override di privilege terpisah.

Widget hak akses Odoo 19 (`res_user_group_ids_field`) = satu pilihan per privilege + `Command.set`; grup kedua dalam
privilege yang sama terhapus saat form user disimpan. Test mengemulasi penyimpanan widget.
"""

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged

MANAGER = "efn_expiry_monitor.group_markdown_manager"
OVERRIDE = "efn_expiry_monitor.group_expiry_override"


def widget_roundtrip(user):
    """Emulasi simpan widget Odoo 19: `findLast` grup terpilih per privilege + grup tanpa privilege, `Command.set`."""
    selected = set(user.group_ids.ids)
    keep = []
    for privilege in user.env["res.groups.privilege"].sudo().search([]):
        chosen = [gid for gid in privilege.group_ids.ids if gid in selected]
        if chosen:
            keep.append(chosen[-1])
    keep += [g.id for g in user.group_ids if not g.privilege_id]
    user.write({"group_ids": [Command.set(keep)]})


@tagged("post_install", "-at_install", "efn")
class TestEfnExpiryPrivilege(TransactionCase):
    def test_separate_privileges_no_new_implied(self):
        manager, override = self.env.ref(MANAGER), self.env.ref(OVERRIDE)
        self.assertTrue(manager.privilege_id and override.privilege_id)
        self.assertNotEqual(manager.privilege_id, override.privilege_id)
        self.assertNotIn(manager, override.all_implied_ids)
        self.assertNotIn(override, manager.all_implied_ids)

    def test_both_groups_survive_form_save(self):
        user = new_test_user(self.env, login="efn_expiry_priv_both", groups=f"base.group_user,{MANAGER},{OVERRIDE}")
        widget_roundtrip(user)
        self.assertIn(self.env.ref(MANAGER), user.group_ids)
        self.assertIn(self.env.ref(OVERRIDE), user.group_ids)
        self.assertTrue(user.has_group("point_of_sale.group_pos_user"))
        self.assertTrue(user.has_group("stock.group_stock_user"))
