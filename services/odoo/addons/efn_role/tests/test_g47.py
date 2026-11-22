"""G47 (docs/04 §28.13.2, fit-gap G47 §2.5) + G52 (§28.15.2): persona Insight di daftar grup sensitif (SEC-193).

G52: grup persona dimiliki efn_insight (xmlid name tetap); kpi_admin tetap efn_insight_sm.
"""

from odoo.tests import TransactionCase, tagged

from odoo.addons.efn_role.models.role_policy import SENSITIVE_GROUP_XMLIDS, sensitive_group_ids

SM_SENSITIVE = (
    "efn_insight.group_insight_exec",
    "efn_insight.group_insight_category",
    "efn_insight.group_insight_dc",
    "efn_insight.group_insight_marketing",
    "efn_insight_sm.group_insight_kpi_admin",
)


@tagged("post_install", "-at_install", "efn")
class TestEfnRoleG47(TransactionCase):
    def test_constant_lists_sm_personas_but_not_store(self):
        for xmlid in SM_SENSITIVE:
            self.assertIn(xmlid, SENSITIVE_GROUP_XMLIDS)
        self.assertNotIn("efn_insight.group_insight_store", SENSITIVE_GROUP_XMLIDS)
        # G52: xmlid lama efn_insight_sm.group_insight_<persona> tidak lagi dirujuk.
        for persona in ("exec", "category", "dc", "marketing", "store"):
            self.assertNotIn(f"efn_insight_sm.group_insight_{persona}", SENSITIVE_GROUP_XMLIDS)

    def test_resolved_ids_when_installed(self):
        ids = set(sensitive_group_ids(self.env))
        store = self.env.ref("efn_insight.group_insight_store", raise_if_not_found=False)
        installed = [self.env.ref(x, raise_if_not_found=False) for x in SM_SENSITIVE]
        if not all(installed):
            # efn_insight/efn_insight_sm belum terpasang: xmlid dilewati tanpa galat (xmlid_group_ids).
            self.assertTrue(ids)
            return
        for group in installed:
            self.assertIn(group.id, ids, group.get_external_id()[group.id])
        self.assertNotIn(store.id, ids)
