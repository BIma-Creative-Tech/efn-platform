"""G21 W-31 (docs/04 §13.3 no. 1, docs/08 §12): templat departemen & jabatan per PT, tanpa nama orang."""

from odoo.tests import TransactionCase, new_test_user, tagged

MODULE = "efn_setup_org"

# Sufiks xmlid -> xmlid company efn_setup_company (kode entitas 18-41; company_rtl = GL, company_fnb = PB, L-48).
COMPANIES = {
    "efn": "efn_setup_company.company_efn",
    "gl": "efn_setup_company.company_rtl",
    "pb": "efn_setup_company.company_fnb",
    "bcr": "efn_setup_company.company_bcr",
    "bcf": "efn_setup_company.company_bcf",
    "farm": "efn_setup_company.company_farm",
}

DEPARTMENTS = {
    "board": "Direksi",
    "finance": "Keuangan & Akuntansi",
    "store": "Operasional Toko & Outlet",
    "merch": "Merchandising & Pengadaan",
    "warehouse": "Gudang & Logistik",
    "cs": "Layanan Pelanggan (CS)",
    "online": "Penjualan Online & Loyalty",
    "it": "IT",
    "audit": "Audit Internal",
}

# kode jabatan -> (nama, kode departemen). Kode dipakai efn_role (docs/08 §12.2) -> jangan diubah tanpa spesifikasi.
JOBS = {
    "director": ("Direktur", "board"),
    "finance_manager": ("Finance Manager", "finance"),
    "finance_staff": ("Staf Finance", "finance"),
    "store_manager": ("Store Manager", "store"),
    "cashier": ("Kasir", "store"),
    "merchandiser": ("Merchandiser", "merch"),
    "procurement": ("Staf Pengadaan", "merch"),
    "warehouse_staff": ("Staf Gudang", "warehouse"),
    "courier": ("Kurir", "warehouse"),
    "cs_agent": ("Staf CS", "cs"),
    "cs_refund_approver": ("Approver Refund CS", "cs"),
    "sales_manager_online": ("Sales Manager Online", "online"),
    "loyalty_manager": ("Loyalty Manager", "online"),
    "superadmin": ("Superadmin IT", "it"),
    "auditor": ("Auditor Internal", "audit"),
}


@tagged("post_install", "-at_install", "efn")
class TestEfnSetupOrg(TransactionCase):
    def _ref(self, xmlid):
        return self.env.ref(f"{MODULE}.{xmlid}")

    def test_departments_per_company(self):
        for suffix, company_xmlid in COMPANIES.items():
            company = self.env.ref(company_xmlid)
            for code, name in DEPARTMENTS.items():
                with self.subTest(company=suffix, department=code):
                    dept = self._ref(f"dept_{code}_{suffix}")
                    self.assertEqual(dept.name, name)
                    self.assertEqual(dept.company_id, company)
                    self.assertTrue(dept.active)

    def test_jobs_per_company_in_department_of_same_company(self):
        for suffix, company_xmlid in COMPANIES.items():
            company = self.env.ref(company_xmlid)
            for code, (name, dept_code) in JOBS.items():
                with self.subTest(company=suffix, job=code):
                    job = self._ref(f"job_{code}_{suffix}")
                    self.assertEqual(job.name, name)
                    self.assertEqual(job.company_id, company)
                    self.assertEqual(job.department_id, self._ref(f"dept_{dept_code}_{suffix}"))
                    self.assertEqual(job.department_id.company_id, job.company_id)
                    # Templat, bukan lowongan; tanpa user rekruter.
                    self.assertEqual(job.no_of_recruitment, 0)
                    self.assertFalse(job.user_id)

    def test_template_counts(self):
        data = self.env["ir.model.data"].search([("module", "=", MODULE)])
        by_model = {}
        for rec in data:
            by_model.setdefault(rec.model, 0)
            by_model[rec.model] += 1
        self.assertEqual(by_model.get("hr.department"), len(COMPANIES) * len(DEPARTMENTS))
        self.assertEqual(by_model.get("hr.job"), len(COMPANIES) * len(JOBS))

    def test_no_person_data_and_user_editable(self):
        """18-24/18-40: nama orang tidak di data; seluruh data noupdate (boleh diubah pengguna)."""
        data = self.env["ir.model.data"].search([("module", "=", MODULE)])
        self.assertTrue(data)
        self.assertEqual(set(data.mapped("model")), {"hr.department", "hr.job"})
        self.assertTrue(all(data.mapped("noupdate")))
        for suffix in COMPANIES:
            dept = self._ref(f"dept_store_{suffix}")
            self.assertFalse(dept.manager_id)
            self.assertFalse(dept.member_ids)
            self.assertFalse(self._ref(f"job_cashier_{suffix}").employee_ids)

    def test_multi_company_rule_isolates_templates(self):
        """Rule standar hr_dept_comp_rule / hr_job_comp_rule: user PT GL tidak melihat templat PT PB."""
        gl = self.env.ref(COMPANIES["gl"])
        user = new_test_user(
            self.env,
            login="efn_w31_hr_gl",
            groups="base.group_user,hr.group_hr_user",
            company_id=gl.id,
            company_ids=[(6, 0, gl.ids)],
        )
        Dept = self.env["hr.department"].with_user(user)
        Job = self.env["hr.job"].with_user(user)
        visible_depts = Dept.search([("id", "in", [self._ref(f"dept_finance_{s}").id for s in COMPANIES])])
        self.assertEqual(visible_depts, self._ref("dept_finance_gl"))
        visible_jobs = Job.search([("id", "in", [self._ref(f"job_cashier_{s}").id for s in COMPANIES])])
        self.assertEqual(visible_jobs, self._ref("job_cashier_gl"))

    def test_same_job_name_allowed_across_companies(self):
        """Constraint standar unique(name, company_id, department_id): nama sama lintas PT sah."""
        names = {self._ref(f"job_store_manager_{s}").name for s in COMPANIES}
        self.assertEqual(names, {"Store Manager"})
        companies = {self._ref(f"job_store_manager_{s}").company_id.id for s in COMPANIES}
        self.assertEqual(len(companies), len(COMPANIES))
