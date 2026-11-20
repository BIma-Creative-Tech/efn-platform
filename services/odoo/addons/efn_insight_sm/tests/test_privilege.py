"""Grup persona & privilege (docs/04 §28.13.2, fit-gap G47 §2.1-§2.2): satu privilege per grup, implied, persona tidak
memberi hak tindakan, dan beberapa persona tetap dipegang setelah form user Odoo 19 disimpan."""

from odoo.tests import TransactionCase, tagged

from .common import PERSONA, setup_sm_fixture, widget_roundtrip

ACTION_GROUPS = (
    "efn_replenishment.group_replenishment_store",
    "efn_replenishment.group_replenishment_buyer",
    "efn_replenishment.group_replenishment_admin",
    "efn_expiry_monitor.group_markdown_manager",
    "efn_expiry_monitor.group_expiry_override",
    "efn_campaign.group_campaign_user",
    "efn_campaign.group_campaign_approver",
    "efn_assortment.group_assortment_manager",
    "stock.group_stock_user",
    "purchase.group_purchase_user",
)


@tagged("efn", "post_install", "-at_install")
class TestInsightSmPrivilege(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        setup_sm_fixture(cls)

    def test_one_privilege_per_group(self):
        category = self.env.ref("efn_insight.module_category_efn_insight")
        insight_user = self.env.ref("efn_insight.group_insight_user")
        privileges = set()
        for name, xmlid in PERSONA.items():
            group = self.env.ref(xmlid)
            with self.subTest(persona=name):
                self.assertTrue(group.privilege_id)
                self.assertEqual(group.privilege_id.category_id, category)
                self.assertEqual(group.privilege_id.group_ids, group, "satu grup per privilege")
                self.assertTrue(group.privilege_id.description)
                privileges.add(group.privilege_id.id)
                if name == "kpi_admin":
                    self.assertNotIn(insight_user, group.all_implied_ids, "kpi_admin tidak memberi dashboard")
                    self.assertIn(self.env.ref("base.group_user"), group.implied_ids)
                else:
                    self.assertIn(insight_user, group.implied_ids)
        self.assertEqual(len(privileges), 6)
        base_privilege = self.env.ref("efn_insight.res_groups_privilege_efn_insight")
        sequences = self.env["res.groups.privilege"].browse(list(privileges)).mapped("sequence")
        self.assertLess(base_privilege.sequence, min(sequences), "privilege 'Insight' tampil pertama")

    def test_personas_imply_no_action_group(self):
        action = [self.env.ref(x, raise_if_not_found=False) for x in ACTION_GROUPS]
        action = [g for g in action if g]
        for name, xmlid in PERSONA.items():
            implied = self.env.ref(xmlid).all_implied_ids
            for group in action:
                with self.subTest(persona=name, group=group.name):
                    self.assertNotIn(group, implied)
        for group in action:
            for xmlid in PERSONA.values():
                self.assertNotIn(self.env.ref(xmlid), group.all_implied_ids)

    def test_multiple_personas_survive_form_save(self):
        repl_store = "efn_replenishment.group_replenishment_store"
        extra = [repl_store] if self.env.ref(repl_store, raise_if_not_found=False) else []
        user = self.make_user("efn_sm_priv_multi", ["exec", "store", "dc", "kpi_admin"], extra=extra)
        widget_roundtrip(user)
        for name in ("exec", "store", "dc", "kpi_admin"):
            self.assertIn(self.env.ref(PERSONA[name]), user.group_ids, name)
        for xmlid in extra:
            self.assertIn(self.env.ref(xmlid), user.group_ids, xmlid)
        self.assertTrue(user.has_group("efn_insight.group_insight_user"))

    def test_menu_and_action(self):
        menu = self.env.ref("efn_insight_sm.menu_efn_insight_sm_kpi")
        self.assertEqual(menu.parent_id, self.env.ref("stock.menu_stock_config_settings"))
        self.assertEqual(menu.group_ids, self.env.ref(PERSONA["kpi_admin"]))
        self.assertEqual(menu.action, self.env.ref("efn_insight_sm.action_efn_insight_sm_kpi"))
