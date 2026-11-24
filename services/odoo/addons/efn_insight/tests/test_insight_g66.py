"""G66 (docs/04 §30.7, ADR-051 Proposed): grup production planning -> peran `pp_planner`/`pp_warehouse` di `roles`
eit3 + aturan T9 (pembuat). Cakupan diuji lewat fungsi controller seperti `_token_v3`
(`apply_pp_roles(merge_scope_v3(...))`) + `build_payload_v3`; jalur HTTP di `test_insight_sso.py`.
"""

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_insight.controllers.main import _esb_scope, apply_pp_roles, merge_scope_v3
from odoo.addons.efn_insight.lib import insight_token as it
from odoo.addons.efn_insight.models.res_users import PERSONA_GROUPS, PP_GROUPS, empty_sm_scope

CTRL = "odoo.addons.efn_insight.controllers.main"
PLANNER = "efn_insight.group_prodplan_planner"
WAREHOUSE = "efn_insight.group_prodplan_warehouse"


@tagged("efn", "post_install", "-at_install")
class TestInsightG66(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env["res.company"]
        # Kode company unik per DB: kosongkan dulu (rollback akhir test).
        Company.with_context(active_test=False).search([("efn_brand_code", "!=", False)]).write(
            {"efn_brand_code": False}
        )
        Company.flush_model(["efn_brand_code"])
        cls.pb = Company.create({"name": "PT Uji G66 PB", "efn_brand_code": "PB"})
        cls.bcf = Company.create({"name": "PT Uji G66 BCF", "efn_brand_code": "BCF"})
        cls.gl = Company.create({"name": "PT Uji G66 GL", "efn_brand_code": "GL"})
        Branch = cls.env["efn.insight.esb.branch"]
        Branch.with_context(active_test=False).search([("esb_branch_id", ">=", 9000)]).unlink()
        cls.branch = Branch.create({"company_id": cls.pb.id, "esb_brand": "pb", "esb_branch_id": 9601, "name": "PB"})

    def _user(self, login, groups=(), personas=(), companies=None, branches=None):
        all_groups = ["base.group_user", *groups, *(PERSONA_GROUPS[p] for p in personas)]
        companies = companies or self.pb
        user = new_test_user(
            self.env,
            login=login,
            name=f"Uji {login}",
            groups=",".join(all_groups),
            company_id=companies[0].id,
            company_ids=[Command.set(companies.ids)],
        )
        if branches is not None:
            user.sudo().efn_insight_esb_branch_ids = [Command.set(branches.ids)]
        return user

    def _scope(self, user, sm=None, fnb=True):
        """Sama dengan `_token_v3`: gabung ESB + SM, lalu peran PP."""
        esb = _esb_scope(self.env, user, user._efn_insight_personas(), 3, fnb)
        merged = merge_scope_v3(esb, sm or empty_sm_scope())
        return apply_pp_roles(merged, user._efn_insight_pp_roles(), user.id)

    def _payload(self, user, **kwargs):
        scope = self._scope(user, **kwargs)
        return it.build_payload_v3(sub=user.id, name=user.name, iat=1795000000, jti="b" * 32, **scope)

    # ================================================================== grup
    def test_groups_and_privileges(self):
        self.assertEqual(set(PP_GROUPS), it.PP_ROLES)
        self.assertFalse(set(PP_GROUPS) & set(PERSONA_GROUPS), "PP bukan persona")
        insight_user = self.env.ref("efn_insight.group_insight_user")
        category = self.env.ref("efn_insight.module_category_efn_insight")
        base_privilege = self.env.ref("efn_insight.res_groups_privilege_efn_insight")
        for role, xmlid in PP_GROUPS.items():
            with self.subTest(role=role):
                group = self.env.ref(xmlid)
                self.assertIn(insight_user, group.implied_ids)
                self.assertEqual(group.privilege_id.group_ids, group, "satu grup per privilege")
                self.assertEqual(group.privilege_id.category_id, category)
                self.assertTrue(group.privilege_id.description)
                self.assertLess(base_privilege.sequence, group.privilege_id.sequence)
                # PP tidak memberi persona (tanda tangan ClickHouse tidak berubah).
                for persona_xmlid in PERSONA_GROUPS.values():
                    self.assertNotIn(self.env.ref(persona_xmlid), group.all_implied_ids)

    # ================================================================== grup -> peran
    def test_planner_gets_pp_planner(self):
        user = self._user("efn_g66_planner", [PLANNER])
        self.assertTrue(user.has_group("efn_insight.group_insight_user"), "implied pengguna Insight")
        payload = self._payload(user)
        self.assertEqual(payload["roles"], ["pp_planner", "user"])
        self.assertEqual((payload["codes"], payload["brs"]), (["PB"], []))

    def test_without_group_no_pp(self):
        user = self._user("efn_g66_plain", ["efn_insight.group_insight_user"], companies=self.pb | self.bcf)
        self.assertEqual(user._efn_insight_pp_roles(), set())
        self.assertEqual(self._payload(user)["roles"], ["user"])

    def test_both_groups_get_both_roles(self):
        # SoD per dokumen ditegakkan prodplan; Odoo boleh menerbitkan keduanya (docs/04 §30.7).
        user = self._user("efn_g66_both", [PLANNER, WAREHOUSE])
        self.assertEqual(self._payload(user)["roles"], ["pp_planner", "pp_warehouse", "user"])

    def test_warehouse_with_exec_and_all_brands(self):
        exec_user = self._user("efn_g66_exec_wh", [WAREHOUSE], personas=["exec"])
        self.assertEqual(self._payload(exec_user)["roles"], ["exec", "pp_warehouse", "user"])
        all_user = self._user("efn_g66_all_pl", [PLANNER, "efn_insight.group_insight_all_brands"], companies=self.gl)
        payload = self._payload(all_user)
        self.assertEqual(payload["roles"], ["all_brands", "pp_planner"])
        self.assertEqual(payload["codes"], sorted(it.ESB_CODES))

    def test_pp_does_not_depend_on_fnb_flag(self):
        user = self._user("efn_g66_noflag", [PLANNER])
        self.assertEqual(self._payload(user, fnb=False)["roles"], ["pp_planner", "user"])

    # ================================================================== T9 di pembuat (buang + log)
    def test_t9_branch_scoped_store_drops_pp(self):
        user = self._user("efn_g66_store_pl", [PLANNER, WAREHOUSE], personas=["store"], branches=self.branch)
        with self.assertLogs(CTRL, "INFO") as logs:
            payload = self._payload(user)
        self.assertEqual(payload["roles"], ["store", "user"])
        self.assertEqual(payload["brs"], ["pb:9601"])
        self.assertIn(f"insight_pp_conflict uid={user.id}", "\n".join(logs.output))
        self.assertNotIn(user.login, "\n".join(logs.output))

    def test_t9_store_with_exec_keeps_pp(self):
        # store + exec = PT penuh, brs kosong -> PP tetap.
        user = self._user("efn_g66_exec_store_pl", [PLANNER], personas=["store", "exec"], branches=self.branch)
        payload = self._payload(user)
        self.assertEqual(payload["roles"], ["exec", "pp_planner", "store", "user"])
        self.assertEqual(payload["brs"], [])

    def test_t9_no_esb_code_drops_pp(self):
        # User supermarket saja (tanpa PT ESB) dengan grup planner: PP dibuang, dashboard SMD tetap terbuka.
        user = self._user("efn_g66_smd_pl", [PLANNER], companies=self.gl)
        sm = dict(empty_sm_scope(), personas=["exec"], codes=["SMD"], cids=[999999], wids=[0, 42])
        with self.assertLogs(CTRL, "INFO") as logs:
            payload = self._payload(user, sm=sm)
        self.assertEqual(payload["roles"], ["exec", "user"])
        self.assertEqual(payload["codes"], ["SMD"])
        self.assertIn(f"insight_pp_no_esb uid={user.id}", "\n".join(logs.output))

    def test_apply_pp_roles_is_pure(self):
        scope = {"codes": ["PB"], "cids": [1], "roles": ["user"], "wids": [], "cats": [], "brs": []}
        result = apply_pp_roles(scope, {"pp_planner", "unknown"}, 7)
        self.assertEqual(result["roles"], ["pp_planner", "user"], "hanya PP_ROLES yang ditambahkan")
        self.assertEqual(scope["roles"], ["user"], "input tidak diubah")
        self.assertIs(apply_pp_roles(scope, set(), 7), scope)

    # ================================================================== validasi klaim (pembuat + rujukan)
    def _claims(self, **overrides):
        payload = {"codes": ["PB"], "cids": [1], "roles": ["pp_planner", "user"], "wids": [], "cats": [], "brs": []}
        payload.update(overrides)
        return payload

    def test_validate_t9_and_role_set(self):
        it.validate_claims_v3(self._claims())
        it.validate_claims_v3(self._claims(roles=["all_brands", "pp_planner", "pp_warehouse"], cids=[]))
        cases = (
            # T9: brs tidak kosong (kepala toko ber-cabang).
            ({"roles": ["pp_planner", "store", "user"], "brs": ["pb:1"]}, "bad_claims"),
            # T9: tanpa kode ESB.
            ({"roles": ["exec", "pp_warehouse", "user"], "codes": ["SMD"], "wids": [0]}, "bad_claims"),
            # Peran tidak dikenal / urutan salah.
            ({"roles": ["pp_admin", "user"]}, "bad_roles"),
            ({"roles": ["user", "pp_planner"]}, "bad_roles"),
            # PP tidak menggantikan basis user/all_brands.
            ({"roles": ["pp_planner"]}, "bad_roles"),
        )
        for overrides, code in cases:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, f"^{code}$"):
                it.validate_claims_v3(self._claims(**overrides))

    def test_eit2_never_accepts_pp(self):
        claims = self._claims()
        claims.pop("brs")
        with self.assertRaisesRegex(ValueError, "^bad_roles$"):
            it.validate_claims_v2(claims)

    def test_ascii_order_in_payload(self):
        # docs/04 §30.7: all_brands < category < dc < exec < marketing < pp_planner < pp_warehouse < store < user.
        user = self._user("efn_g66_order", [PLANNER, WAREHOUSE], personas=["exec", "marketing"])
        roles = self._payload(user)["roles"]
        self.assertEqual(roles, ["exec", "marketing", "pp_planner", "pp_warehouse", "user"])
        self.assertLessEqual(len(roles), it.MAX_ROLES_V3)
