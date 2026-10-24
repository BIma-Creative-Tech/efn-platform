"""G22 (docs/08 §13.4; matriks §12.2b; L-64, L-67, L-69): peran per PT x jabatan, sinkron hanya peran non-sensitif,
Auditor tanpa ekspor generik, aturan SoD bawaan dirujuk lewat kode (tidak dibuat ulang)."""

from odoo.exceptions import ValidationError
from odoo.fields import Command
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_role.models.role_policy import closure, implied_map, sensitive_group_ids

MODULE = "efn_setup_role"

# Sufiks xmlid -> xmlid company (kode entitas 18-41; company_rtl = GL, company_fnb = PB).
COMPANIES = {
    "efn": "efn_setup_company.company_efn",
    "gl": "efn_setup_company.company_rtl",
    "pb": "efn_setup_company.company_fnb",
    "bcr": "efn_setup_company.company_bcr",
    "bcf": "efn_setup_company.company_bcf",
    "wtz": "efn_setup_company.company_wtz",  # G24 W-40 (docs/04 §16.1)
    "farm": "efn_setup_company.company_farm",
}
POS_COMPANIES = ("gl", "pb", "bcr", "bcf", "wtz")
CK_COMPANIES = ("pb", "bcf")

# kode jabatan (efn_setup_org) -> grup eksplisit peran (docs/08 §12.2b + §13.4).
ROLE_GROUPS = {
    "director": ("account.group_account_readonly",),
    "finance_manager": ("account.group_account_manager", "account.group_account_user"),
    "finance_staff": ("account.group_account_user",),
    "store_manager": ("point_of_sale.group_pos_manager",),
    "cashier": ("point_of_sale.group_pos_user", "efn_role.group_role_cashier"),
    "merchandiser": ("sales_team.group_sale_manager", "stock.group_stock_user"),
    "procurement": ("purchase.group_purchase_user", "stock.group_stock_user"),
    "warehouse_staff": ("stock.group_stock_user",),
    "cs_agent": ("sales_team.group_sale_salesman_all_leads",),
    "cs_refund_approver": ("sales_team.group_sale_salesman_all_leads", "efn_role.group_refund_approver"),
    "sales_manager_online": ("sales_team.group_sale_manager", "stock.group_stock_user"),
    "loyalty_manager": ("efn_loyalty_accounting.group_loyalty_manager",),
    "auditor": ("account.group_account_readonly", "efn_audit.group_auditor"),
}
# Jabatan tanpa peran data: Kurir (tanpa user Odoo), Superadmin IT (peran admin hanya oleh Superadmin lewat UI).
JOBS_WITHOUT_ROLE = ("courier", "superadmin")
# Satu-satunya peran yang boleh disinkron dari jabatan (L-67 + daftar sensitif tambahan).
SYNC_JOBS = {"cashier", "warehouse_staff"}
EXTRA_SENSITIVE = (
    "account.group_account_readonly",
    "account.group_account_user",
    "purchase.group_purchase_user",
    "efn_audit.group_auditor",
    "efn_audit.group_auditor_personal",
    "sales_team.group_sale_salesman_all_leads",
)
# SEC-155 / L-67: tidak ada peran data yang memberi ekspor generik atau hak admin.
FORBIDDEN_FOR_ROLES = ("base.group_allow_export", "base.group_erp_manager", "base.group_system")


