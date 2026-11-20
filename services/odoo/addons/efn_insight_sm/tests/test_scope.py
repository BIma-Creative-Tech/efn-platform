"""Cakupan persona supermarket (docs/04 §28.13.3 butir 2) + konsistensi token eit2 dari cakupan nyata."""

from odoo import Command
from odoo.tests import TransactionCase, tagged

from odoo.addons.efn_insight.controllers.main import _insight_scope, merge_scope
from odoo.addons.efn_insight.lib import insight_token as it

from .common import setup_sm_fixture


@tagged("efn", "post_install", "-at_install")
class TestInsightSmScope(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        setup_sm_fixture(cls)

    def _scope(self, user):
        return user.with_user(user)._efn_insight_sm_scope()

    def _token_payload(self, user):
        """Payload eit2 lengkap seperti controller (ESB + SM) — wajib lolos aturan konsistensi."""
        scope = merge_scope(_insight_scope(self.env, user), self._scope(user))
        return it.build_payload_v2(sub=user.id, name=user.name, iat=1795000000, jti="a" * 32, **scope)

    def test_org_personas_all_warehouses_plus_zero(self):
        for persona in ("exec", "category", "dc", "marketing"):
            with self.subTest(persona=persona):
                user = self.make_user(f"efn_sm_scope_{persona}", [persona])
                scope = self._scope(user)
                self.assertEqual(scope["personas"], [persona])
                self.assertEqual(scope["codes"], ["SMD"])
                self.assertEqual(scope["cids"], [self.smd.id])
                self.assertEqual(scope["wids"], [0, *self.smd_wh_ids])
                self.assertIn(self.dc.id, scope["wids"])
                self.assertNotIn(self.pb_wh.id, scope["wids"])
                self.assertEqual(scope["cats"], [])
                self.assertFalse(scope["no_outlet"])
                payload = self._token_payload(user)
                self.assertEqual(payload["roles"], sorted([persona, "user"]))

    def test_store_only_own_outlet_without_zero(self):
        user = self.make_user("efn_sm_scope_store", ["store"])
        user.sudo().efn_outlet_warehouse_ids = [Command.set([self.outlet1.id, self.pb_wh.id])]
        scope = self._scope(user)
        self.assertEqual(scope["personas"], ["store"])
        self.assertEqual(scope["wids"], [self.outlet1.id], "gudang PT lain (PB) tidak ikut; tanpa 0")
        payload = self._token_payload(user)
        self.assertEqual(payload["roles"], ["store", "user"])
        self.assertEqual(payload["codes"], ["SMD"])

    def test_store_without_outlet_is_no_outlet(self):
        user = self.make_user("efn_sm_scope_store_none", ["store"])
        scope = self._scope(user)
        self.assertEqual(scope["personas"], [])
        self.assertEqual(scope["wids"], [])
        self.assertTrue(scope["no_outlet"])
        # Outlet di PT lain saja juga tidak sah.
        user.sudo().efn_outlet_warehouse_ids = [Command.set([self.pb_wh.id])]
        self.assertTrue(self._scope(user)["no_outlet"])
        # Outlet diarsipkan tidak ikut.
        user.sudo().efn_outlet_warehouse_ids = [Command.set([self.outlet2.id])]
        self.outlet2.active = False
        self.assertTrue(self._scope(user)["no_outlet"])

    def test_store_plus_exec(self):
        # Vektor A kontrak: exec + store -> wids = {0} ∪ semua warehouse.
        user = self.make_user("efn_sm_scope_exec_store", ["exec", "store"], companies=self.smd | self.pb)
        user.sudo().efn_outlet_warehouse_ids = [Command.set([self.outlet1.id])]
        payload = self._token_payload(user)
        self.assertEqual(payload["roles"], ["exec", "store", "user"])
        self.assertEqual(payload["codes"], ["PB", "SMD"])
        self.assertEqual(payload["cids"], sorted([self.smd.id, self.pb.id]))
        self.assertEqual(payload["wids"], [0, *self.smd_wh_ids])

    def test_category_cats(self):
        user = self.make_user("efn_sm_scope_cat", ["category"])
        user.sudo().efn_insight_categ_ids = [Command.set([self.categ_b.id, self.categ_a.id])]
        scope = self._scope(user)
        self.assertEqual(scope["cats"], sorted([self.categ_a.id, self.categ_b.id]))
        self.assertEqual(self._token_payload(user)["cats"], scope["cats"])
        # Kategori dihapus -> hilang dari cats (relasi ondelete cascade).
        self.categ_b.unlink()
        self.assertEqual(self._scope(user)["cats"], [self.categ_a.id])

    def test_cats_only_for_category_persona(self):
        user = self.make_user("efn_sm_scope_exec_cats", ["exec"])
        user.sudo().efn_insight_categ_ids = [Command.set([self.categ_a.id])]
        self.assertEqual(self._scope(user)["cats"], [])

    def test_no_sm_company_no_persona(self):
        user = self.make_user("efn_sm_scope_pb_only", ["exec", "store"], companies=self.pb)
        user.sudo().efn_outlet_warehouse_ids = [Command.set([self.outlet1.id])]
        scope = self._scope(user)
        self.assertEqual(scope["personas"], [])
        self.assertEqual((scope["codes"], scope["cids"], scope["wids"]), ([], [], []))
        payload = self._token_payload(user)
        self.assertEqual(payload["roles"], ["user"])
        self.assertEqual(payload["codes"], ["PB"])

    def test_archived_sm_company_excluded(self):
        user = self.make_user("efn_sm_scope_arch", ["exec"], companies=self.pb | self.smd)
        self.smd.active = False
        self.assertEqual(self._scope(user)["personas"], [])

    def test_sm_company_in_company_ids_not_switcher(self):
        # Company aktif di pemilih = PB; SMD hanya di company_ids -> tetap masuk (pola §26.2 / Q-INS2).
        user = self.make_user("efn_sm_scope_switch", ["dc"], companies=self.pb | self.smd)
        self.assertEqual(user.company_id, self.pb)
        self.assertEqual(self._scope(user)["cids"], [self.smd.id])

    def test_all_brands_does_not_grant_smc(self):
        all_brands = ["efn_insight.group_insight_all_brands"]
        user = self.make_user("efn_sm_scope_allb", [], companies=self.gl, extra=all_brands)
        self.assertEqual(self._scope(user)["personas"], [])
        user2 = self.make_user(
            "efn_sm_scope_allb_exec", ["exec"], companies=self.smd, extra=["efn_insight.group_insight_all_brands"]
        )
        payload = self._token_payload(user2)
        self.assertEqual(payload["roles"], ["all_brands", "exec"])
        self.assertIn("SMD", payload["codes"])
        self.assertIn(self.smd.id, payload["cids"])

    def test_without_persona_empty(self):
        user = self.make_user("efn_sm_scope_plain", [], extra=["efn_insight.group_insight_user"])
        scope = self._scope(user)
        self.assertEqual(scope["personas"], [])
        self.assertFalse(scope["no_outlet"])

    def test_kpi_admin_is_not_persona(self):
        user = self.make_user("efn_sm_scope_kpi", ["kpi_admin"])
        self.assertFalse(user.has_group("efn_insight.group_insight_user"))
        self.assertEqual(self._scope(user)["personas"], [])
