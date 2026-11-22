"""G52 (docs/04 §28.15.2-§28.15.4, §28.15.11): relokasi grup persona, model cabang ESB, dan aturan cakupan v3.

Cakupan diuji lewat fungsi controller (`_esb_scope`, `merge_scope_v3`) + `build_payload_v3` (aturan T1-T8 dari
cakupan nyata). Hook SM tidak dipanggil di sini (cakupan SM kosong; kombinasi SMD diuji di efn_insight_sm).
"""

import psycopg2

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.efn_insight.controllers.main import _esb_scope, merge_scope_v3
from odoo.addons.efn_insight.lib import insight_token as it
from odoo.addons.efn_insight.lib.persona_xmlids import MOVED_NAMES, rename_persona_xmlids
from odoo.addons.efn_insight.models.res_users import PERSONA_GROUPS, empty_sm_scope

# ID cabang sintetis: "pb:91012" < "pb:9107" (urutan ASCII ≠ numerik).
BR_A, BR_B = 9107, 91012


@tagged("efn", "post_install", "-at_install")
class TestInsightG52(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env["res.company"]
        # Kode company unik per DB: kosongkan dulu (rollback akhir test). active_test=False: arsip juga memegang kode.
        Company.with_context(active_test=False).search([("efn_brand_code", "!=", False)]).write(
            {"efn_brand_code": False}
        )
        Company.flush_model(["efn_brand_code"])
        cls.pb = Company.create({"name": "PT Uji G52 PB", "efn_brand_code": "PB"})
        cls.bcf = Company.create({"name": "PT Uji G52 BCF", "efn_brand_code": "BCF"})
        cls.bcr = Company.create({"name": "PT Uji G52 BCR", "efn_brand_code": "BCR"})
        cls.wtz = Company.create({"name": "PT Uji G52 WTZ", "efn_brand_code": "WTZ"})
        cls.gl = Company.create({"name": "PT Uji G52 GL", "efn_brand_code": "GL"})
        cls.Branch = cls.env["efn.insight.esb.branch"]
        # Data cabang uji tidak boleh bentrok dengan impor nyata di DB uji.
        cls.Branch.with_context(active_test=False).search([("esb_branch_id", ">=", 9000)]).unlink()
        cls.br_a = cls.Branch.create(
            {"company_id": cls.pb.id, "esb_brand": "pb", "esb_branch_id": BR_A, "name": "PB Uji A"}
        )
        cls.br_b = cls.Branch.create(
            {"company_id": cls.pb.id, "esb_brand": "pb", "esb_branch_id": BR_B, "name": "PB Uji B"}
        )
        cls.br_wtz = cls.Branch.create(
            {"company_id": cls.wtz.id, "esb_brand": "wtz", "esb_branch_id": 9003, "name": "WTZ Uji"}
        )
        cls.br_bacha = cls.Branch.create(
            {"company_id": cls.bcr.id, "esb_brand": "bacha", "esb_branch_id": 9015, "name": "Bacha butik uji"}
        )

    def _user(self, login, personas=(), companies=None, extra=(), branches=None):
        groups = ["base.group_user", "efn_insight.group_insight_user", *(PERSONA_GROUPS[p] for p in personas), *extra]
        companies = companies or self.pb
        user = new_test_user(
            self.env,
            login=login,
            name=f"Uji {login}",
            groups=",".join(groups),
            company_id=companies[0].id,
            company_ids=[Command.set(companies.ids)],
        )
        if branches is not None:
            user.sudo().efn_insight_esb_branch_ids = [Command.set(branches.ids)]
        return user

    def _scope(self, user, version=3, fnb=True, sm=None):
        esb = _esb_scope(self.env, user, user._efn_insight_personas(), version, fnb)
        return esb, merge_scope_v3(esb, sm or empty_sm_scope())

    def _payload(self, user, **kwargs):
        _esb, scope = self._scope(user, **kwargs)
        return it.build_payload_v3(sub=user.id, name=user.name, iat=1795000000, jti="a" * 32, **scope)

    # ================================================================== relokasi grup (§28.15.2)
    def test_persona_groups_owned_by_efn_insight(self):
        self.assertEqual(set(PERSONA_GROUPS), it.PERSONAS)
        insight_user = self.env.ref("efn_insight.group_insight_user")
        category = self.env.ref("efn_insight.module_category_efn_insight")
        IMD = self.env["ir.model.data"].sudo()
        for persona, xmlid in PERSONA_GROUPS.items():
            with self.subTest(persona=persona):
                group = self.env.ref(xmlid)
                self.assertIn(insight_user, group.implied_ids)
                self.assertEqual(group.privilege_id.group_ids, group, "satu grup per privilege")
                self.assertEqual(group.privilege_id.category_id, category)
                privilege_xmlid = f"efn_insight.res_groups_privilege_insight_sm_{persona}"
                self.assertEqual(self.env.ref(privilege_xmlid), group.privilege_id)
                self.assertIsNone(self.env.ref(f"efn_insight_sm.group_insight_{persona}", raise_if_not_found=False))
        self.assertEqual(IMD.search_count([("module", "=", "efn_insight"), ("name", "in", list(MOVED_NAMES))]), 10)
        self.assertFalse(IMD.search_count([("module", "=", "efn_insight_sm"), ("name", "in", list(MOVED_NAMES))]))

    def test_relocation_keeps_group_ids_and_membership(self):
        """Simulasi DB pra-G52 (xmlid di efn_insight_sm) -> pre-migrate -> id grup & anggota sama, idempoten."""
        # ormcache xmlid berisi nilai simulasi: kosongkan agar test lain tidak membaca cache basi setelah rollback.
        self.addCleanup(self.env.registry.clear_all_caches)
        user = self._user("efn_g52_reloc", ["store", "exec", "dc"])
        ids_before = {persona: self.env.ref(xmlid).id for persona, xmlid in PERSONA_GROUPS.items()}
        kpi = self.env.ref("efn_insight_sm.group_insight_kpi_admin", raise_if_not_found=False)
        self.env.flush_all()
        cr = self.env.cr
        cr.execute(
            "UPDATE ir_model_data SET module = 'efn_insight_sm' WHERE module = 'efn_insight' AND name IN %s",
            (MOVED_NAMES,),
        )
        self.assertEqual(cr.rowcount, 10)
        self.env.invalidate_all()
        self.env.registry.clear_all_caches()
        self.assertIsNone(self.env.ref("efn_insight.group_insight_store", raise_if_not_found=False))
        self.assertEqual(self.env.ref("efn_insight_sm.group_insight_store").id, ids_before["store"])

        self.assertEqual(rename_persona_xmlids(cr), 10)
        self.env.invalidate_all()
        self.env.registry.clear_all_caches()
        for persona, xmlid in PERSONA_GROUPS.items():
            self.assertEqual(self.env.ref(xmlid).id, ids_before[persona], persona)
        for persona in ("store", "exec", "dc"):
            self.assertTrue(user.has_group(PERSONA_GROUPS[persona]), persona)
        self.assertEqual(user._efn_insight_personas(), {"store", "exec", "dc"})
        # Idempoten (pre-migrate kedua modul menjalankan SQL yang sama).
        self.assertEqual(rename_persona_xmlids(cr), 0)
        if kpi:
            # kpi_admin tidak ikut pindah.
            self.assertEqual(self.env.ref("efn_insight_sm.group_insight_kpi_admin"), kpi)

    def test_relocation_skips_conflicting_rows(self):
        # Baris efn_insight_sm dengan nama yang sudah ada di efn_insight tidak dipindah (tanpa galat unik).
        cr = self.env.cr
        cr.execute(
            "INSERT INTO ir_model_data (module, name, model, res_id, noupdate) "
            "VALUES ('efn_insight_sm', 'group_insight_dc', 'res.groups', %s, false)",
            (self.env.ref("base.group_user").id,),
        )
        self.assertEqual(rename_persona_xmlids(cr), 0)
        cr.execute("SELECT count(*) FROM ir_model_data WHERE module = 'efn_insight_sm' AND name = 'group_insight_dc'")
        self.assertEqual(cr.fetchone()[0], 1)

    # ================================================================== model cabang (§28.15.3)
    def test_branch_display_and_defaults(self):
        self.assertTrue(self.br_a.is_store and self.br_a.active)
        self.assertEqual(self.br_a.display_name, f"[pb:{BR_A}] PB Uji A")
        field = self.env["res.users"]._fields["efn_insight_esb_branch_ids"]
        self.assertEqual(field.relation, "efn_res_users_insight_esb_branch_rel")
        self.assertEqual(field.domain, "[('company_id', 'in', company_ids)]")
        self.assertEqual(field.groups, "base.group_erp_manager")
        self.assertNotIn("efn_insight_esb_branch_ids", self.env["res.users"].SELF_WRITEABLE_FIELDS)

    def test_branch_unique_per_esb_account(self):
        # Sama id beda akun ESB = sah (branch_id hanya unik per akun ESB).
        self.Branch.create({"company_id": self.wtz.id, "esb_brand": "wtz", "esb_branch_id": BR_A, "name": "WTZ A"})
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.IntegrityError), self.env.cr.savepoint():
            self.Branch.create({"company_id": self.pb.id, "esb_brand": "pb", "esb_branch_id": BR_A, "name": "dup"})
            self.env.flush_all()

    def test_branch_brand_company_match(self):
        self.Branch.create({"company_id": self.bcf.id, "esb_brand": "bacha", "esb_branch_id": 9016, "name": "BCF"})
        for company, brand in ((self.wtz, "pb"), (self.pb, "bacha"), (self.gl, "pb"), (self.bcr, "imd")):
            with self.subTest(company=company.efn_brand_code, brand=brand), self.assertRaises(ValidationError):
                self.Branch.create(
                    {"company_id": company.id, "esb_brand": brand, "esb_branch_id": 9020, "name": "salah PT"}
                )
        moved = self.Branch.create({"company_id": self.pb.id, "esb_brand": "pb", "esb_branch_id": 9021, "name": "m"})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            moved.company_id = self.wtz
            moved.flush_recordset()

    def test_branch_id_range(self):
        for value in (0, -1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.Branch.create({"company_id": self.pb.id, "esb_brand": "pb", "esb_branch_id": value, "name": "x"})

    def test_branch_acl_and_record_rule(self):
        reader = self._user("efn_g52_reader", ["store"])
        with self.assertRaises(AccessError):
            self.Branch.with_user(reader).search([])
        admin = new_test_user(
            self.env,
            login="efn_g52_admin_pb",
            groups="base.group_user,base.group_erp_manager",
            company_id=self.pb.id,
            company_ids=[Command.set([self.pb.id, self.bcf.id])],
        )
        visible = self.Branch.with_user(admin).search([("esb_branch_id", ">=", 9000)])
        self.assertIn(self.br_a, visible)
        self.assertNotIn(self.br_wtz, visible, "record rule multi-company: PT WTZ bukan milik admin")
        self.assertNotIn(self.br_bacha, visible)
        created = self.Branch.with_user(admin).create(
            {"company_id": self.bcf.id, "esb_brand": "bacha", "esb_branch_id": 9017, "name": "oleh admin"}
        )
        self.assertTrue(created)

    def test_branch_csv_import(self):
        fields = ["esb_brand", "esb_branch_id", "name", "branch_code", "is_store", "active", "company_id"]
        rows = [
            ["pb", "9101", "PB impor 1", "PB-01", "True", "True", self.pb.name],
            ["wtz", "9102", "WTZ gudang", "", "False", "True", self.wtz.name],
        ]
        result = self.Branch.load(fields, rows)
        self.assertFalse([m for m in result["messages"] if m.get("type") == "error"], result["messages"])
        imported = self.Branch.browse(result["ids"])
        self.assertEqual(imported.mapped("esb_branch_id"), [9101, 9102])
        self.assertEqual(imported.mapped("is_store"), [True, False])
        self.assertEqual(imported.company_id, self.pb | self.wtz)

    # ================================================================== cakupan v3 (§28.15.4, §28.15.11)
    def test_contract_example_store_pb(self):
        user = self._user("efn_g52_store", ["store"], branches=self.br_a | self.br_b)
        payload = self._payload(user)
        self.assertEqual(payload["codes"], ["PB"])
        self.assertEqual(payload["cids"], [self.pb.id])
        self.assertEqual(payload["roles"], ["store", "user"])
        self.assertEqual(payload["brs"], [f"pb:{BR_B}", f"pb:{BR_A}"])  # urut ASCII
        self.assertEqual(payload["wids"], [])

    def test_store_only_bacha_vector_g(self):
        user = self._user("efn_g52_bacha", ["store"], companies=self.bcr, branches=self.br_bacha)
        payload = self._payload(user)
        self.assertEqual((payload["codes"], payload["brs"]), (["BCR"], ["bacha:9015"]))

    def test_store_only_revoked_without_flag_and_in_rollback_versions(self):
        """L-241: pencabutan cakupan PT penuh tidak bergantung flag maupun versi token."""
        user = self._user("efn_g52_revoke", ["store"], companies=self.pb | self.bcf, branches=self.br_a)
        for version, fnb in ((3, False), (2, True), (2, False), (1, True)):
            with self.subTest(version=version, fnb=fnb):
                esb, scope = self._scope(user, version=version, fnb=fnb)
                self.assertEqual((esb["codes"], esb["cids"], esb["brs"]), ([], [], []))
                self.assertFalse(esb["no_branch"])
                self.assertEqual(scope["codes"], [])

    def test_store_only_without_branch_is_no_branch(self):
        user = self._user("efn_g52_nobranch", ["store"])
        esb, scope = self._scope(user)
        self.assertTrue(esb["no_branch"])
        self.assertEqual(scope["codes"], [])
        # Tanpa PT ESB sama sekali: bukan no_branch (pesan outlet/PT berlaku).
        smd_like = self._user("efn_g52_nobranch_gl", ["store"], companies=self.gl)
        self.assertFalse(self._scope(smd_like)[0]["no_branch"])

    def test_invalid_branches_ignored(self):
        archived = self.Branch.create(
            {"company_id": self.pb.id, "esb_brand": "pb", "esb_branch_id": 9201, "name": "arsip", "active": False}
        )
        warehouse = self.Branch.create(
            {"company_id": self.pb.id, "esb_brand": "pb", "esb_branch_id": 9202, "name": "gudang", "is_store": False}
        )
        # company_id = BCF (PB hanya di company_ids) agar PB bisa diarsipkan di akhir test.
        user = self._user(
            "efn_g52_invalid",
            ["store"],
            companies=self.bcf | self.pb,
            branches=self.br_a | archived | warehouse | self.br_wtz,
        )
        self.assertEqual(self._payload(user)["brs"], [f"pb:{BR_A}"], "arsip, non-toko, PT bukan milik user dibuang")
        # Hanya cabang PT yang bukan milik user -> tidak ada cabang sah -> no_branch.
        user.sudo().efn_insight_esb_branch_ids = [Command.set(self.br_wtz.ids)]
        self.assertTrue(self._scope(user)[0]["no_branch"])
        # Cabang sah tetapi PT-nya diarsipkan -> tidak sah.
        user.sudo().efn_insight_esb_branch_ids = [Command.set(self.br_a.ids)]
        self.pb.active = False
        self.assertFalse(self._scope(user)[0]["brs"])

    def test_exec_and_store_full_pt(self):
        user = self._user("efn_g52_exec_store", ["store", "exec"], companies=self.pb | self.bcf, branches=self.br_a)
        payload = self._payload(user)
        self.assertEqual(payload["roles"], ["exec", "store", "user"])
        self.assertEqual(payload["codes"], ["BCF", "PB"])
        self.assertEqual(payload["brs"], [])

    def test_store_plus_marketing_issues_marketing_only(self):
        user = self._user("efn_g52_mkt_store", ["store", "marketing"], companies=self.pb | self.wtz)
        payload = self._payload(user)
        self.assertEqual(payload["roles"], ["marketing", "user"])
        self.assertEqual(payload["codes"], ["PB", "WTZ"])

    def test_store_plus_dc_or_category_no_esb(self):
        for persona in ("dc", "category"):
            with self.subTest(persona=persona):
                user = self._user(f"efn_g52_{persona}_store", ["store", persona], branches=self.br_a)
                esb, scope = self._scope(user)
                self.assertEqual((esb["codes"], esb["personas"], esb["brs"]), ([], [], []))
                self.assertFalse(esb["no_branch"])

    def test_store_plus_all_brands(self):
        user = self._user(
            "efn_g52_allb_store", ["store"], companies=self.gl, extra=["efn_insight.group_insight_all_brands"]
        )
        payload = self._payload(user)
        self.assertEqual(payload["roles"], ["all_brands"], "store hanya bila SM menerbitkannya")
        self.assertEqual(payload["codes"], sorted(it.ESB_CODES))
        self.assertEqual(payload["brs"], [])

    def test_fnb_personas_require_flag(self):
        exec_user = self._user("efn_g52_exec", ["exec"])
        self.assertEqual(self._payload(exec_user, fnb=False)["roles"], ["user"])
        self.assertEqual(self._payload(exec_user)["roles"], ["exec", "user"])
        mkt = self._user("efn_g52_mkt", ["marketing"], companies=self.pb | self.wtz)
        self.assertEqual(self._payload(mkt)["roles"], ["marketing", "user"])
        # category/dc tidak memberi apa pun di F&B.
        dc = self._user("efn_g52_dc", ["dc"])
        payload = self._payload(dc)
        self.assertEqual((payload["roles"], payload["codes"]), (["user"], ["PB"]))

    def test_plain_user_unchanged(self):
        user = self._user("efn_g52_plain", [], companies=self.pb | self.bcf)
        payload = self._payload(user)
        self.assertEqual((payload["codes"], payload["roles"], payload["brs"]), (["BCF", "PB"], ["user"], []))

    def test_sm_scope_merges_with_branch_scope(self):
        # Store F&B (brs) + store SMD (wids) — gabungan wajib lolos T1-T6.
        user = self._user("efn_g52_store_both", ["store"], branches=self.br_a)
        sm = dict(empty_sm_scope(), personas=["store"], codes=["SMD"], cids=[999999], wids=[42])
        payload = self._payload(user, sm=sm)
        self.assertEqual(payload["codes"], ["PB", "SMD"])
        self.assertEqual(payload["roles"], ["store", "user"])
        self.assertEqual(payload["brs"], [f"pb:{BR_A}"])
        self.assertEqual(payload["wids"], [42])