@tagged("post_install", "-at_install", "efn")
class TestEfnSetupRole(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Role = cls.env["efn.role"].with_context(active_test=False)
        cls.data_roles = cls.Role.browse(
            cls.env["ir.model.data"].search([("module", "=", MODULE), ("model", "=", "efn.role")]).mapped("res_id")
        )

    def _ref(self, xmlid):
        return self.env.ref(f"{MODULE}.{xmlid}")

    def _groups(self, xmlids):
        return self.env["res.groups"].browse([self.env.ref(x).id for x in xmlids])

    def _closure(self, role):
        return closure(role.group_ids.ids, implied_map(self.env))

    def _user(self, login, company_suffix):
        company = self.env.ref(COMPANIES[company_suffix])
        return new_test_user(
            self.env,
            login=login,
            groups="base.group_user",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )

    def _employee(self, name, user, job_xmlid):
        job = self.env.ref(job_xmlid)
        return self.env["hr.employee"].create(
            {"name": name, "company_id": job.company_id.id, "user_id": user.id, "job_id": job.id}
        )

    def _lines(self, role, user):
        return self.env["efn.role.line"].search([("role_id", "=", role.id), ("user_id", "=", user.id)])

    # ------------------------------------------------------------- matriks
    def test_roles_per_company_and_job_match_matrix(self):
        for suffix, company_xmlid in COMPANIES.items():
            company = self.env.ref(company_xmlid)
            for job_code, xmlids in ROLE_GROUPS.items():
                with self.subTest(pt=suffix, job=job_code):
                    role = self._ref(f"role_{job_code}_{suffix}")
                    self.assertTrue(role.active)
                    self.assertEqual(role.company_id, company)
                    self.assertEqual(role.job_id, self.env.ref(f"efn_setup_org.job_{job_code}_{suffix}"))
                    self.assertEqual(role.job_id.company_id, company)
                    expected = self._groups(xmlids)
                    if job_code == "warehouse_staff" and suffix in CK_COMPANIES:
                        expected |= self.env.ref("mrp.group_mrp_user")
                    self.assertEqual(role.group_ids, expected)

    def test_terminal_pos_roles_only_for_pos_companies(self):
        pos_user = self.env.ref("point_of_sale.group_pos_user")
        for suffix, company_xmlid in COMPANIES.items():
            with self.subTest(pt=suffix):
                role = self.env.ref(f"{MODULE}.role_pos_terminal_{suffix}", raise_if_not_found=False)
                if suffix not in POS_COMPANIES:
                    self.assertFalse(role)
                    continue
                self.assertEqual(role.company_id, self.env.ref(company_xmlid))
                self.assertFalse(role.job_id)
                self.assertEqual(role.group_ids, pos_user)
                self.assertFalse(role.sync_from_job)

    def test_role_counts_and_no_role_for_courier_or_superadmin(self):
        expected = len(COMPANIES) * len(ROLE_GROUPS) + len(POS_COMPANIES)
        self.assertEqual(len(self.data_roles), expected)
        for suffix in COMPANIES:
            for job_code in JOBS_WITHOUT_ROLE:
                with self.subTest(pt=suffix, job=job_code):
                    self.assertFalse(self.env.ref(f"{MODULE}.role_{job_code}_{suffix}", raise_if_not_found=False))
                    job = self.env.ref(f"efn_setup_org.job_{job_code}_{suffix}")
                    self.assertFalse(self.data_roles.filtered(lambda r, j=job: r.job_id == j))
        self.assertFalse(self.data_roles.filtered("is_admin_role"), "peran admin tidak dibuat lewat data")

    def test_one_role_per_company_and_job(self):
        keys = [(r.company_id.id, r.job_id.id) for r in self.data_roles if r.job_id]
        self.assertEqual(len(keys), len(set(keys)))

    def test_data_only_without_assignments_and_noupdate(self):
        data = self.env["ir.model.data"].search([("module", "=", MODULE)])
        self.assertEqual(set(data.mapped("model")), {"efn.role", "ir.config_parameter"})
        self.assertTrue(all(data.mapped("noupdate")))
        self.assertFalse(self.env["efn.role.line"].search([("role_id", "in", self.data_roles.ids)]))

    # ------------------------------------------------------- L-67 / SEC-154
    def test_sync_from_job_only_for_non_sensitive_roles(self):
        sensitive = sensitive_group_ids(self.env)
        flagged = self.data_roles.filtered("sync_from_job")
        self.assertEqual(
            {r.job_id.id for r in flagged},
            {self.env.ref(f"efn_setup_org.job_{job}_{s}").id for job in SYNC_JOBS for s in COMPANIES},
        )
        for role in flagged:
            with self.subTest(role=role.name):
                self.assertFalse(role.is_sensitive_role)
                self.assertFalse(self._closure(role) & sensitive)
        for role in self.data_roles.filtered("is_sensitive_role"):
            with self.subTest(sensitive=role.name):
                self.assertFalse(role.sync_from_job)

    def test_extra_sensitive_groups_parameter(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("efn_role.sensitive_group_xmlids")
        self.assertEqual(set(raw.split(",")), set(EXTRA_SENSITIVE))
        sensitive = sensitive_group_ids(self.env)
        for xmlid in EXTRA_SENSITIVE:
            with self.subTest(group=xmlid):
                self.assertIn(self.env.ref(xmlid).id, sensitive)
        # Peran yang memuat grup tambahan tidak bisa diberi flag sinkron.
        for xmlid in ("role_auditor_gl", "role_cs_agent_gl", "role_finance_staff_gl", "role_procurement_gl"):
            with self.subTest(role=xmlid), self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    self._ref(xmlid).write({"sync_from_job": True})

    # -------------------------------------------------------- SEC-155
    def test_auditor_role_without_generic_export_or_admin(self):
        forbidden = {self.env.ref(x).id for x in (*FORBIDDEN_FOR_ROLES, "efn_audit.group_manager")}
        auditor = self.env.ref("efn_audit.group_auditor").id
        personal = self.env.ref("efn_audit.group_auditor_personal").id
        for suffix in COMPANIES:
            with self.subTest(pt=suffix):
                groups = self._closure(self._ref(f"role_auditor_{suffix}"))
                self.assertIn(auditor, groups)
                self.assertNotIn(personal, groups, "18-38 belum diputuskan: data pribadi lintas PT manual")
                self.assertFalse(groups & forbidden)

    def test_no_data_role_grants_export_or_admin(self):
        forbidden = {self.env.ref(x).id for x in FORBIDDEN_FOR_ROLES}
        graph = implied_map(self.env)
        for role in self.data_roles:
            with self.subTest(role=role.name):
                self.assertFalse(closure(role.group_ids.ids, graph) & forbidden)

    # ------------------------------------------------------------- SoD
    def test_default_sod_rules_referenced_by_code_not_recreated(self):
        Rule = self.env["efn.sod.rule"].sudo().with_context(active_test=False)
        expected = {
            "SOD-01": ("sales_team.group_sale_manager", "efn_role.group_refund_approver", "block"),
            "SOD-02": ("efn_role.group_role_cashier", "point_of_sale.group_pos_manager", "block"),
            "SOD-03": ("efn_loyalty_accounting.group_loyalty_manager", "account.group_account_manager", "block"),
            "SOD-05": ("purchase.group_purchase_user", "account.group_account_manager", "report_only"),
            "SOD-06": ("efn_audit.group_auditor", "base.group_erp_manager", "report_only"),
        }
        for code, (xmlid_a, xmlid_b, mode) in expected.items():
            with self.subTest(code=code):
                rule = Rule.search([("code", "=", code)])
                self.assertEqual(len(rule), 1)
                pair = {rule.group_a_id, rule.group_b_id}
                self.assertEqual(pair, {self.env.ref(xmlid_a), self.env.ref(xmlid_b)})
                # L-69: SOD-03 wajib block. DB bersih tanpa user rangkap -> aturan block tetap block.
                self.assertEqual(rule.mode, mode)
        self.assertFalse(
            self.env["ir.model.data"].search_count([("module", "=", MODULE), ("model", "=", "efn.sod.rule")]),
            "aturan SoD tidak boleh dibuat ulang oleh modul data (dirujuk lewat kode)",
        )

    def test_no_single_role_violates_block_rule(self):
        rules = self.env["efn.sod.rule"].sudo().search([("mode", "=", "block")])
        self.assertTrue(rules)
        graph = implied_map(self.env)
        for role in self.data_roles:
            groups = closure(role.group_ids.ids, graph)
            for rule in rules:
                with self.subTest(role=role.name, rule=rule.code):
                    self.assertFalse(rule.group_a_id.id in groups and rule.group_b_id.id in groups)

    # ---------------------------------------------------- penerapan (sanity)
    def test_cashier_role_applies_and_blocks_store_manager(self):
        gl = self.env.ref(COMPANIES["gl"])
        user = self._user("efn_g22_kasir", "efn")
        Line = self.env["efn.role.line"]
        Line.create({"role_id": self._ref("role_cashier_gl").id, "user_id": user.id})
        self.assertIn(self.env.ref("point_of_sale.group_pos_user"), user.group_ids)
        self.assertIn(self.env.ref("efn_role.group_role_cashier"), user.group_ids)
        self.assertIn(gl, user.company_ids, "peran menambah company PT peran")
        pos_manager = self.env.ref("point_of_sale.group_pos_manager")
        # Grup terkelola / SOD-02: POS Administrator langsung ke kasir ditolak.
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            user.write({"group_ids": [Command.link(pos_manager.id)]})
        # Peran Store Manager untuk user yang sama ditolak (SOD-02 block).
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            Line.create({"role_id": self._ref("role_store_manager_gl").id, "user_id": user.id})

    def test_job_sync_only_for_flagged_role(self):
        user = self._user("efn_g22_gudang", "gl")
        self._employee("Staf Gudang Uji G22", user, "efn_setup_org.job_warehouse_staff_gl")
        role = self._ref("role_warehouse_staff_gl")
        self.Role._efn_sync_job_lines(roles=role, raise_errors=True)
        self.assertEqual(self._lines(role, user).source, "job")
        self.assertIn(self.env.ref("stock.group_stock_user"), user.all_group_ids)
        # Jabatan sensitif (Store Manager) tidak pernah disinkron, walau HR mengisi jabatannya.
        user2 = self._user("efn_g22_store_manager", "gl")
        self._employee("Store Manager Uji G22", user2, "efn_setup_org.job_store_manager_gl")
        manager_role = self._ref("role_store_manager_gl")
        self.Role._efn_sync_job_lines(roles=manager_role)
        self.assertFalse(self._lines(manager_role, user2))
        self.assertNotIn(self.env.ref("point_of_sale.group_pos_manager"), user2.all_group_ids)
