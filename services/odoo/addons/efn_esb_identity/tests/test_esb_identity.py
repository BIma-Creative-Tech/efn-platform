"""G66b (docs/04 §30.17.11, ADR-051 A1 Proposed; fit-gap efn-odoo-functional "G66b" §4 butir 3).

Metode RPC dipanggil langsung sebagai user teknis (jalur /json/2 sama: `get_public_method` + kwargs
`payload`). Semua data = data uji (tanpa nama/username nyata). Tidak ada rahasia.
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessDenied, AccessError, UserError, ValidationError
from odoo.fields import Command
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_esb_identity.models.efn_esb_identity import GROUP_PROVISIONER, STALE_LOGIN_DAYS

LOGGER = "odoo.addons.efn_esb_identity.models.efn_esb_identity"
PP_XMLIDS = (
    "efn_insight.group_prodplan_planner",
    "efn_insight.group_prodplan_warehouse",
    "efn_insight.group_insight_user",
)
PROV_GROUPS = f"base.group_user,{GROUP_PROVISIONER}"


@tagged("efn", "post_install", "-at_install")
class TestEsbIdentity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        Company = env["res.company"]
        # efn_brand_code unik per DB: kosongkan dulu (rollback akhir test), pola test_insight_g66.
        Company.with_context(active_test=False).search([("efn_brand_code", "!=", False)]).write(
            {"efn_brand_code": False}
        )
        Company.flush_model(["efn_brand_code"])
        cls.pb = Company.create({"name": "PT Uji ESB-ID PB", "efn_brand_code": "PB"})
        cls.bcf = Company.create({"name": "PT Uji ESB-ID BCF", "efn_brand_code": "BCF"})
        cls.bcr = Company.create({"name": "PT Uji ESB-ID BCR", "efn_brand_code": "BCR"})
        Branch = env["efn.insight.esb.branch"].with_context(active_test=False)
        Branch.search([("esb_branch_id", ">=", 9700), ("esb_branch_id", "<", 9800)]).unlink()
        cls.br_pb = cls._branch(cls.pb, "pb", 9701)
        cls.br_pb_ck = cls._branch(cls.pb, "pb", 9702, is_store=False)
        cls.br_pb_archived = cls._branch(cls.pb, "pb", 9703, active=False)
        cls.br_bcf = cls._branch(cls.bcf, "bacha", 9711)
        cls.br_bcr = cls._branch(cls.bcr, "bacha", 9712)

        cls.group_user = env.ref("base.group_user")
        cls.group_multi = env.ref("base.group_multi_company")
        cls.group_prov = env.ref(GROUP_PROVISIONER)
        cls.provisioner = new_test_user(env, login="efn_esb_prov_uji", groups=PROV_GROUPS)
        cls.plain = new_test_user(env, login="efn_esb_plain_uji", groups="base.group_user")
        sa_companies = env.company | cls.pb
        cls.superadmin = new_test_user(
            env,
            login="efn_esb_sa_uji",
            groups="base.group_system",
            company_id=env.company.id,
            company_ids=[Command.set(sa_companies.ids)],
        )
        cls.mgr_pb = new_test_user(
            env,
            login="efn_esb_mgr_pb_uji",
            groups="base.group_erp_manager",
            company_id=cls.pb.id,
            company_ids=[Command.set(cls.pb.ids)],
        )
        cls.Identity = env["efn.esb.identity"]

    @classmethod
    def _branch(cls, company, brand, bid, **extra):
        vals = {"company_id": company.id, "esb_brand": brand, "esb_branch_id": bid, "name": f"Uji {bid}"}
        vals.update(extra)
        return cls.env["efn.insight.esb.branch"].create(vals)

    # ------------------------------------------------------------------ helper
    def _payload(self, **over):
        payload = {
            "esb_account": "pb",
            "username": "uji.planner",
            "full_name": "Planner Uji",
            "company_code": "PB",
            "role_id": 7,
            "pp_roles": ["pp_planner"],
            "branch_ids": [9701],
        }
        payload.update(over)
        return payload

    def _provision(self, payload, user=None):
        return self.Identity.with_user(user or self.provisioner).efn_esb_provision(payload)

    def _identity(self, result):
        return self.Identity.browse(result["identity_id"])

    def _user(self, identity):
        return identity.user_id.with_context(active_test=False)

    def _password_is_null(self, user):
        self.env["res.users"].flush_model()
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", [user.id])
        return self.env.cr.fetchone()[0] is None

    # ================================================================== create
    def test_01_create_archived_internal_user_without_pp_groups(self):
        result = self._provision(self._payload())
        self.assertEqual(set(result), {"identity_id", "created"}, "respons tanpa PII")
        self.assertIs(result["created"], True)
        identity = self._identity(result)
        user = self._user(identity)
        self.assertEqual(identity.state, "shadow")
        self.assertEqual((identity.esb_account, identity.esb_username), ("pb", "uji.planner"))
        self.assertEqual(identity.esb_pp_roles, "pp_planner")
        self.assertEqual(identity.esb_role_id, 7)
        self.assertEqual(identity.esb_branch_ids, self.br_pb)
        self.assertEqual(identity.company_id, self.pb)
        self.assertTrue(identity.first_login_at and identity.last_login_at)

        self.assertFalse(user.active, "shadow user diarsipkan sejak create")
        self.assertFalse(user.share, "user internal")
        self.assertEqual(user.login, "esb:pb:uji.planner")
        self.assertEqual(user.name, "Planner Uji")
        self.assertFalse(user.email)
        self.assertEqual(set(user.group_ids.ids), {self.group_user.id}, "grup eksplisit hanya Internal User")
        for xmlid in PP_XMLIDS:
            self.assertNotIn(self.env.ref(xmlid), user.all_group_ids, xmlid)
        self.assertEqual((user.company_id, user.company_ids), (self.pb, self.pb))
        self.assertFalse(user.sudo().efn_insight_esb_branch_ids, "persona store tidak diisi")
        self.assertTrue(self._password_is_null(user), "tanpa password")
        self.assertFalse(self.env["res.users"].search([("login", "=", user.login)]), "tidak terlihat _login")

    def test_02_username_normalized_lowercase(self):
        result = self._provision(self._payload(username="  Uji.Besar "))
        identity = self._identity(result)
        self.assertEqual(identity.esb_username, "uji.besar")
        self.assertEqual(self._user(identity).login, "esb:pb:uji.besar")
        again = self._provision(self._payload(username="uji.besar"))
        self.assertEqual((again["identity_id"], again["created"]), (identity.id, False))

    # ================================================================== idempoten + cermin
    def test_03_idempotent_and_mirror_overwritten(self):
        first = self._provision(self._payload())
        second = self._provision(self._payload())
        self.assertEqual((second["identity_id"], second["created"]), (first["identity_id"], False))
        identity = self._identity(first)
        Users = self.env["res.users"].with_context(active_test=False)
        self.assertEqual(Users.search_count([("login", "=", "esb:pb:uji.planner")]), 1)

        identity.write({"last_login_at": fields.Datetime.now() - timedelta(days=3)})
        old = identity.last_login_at
        payload = self._payload(pp_roles=["pp_warehouse", "pp_planner"], branch_ids=[9701, 9702, 9702])
        self._provision(payload)
        self.assertEqual(identity.esb_pp_roles, "pp_planner,pp_warehouse")
        self.assertEqual(identity.esb_branch_ids, self.br_pb | self.br_pb_ck)
        self.assertGreater(identity.last_login_at, old)

        # Izin dikurangi di ESB -> cermin ikut berkurang; tetap tanpa grup PP.
        self._provision(self._payload(pp_roles=[], branch_ids=[9702], full_name="Planner Uji Baru"))
        self.assertFalse(identity.esb_pp_roles)
        self.assertEqual(identity.esb_branch_ids, self.br_pb_ck)
        user = self._user(identity)
        self.assertEqual(user.name, "Planner Uji Baru")
        self.assertEqual(set(user.group_ids.ids), {self.group_user.id})

    def test_04_manual_unarchive_of_shadow_is_reverted(self):
        identity = self._identity(self._provision(self._payload()))
        user = self._user(identity)
        planner = self.env.ref(PP_XMLIDS[0])
        user.sudo().write({"active": True, "group_ids": [Command.link(planner.id)]})
        self._provision(self._payload())
        self.assertFalse(user.active, "shadow (belum diaktifkan) diarsipkan kembali")
        self.assertEqual(set(user.group_ids.ids), {self.group_user.id}, "grup PP manual dicabut")

    # ================================================================== payload tertutup
    def test_05_payload_with_secret_or_unknown_key_rejected(self):
        before = self.Identity.search_count([])
        for extra in ({"password": "x"}, {"token": "x"}, {"email": "a@b.c"}, {"phone": "0812"}):
            with self.subTest(key=next(iter(extra))), self.assertRaises(UserError) as cm:
                self._provision(self._payload(**extra))
            self.assertEqual(cm.exception.args[0], "efn_esb_identity:invalid_input")
        missing = self._payload()
        del missing["branch_ids"]
        with self.assertRaises(UserError):
            self._provision(missing)
        self.assertEqual(self.Identity.search_count([]), before)

    def test_06_invalid_values_rejected(self):
        cases = {
            "esb_account": ["xx", 5, None],
            "username": ["", "a b", "x" * 65, "nama;drop", 12],
            "full_name": ["x" * 129, "a\nb", None],
            "company_code": [5, "x" * 65],
            "role_id": [-1, True, "7", 2**32],
            "pp_roles": [["admin"], "pp_planner", ["pp_planner", "pp_planner"]],
            "branch_ids": [[0], ["9701"], [True], list(range(1, 202)), 9701],
        }
        before = self.Identity.search_count([])
        for key, values in cases.items():
            for value in values:
                label = repr(value)[:20]
                with self.subTest(key=key, value=label), self.assertRaises(UserError) as cm:
                    self._provision(self._payload(**{key: value}))
                self.assertEqual(cm.exception.args[0], "efn_esb_identity:invalid_input")
        with self.assertRaises(UserError):
            self._provision([self._payload()])
        with self.assertRaises(UserError) as cm:
            self._provision(self._payload(branch_ids=list(range(1, 5001))))
        self.assertEqual(cm.exception.args[0], "efn_esb_identity:payload_too_large")
        self.assertEqual(self.Identity.search_count([]), before)

    def test_07_nullable_fields_accepted(self):
        payload = self._payload(username="uji.null", company_code=None, role_id=None, full_name="")
        identity = self._identity(self._provision(payload))
        self.assertEqual(identity.esb_role_id, 0)
        self.assertEqual(self._user(identity).name, "uji.null", "nama kosong -> username")

    # ================================================================== pemanggil
    def test_08_caller_without_group_or_with_extra_group_denied(self):
        extra = new_test_user(self.env, login="efn_esb_prov_extra", groups=f"{PROV_GROUPS},base.group_erp_manager")
        for user in (self.plain, self.mgr_pb, self.superadmin, extra):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self._provision(self._payload(username="uji.denied"), user=user)
        self.assertFalse(self.Identity.search([("esb_username", "=", "uji.denied")]))

    def test_09_technical_user_data(self):
        tech = self.env.ref("efn_esb_identity.user_esb_provisioner")
        self.assertTrue(tech.active)
        self.assertFalse(tech.share)
        self.assertEqual(set(tech.group_ids.ids), {self.group_user.id, self.group_prov.id})
        self.assertEqual(len(tech.company_ids), 1)
        ApiKeys = self.env["res.users.apikeys"].sudo()
        self.assertFalse(ApiKeys.search_count([("user_id", "=", tech.id)]), "API key tidak di data")
        self.assertTrue(self._password_is_null(tech))
        result = self._provision(self._payload(username="uji.tech"), user=tech)
        self.assertTrue(result["created"])
        # Provisioner tanpa ACL baca identitas (tanpa enumerasi username lewat search_read).
        with self.assertRaises(AccessError):
            self.Identity.with_user(tech).search_read([], ["esb_username"])

    def test_09a_technical_user_data_with_archived_main_company(self):
        """Regresi efn-prod 25 Nov 2026: base.main_company diarsipkan -> install gagal (company tidak diizinkan)."""
        from odoo.tools import convert_file  # noqa: PLC0415

        main = self.env.ref("base.main_company")
        other = self.env["res.company"].search([("id", "!=", main.id)], order="id", limit=1)
        if not other:
            other = self.env["res.company"].create({"name": "PT Uji Aktif"})
        old = self.env.ref("efn_esb_identity.user_esb_provisioner")
        old.login = "efn_esb_provisioner_lama"
        self.env["ir.model.data"].search(
            [("module", "=", "efn_esb_identity"), ("name", "=", "user_esb_provisioner")]
        ).unlink()
        self.env.user.company_ids |= other
        self.env.user.company_id = other
        # Tiru kondisi DB EFN apa adanya (company bawaan arsip); ORM menolak arsip selama user lain memakainya.
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_company SET active = FALSE WHERE id = %s", [main.id])
        self.env.invalidate_all()

        convert_file(self.env, "efn_esb_identity", "data/efn_esb_identity_data.xml", {}, mode="init", noupdate=True)

        tech = self.env.ref("efn_esb_identity.user_esb_provisioner")
        self.assertNotEqual(tech, old)
        first_active = self.env["res.company"].search([], order="id", limit=1)
        self.assertEqual(tech.company_id, first_active)
        self.assertEqual(tech.company_ids, first_active)
        self.assertTrue(tech.company_id.active)
        self.assertEqual(set(tech.group_ids.ids), {self.group_user.id, self.group_prov.id})

    # ================================================================== bentrok / company
    def test_10_login_conflict_with_existing_user_rejected(self):
        existing = self.env["res.users"].create({"name": "User Asli Uji", "login": "esb:pb:uji.konflik"})
        with self.assertRaises(UserError) as cm:
            self._provision(self._payload(username="uji.konflik"))
        self.assertEqual(cm.exception.args[0], "efn_esb_identity:esb_provision_login_conflict")
        self.assertTrue(existing.active)
        self.assertEqual(existing.name, "User Asli Uji")
        linked = self.Identity.search([("user_id", "=", existing.id)])
        self.assertFalse(linked, "tidak pernah menaut user yang ada")

    def test_11_no_matching_branch_rejected(self):
        cases = (
            {"branch_ids": [9799]},
            {"branch_ids": []},
            {"branch_ids": [9703]},  # cabang arsip
            {"esb_account": "wtz", "branch_ids": [9701]},  # cabang akun lain
        )
        for over in cases:
            payload = self._payload(username="uji.nocompany", **over)
            with self.subTest(case=repr(over)), self.assertRaises(UserError) as cm:
                self._provision(payload)
            self.assertEqual(cm.exception.args[0], "efn_esb_identity:esb_provision_no_company")

    def test_12_multi_company_bacha_and_record_rule(self):
        pb_identity = self._identity(self._provision(self._payload()))
        payload = self._payload(esb_account="bacha", username="uji.bacha", company_code=None, branch_ids=[9712, 9711])
        bacha = self._identity(self._provision(payload))
        user = self._user(bacha)
        self.assertEqual(user.company_ids, self.bcf | self.bcr)
        self.assertEqual(user.company_id, self.bcf, "PT pertama (id terkecil)")
        self.assertEqual(bacha.company_id, self.bcf)
        self.assertEqual(set(user.group_ids.ids), {self.group_user.id, self.group_multi.id})
        self.assertEqual(user.login, "esb:bacha:uji.bacha")
        visible = self.Identity.with_user(self.mgr_pb).search([])
        self.assertIn(pb_identity, visible)
        self.assertNotIn(bacha, visible, "record rule multi-company")

    def test_13_same_username_other_account_is_separate_identity(self):
        first = self._provision(self._payload())
        other = self._provision(self._payload(esb_account="bacha", branch_ids=[9711]))
        self.assertNotEqual(first["identity_id"], other["identity_id"])
        self.assertTrue(other["created"])

    # ================================================================== autentikasi
    def test_14_shadow_user_cannot_authenticate(self):
        identity = self._identity(self._provision(self._payload()))
        login = self._user(identity).login
        for password in ("", "apa-saja-Uji-123"):
            credential = {"type": "password", "login": login, "password": password}
            with self.subTest(password=bool(password)), self.assertRaises(AccessDenied):
                self.env["res.users"].authenticate(credential, {"interactive": False})

    # ================================================================== cron stale
    def test_15_stale_cron_revokes_without_deleting(self):
        stale = self._identity(self._provision(self._payload()))
        fresh = self._identity(self._provision(self._payload(username="uji.fresh")))
        now = fields.Datetime.now()
        stale.write({"last_login_at": now - timedelta(days=STALE_LOGIN_DAYS + 1)})
        self.Identity._cron_efn_esb_mark_stale(now=now)
        self.assertEqual(stale.state, "revoked")
        self.assertFalse(stale.esb_pp_roles)
        self.assertFalse(stale.esb_branch_ids)
        self.assertTrue(self._user(stale).exists(), "user tidak dihapus")
        self.assertFalse(self._user(stale).active)
        self.assertEqual(fresh.state, "shadow")
        again = self._provision(self._payload())
        self.assertEqual((again["identity_id"], again["created"]), (stale.id, False))
        self.assertEqual(stale.state, "shadow")
        self.assertEqual(stale.esb_pp_roles, "pp_planner")

    # ================================================================== aktivasi
    def test_16_activation_superadmin_only_with_prerequisites(self):
        identity = self._identity(self._provision(self._payload()))
        user = self._user(identity)
        with self.assertRaises(AccessError):
            identity.with_user(self.mgr_pb).action_efn_activate()
        with self.assertRaises(UserError):
            identity.with_user(self.superadmin).action_efn_activate()
        work = "planner.pb.uji@example.test"
        user.sudo().write({"login": work, "email": work})
        identity.with_user(self.superadmin).action_efn_activate()
        self.assertEqual(identity.state, "activated")
        self.assertTrue(identity.activated_at)
        with self.assertRaises(UserError):
            identity.with_user(self.superadmin).action_efn_activate()

        # Setelah aktivasi: unarchive standar; login ESB berikutnya hanya memperbarui cermin.
        user.sudo().write({"active": True})
        result = self._provision(self._payload(pp_roles=["pp_warehouse"], full_name="Nama ESB Lain"))
        self.assertEqual((result["identity_id"], result["created"]), (identity.id, False))
        self.assertTrue(user.active)
        self.assertEqual(user.login, work)
        self.assertNotEqual(user.name, "Nama ESB Lain")
        self.assertEqual(identity.esb_pp_roles, "pp_warehouse")
        self.assertEqual(identity.state, "activated")

    def test_17_identity_never_deleted_or_created_manually(self):
        identity = self._identity(self._provision(self._payload()))
        with self.assertRaises(AccessError):
            identity.with_user(self.superadmin).unlink()
        vals = {"user_id": self.plain.id, "company_id": self.pb.id, "esb_account": "pb", "esb_username": "manual"}
        with self.assertRaises(AccessError):
            self.Identity.with_user(self.superadmin).create(vals)

    # ================================================================== efn_role
    def test_18_provisioner_group_is_technical_in_efn_role(self):
        if "efn.role" not in self.env:
            self.skipTest("efn_role tidak terpasang")
        from odoo.addons.efn_role.models.role_policy import TECHNICAL_GROUP_XMLIDS

        self.assertIn(GROUP_PROVISIONER, TECHNICAL_GROUP_XMLIDS)
        vals = {"name": "Peran Uji Prov", "company_id": self.pb.id, "group_ids": [Command.set(self.group_prov.ids)]}
        with self.assertRaises(ValidationError):
            self.env["efn.role"].create(vals)

    # ================================================================== log PDP
    def test_19_log_without_username_or_name(self):
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self._provision(self._payload(username="uji.logpii", full_name="Nama Rahasia Uji"))
        output = "\n".join(logs.output)
        self.assertIn("esb_provision identity=", output)
        self.assertNotIn("uji.logpii", output)
        self.assertNotIn("Nama Rahasia", output)
