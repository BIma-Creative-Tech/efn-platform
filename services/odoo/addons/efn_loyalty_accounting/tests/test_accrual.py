"""Akrual harian Poin EFN per PT + rekonsiliasi (docs/04 §13.1 no. 6-7, L-61). BFF di-mock; angka = data uji."""

import json
from datetime import date

from odoo.exceptions import AccessError
from odoo.fields import Command
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.efn_core.models.efn_bff_client import EfnBffError
from odoo.addons.efn_loyalty_accounting.tests.common import ACCOUNT_MANAGER, FakeBffMixin, assign_brand_code, make_user

ACCRUAL_LOGGER = "odoo.addons.efn_loyalty_accounting.models.efn_loyalty_accrual"
RECON_LOGGER = "odoo.addons.efn_loyalty_accounting.recon"
DAY = date(2026, 10, 18)
DAY_STR = "2026-10-18"
ACCOUNT_FIELDS = (
    "efn_loyalty_liability_account_id",
    "efn_loyalty_expense_account_id",
    "efn_loyalty_clearing_account_id",
    "efn_loyalty_breakage_account_id",
    "efn_loyalty_interco_payable_account_id",
)


@tagged("post_install", "-at_install", "efn")
class TestEfnLoyaltyAccrual(FakeBffMixin, AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gl = cls.company_data["company"]
        cls.company_data_2 = cls.setup_other_company(name="PT PB Uji Akrual Poin")
        cls.pb = cls.company_data_2["company"]
        cls.company_data_3 = cls.setup_other_company(name="PT BCR Uji Akrual Poin")
        cls.bcr = cls.company_data_3["company"]
        assign_brand_code(cls.env, cls.gl, "GL")
        assign_brand_code(cls.env, cls.pb, "PB")
        assign_brand_code(cls.env, cls.bcr, "BCR")
        cls.acc = {
            company.id: cls._make_accounts(company, prefix)
            for company, prefix in ((cls.gl, "GL"), (cls.pb, "PB"), (cls.bcr, "BCR"))
        }
        for company in (cls.gl, cls.pb, cls.bcr):
            company.sudo().write(cls.acc[company.id])
        cls.am_gl = make_user(cls.env, "efn_acr_am_gl", [ACCOUNT_MANAGER], [cls.gl])
        cls.am_pb = make_user(cls.env, "efn_acr_am_pb", [ACCOUNT_MANAGER], [cls.pb])

    @classmethod
    def _make_accounts(cls, company, prefix):
        Account = cls.env["account.account"].sudo().with_company(company)
        specs = (
            ("efn_loyalty_liability_account_id", "liability_current", "Liabilitas Poin EFN uji"),
            ("efn_loyalty_expense_account_id", "expense", "Beban Program Poin uji"),
            ("efn_loyalty_clearing_account_id", "asset_current", "Kliring Poin Antar-PT uji"),
            ("efn_loyalty_breakage_account_id", "income", "Pendapatan Poin Kedaluwarsa uji"),
            ("efn_loyalty_interco_payable_account_id", "liability_current", "Utang Poin Antar-PT uji"),
        )
        values = {}
        for index, (field_name, account_type, name) in enumerate(specs, start=1):
            account = Account.create(
                {
                    "name": f"{name} {prefix}",
                    "code": f"LOY{prefix}{index}",
                    "account_type": account_type,
                    "company_ids": [Command.set(company.ids)],
                }
            )
            values[field_name] = account.id
        return values

    def setUp(self):
        super().setUp()
        self.install_fake_bff()
        self.Accrual = self.env["efn.loyalty.accrual"].sudo()

    # ------------------------------------------------------------ helpers
    def accrual(
        self,
        code,
        earned=0,
        reversed_=0,
        by_issuer=None,
        restored=0,
        expired=0,
        closing=0,
        day=DAY_STR,
        as_issuer_redeemed=None,
        as_issuer_restored=None,
    ):
        """Respons akrual BFF. Field L-66 hanya disertakan bila diberikan (BFF lama tanpa field = map kosong)."""
        extra = {}
        if as_issuer_redeemed is not None:
            extra["redeemed_value_idr_as_issuer_by_redeemer"] = as_issuer_redeemed
        if as_issuer_restored is not None:
            extra["restored_value_idr_as_issuer_by_redeemer"] = as_issuer_restored
        return self.ok(
            {
                **extra,
                "company_code": code,
                "date": day,
                "earned_value_idr": earned,
                "earn_reversed_value_idr": reversed_,
                "redeemed_value_idr_by_issuer": by_issuer or {},
                "restored_value_idr": restored,
                "expired_value_idr": expired,
                "closing_liability_idr": closing,
                "generated_at": "2026-10-18T19:00:05Z",
            }
        )

    def _run(self, company, day=DAY):
        return self.Accrual._efn_run(company, day)

    def _moves(self, company, day_str=DAY_STR):
        ref = f"EFN-LOY-{company.efn_brand_code}-{day_str}"
        return self.env["account.move"].sudo().search([("company_id", "=", company.id), ("ref", "=", ref)])

    def assertRecon(self, logs, level, expected):
        """Format mengikat: ``EFN_LOYALTY_RECON `` + ``json.dumps(obj, sort_keys=True)``, tepat 5 kunci, int IDR."""
        recon = [record for record in logs.records if record.getMessage().startswith("EFN_LOYALTY_RECON ")]
        self.assertEqual(len(recon), 1, [r.getMessage() for r in logs.records])
        message = recon[0].getMessage()
        self.assertEqual(recon[0].levelname, level)
        self.assertEqual(message, "EFN_LOYALTY_RECON " + json.dumps(expected, sort_keys=True))
        payload = json.loads(message.split(" ", 1)[1])
        self.assertEqual(
            set(payload), {"bff_closing_liability_idr", "company_code", "date", "diff_idr", "odoo_liability_idr"}
        )
        for key in ("bff_closing_liability_idr", "diff_idr", "odoo_liability_idr"):
            self.assertIs(type(payload[key]), int, key)
        self.assertEqual(payload["diff_idr"], payload["odoo_liability_idr"] - payload["bff_closing_liability_idr"])
        self.assertRegex(payload["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertNotRegex(message, r"62\d{8,}")

    def _sums(self, move, field_name):
        account_id = self.acc[move.company_id.id][field_name]
        lines = move.line_ids.filtered(lambda line: line.account_id.id == account_id)
        return sum(lines.mapped("debit")), sum(lines.mapped("credit"))

    # -------------------------------------------------------------- jurnal
    def test_accrual_journal_lines_recon_ok_and_idempotent(self):
        by_issuer = {"GL": 3_000, "PB": 1_500}
        self.bff_responses = [
            self.accrual("GL", earned=10_000, reversed_=2_000, by_issuer=by_issuer, restored=500, closing=9_500)
        ]
        with self.assertLogs(RECON_LOGGER, "INFO") as logs:
            record = self._run(self.gl)
        self.assertEqual(
            self.bff_calls[0]["params"], {"company_code": "GL", "date": DAY_STR}, "query kontrak §13.1 no. 6"
        )
        self.assertEqual(self.bff_calls[0]["path"], "/v1/pos/loyalty/accruals")
        move = self._moves(self.gl)
        self.assertEqual(len(move), 1)
        self.assertEqual((move.state, move.ref, move.date), ("posted", "EFN-LOY-GL-2026-10-18", DAY))
        self.assertEqual(self._sums(move, "efn_loyalty_expense_account_id"), (10_000, 2_000))
        self.assertEqual(self._sums(move, "efn_loyalty_liability_account_id"), (2_000, 11_500))
        # Tukar poin PT sendiri (GL 3.000) TIDAK dijurnal di sini (sudah lewat POS); lintas PT (PB 1.500) -> kliring.
        self.assertEqual(self._sums(move, "efn_loyalty_clearing_account_id"), (1_500, 0))
        self.assertEqual((record.state, record.move_id), ("done", move))
        self.assertEqual((record.redeemed_same_value_idr, record.redeemed_cross_value_idr), (3_000, 1_500))
        self.assertEqual((record.ledger_liability_idr, record.recon_status, record.recon_diff_idr), (9_500, "ok", 0))
        expected = {
            "bff_closing_liability_idr": 9_500,
            "company_code": "GL",
            "date": DAY_STR,
            "diff_idr": 0,
            "odoo_liability_idr": 9_500,
        }
        self.assertRecon(logs, "INFO", expected)
        # Idempoten: run ulang tidak memanggil BFF (final) dan tidak membuat jurnal kedua, tetapi baris RECON tetap
        # dikirim (dead-man: satu baris per PT per tanggal setiap run).
        with self.assertLogs(RECON_LOGGER, "INFO") as again:
            self._run(self.gl)
        self.assertRecon(again, "INFO", expected)
        self.assertEqual(len(self.bff_calls), 1)
        # Record akrual hilang (mis. dihapus admin) -> jurnal ber-ref sama ditemukan dan dipakai ulang.
        record.unlink()
        self._run(self.gl)
        self.assertEqual(len(self.bff_calls), 2)
        self.assertEqual(len(self._moves(self.gl)), 1)
        relinked = self.Accrual.search([("company_id", "=", self.gl.id), ("date", "=", DAY)])
        self.assertEqual((relinked.state, relinked.move_id), ("done", move))

    def test_per_company_accounts_and_cross_company_clearing(self):
        self.bff_responses = [self.accrual("PB", earned=4_000, by_issuer={"GL": 700}, closing=4_700)]
        self._run(self.pb)
        move = self._moves(self.pb)
        self.assertEqual(move.company_id, self.pb)
        self.assertEqual(set(move.line_ids.account_id.ids) - set(self.acc[self.pb.id].values()), set())
        self.assertEqual(self._sums(move, "efn_loyalty_expense_account_id"), (4_000, 0))
        self.assertEqual(self._sums(move, "efn_loyalty_clearing_account_id"), (700, 0))
        self.assertEqual(self._sums(move, "efn_loyalty_liability_account_id"), (0, 4_700))
        self.assertIn("GL", " ".join(move.line_ids.mapped("name")), "label kliring menyebut PT penerbit")
        self.assertFalse(self._moves(self.gl), "PT lain tidak tersentuh")

    # --------------------------------------------------- L-66 lintas PT
    def _pos_redeem(self, company, value, day=DAY):
        """Simulasi jurnal sesi POS penukaran Poin EFN di PT penebus (§9.2): Dr Liabilitas (outstanding metode Poin
        EFN) / Cr lawan. Angka uji."""
        data = {self.gl.id: self.company_data, self.bcr.id: self.company_data_3}[company.id]
        move = (
            self.env["account.move"]
            .sudo()
            .with_company(company)
            .create(
                {
                    "move_type": "entry",
                    "company_id": company.id,
                    "journal_id": data["default_journal_misc"].id,
                    "date": day,
                    "ref": "POS uji tukar Poin EFN",
                    "line_ids": [
                        Command.create(
                            {
                                "name": "Poin EFN",
                                "account_id": self.acc[company.id]["efn_loyalty_liability_account_id"],
                                "debit": value,
                            }
                        ),
                        Command.create(
                            {"name": "Lawan POS", "account_id": data["default_account_revenue"].id, "credit": value}
                        ),
                    ],
                }
            )
        )
        move.action_post()
        return move

    def test_cross_company_earn_gl_redeem_bcr_both_reconcile_zero(self):
        """L-66: earn 10.000 di GL, 3.000 ditukar di BCR, 500 dari penukaran itu dikembalikan (sisi penerbit).
        Liabilitas GL = 10.000 - 3.000 + 500 = 7.500 (= closing BFF GL); liabilitas BCR = 0 (POS mendebit 3.000,
        akrual kliring mengkredit 3.000; BCR tidak menerbitkan poin) -> keduanya selisih 0."""
        # Sisi PT penebus BCR: POS mendebit liabilitas BCR 3.000; akrual BCR: Dr Piutang/Kliring / Cr Liabilitas.
        self._pos_redeem(self.bcr, 3_000)
        self.bff_responses = [self.accrual("BCR", by_issuer={"GL": 3_000}, closing=0)]
        with self.assertLogs(RECON_LOGGER, "INFO") as logs_bcr:
            bcr_record = self._run(self.bcr)
        bcr_move = self._moves(self.bcr)
        self.assertEqual(self._sums(bcr_move, "efn_loyalty_clearing_account_id"), (3_000, 0))
        self.assertEqual(self._sums(bcr_move, "efn_loyalty_liability_account_id"), (0, 3_000))
        self.assertRecon(
            logs_bcr,
            "INFO",
            {
                "bff_closing_liability_idr": 0,
                "company_code": "BCR",
                "date": DAY_STR,
                "diff_idr": 0,
                "odoo_liability_idr": 0,
            },
        )
        self.assertEqual(bcr_record.recon_status, "ok")
        # Sisi PT penerbit GL: earn + liabilitas berpindah ke utang antar-PT untuk penukaran di BCR; restore kebalikan.
        self.bff_responses = [
            self.accrual(
                "GL",
                earned=10_000,
                closing=7_500,
                as_issuer_redeemed={"BCR": 3_000},
                as_issuer_restored={"BCR": 500},
            )
        ]
        with self.assertLogs(RECON_LOGGER, "INFO") as logs_gl:
            gl_record = self._run(self.gl)
        gl_move = self._moves(self.gl)
        self.assertEqual(self._sums(gl_move, "efn_loyalty_interco_payable_account_id"), (500, 3_000))
        self.assertEqual(self._sums(gl_move, "efn_loyalty_liability_account_id"), (3_000, 10_500))
        self.assertIn("BCR", " ".join(gl_move.line_ids.mapped("name")), "label menyebut PT penebus")
        self.assertRecon(
            logs_gl,
            "INFO",
            {
                "bff_closing_liability_idr": 7_500,
                "company_code": "GL",
                "date": DAY_STR,
                "diff_idr": 0,
                "odoo_liability_idr": 7_500,
            },
        )
        self.assertEqual(
            (gl_record.issuer_redeemed_value_idr, gl_record.issuer_restored_value_idr, gl_record.recon_status),
            (3_000, 500, "ok"),
        )
        # Utang GL ke BCR (2.500) = piutang BCR dari GL untuk penukaran bersih (settlement W-15).
        expected_map = {"redeemed": {"BCR": 3_000}, "restored": {"BCR": 500}}
        self.assertEqual(json.loads(gl_record.issuer_by_redeemer), expected_map)

    def test_as_issuer_fields_optional_and_validated(self):
        # BFF lama tanpa field L-66 -> map kosong (test lain juga tanpa field).
        self.bff_responses = [self.accrual("GL", earned=100, closing=100)]
        record = self._run(self.gl)
        self.assertEqual((record.state, record.issuer_redeemed_value_idr), ("done", 0))
        for bad in ({"GL": 1}, {"XX": 1}, {"BCR": -1}, {"BCR": "1"}, ["BCR"]):
            with self.subTest(bad=bad):
                self.Accrual.search([("company_id", "=", self.gl.id)]).unlink()
                self.bff_responses = [self.accrual("GL", earned=100, closing=100, as_issuer_redeemed=bad)]
                with self.assertLogs(ACCRUAL_LOGGER, "WARNING"):
                    record = self._run(self.gl)
                self.assertEqual((record.state, record.last_error), ("error", "bad_response"))

    def test_missing_interco_payable_account_no_journal(self):
        self.gl.sudo().write({"efn_loyalty_interco_payable_account_id": False})
        self.bff_responses = [self.accrual("GL", earned=1_000, closing=700, as_issuer_redeemed={"BCR": 300})]
        with self.assertLogs(ACCRUAL_LOGGER, "ERROR"):
            record = self._run(self.gl)
        self.assertEqual((record.state, record.last_error), ("missing_config", "missing_accounts:interco_payable"))
        self.assertFalse(self._moves(self.gl))
        self.assertTrue(record.activity_ids)

    def test_missing_account_no_journal_activity_then_retry(self):
        self.pb.sudo().write({"efn_loyalty_clearing_account_id": False})
        self.bff_responses = [self.accrual("PB", earned=1_000, by_issuer={"GL": 200}, closing=1_200)]
        with self.assertLogs(ACCRUAL_LOGGER, "ERROR") as logs:
            record = self._run(self.pb)
        self.assertEqual((record.state, record.last_error), ("missing_config", "missing_accounts:clearing"))
        self.assertFalse(self._moves(self.pb))
        self.assertTrue(record.activity_ids, "aktivitas Finance")
        self.assertNotIn("1000", " ".join(logs.output), "log galat tanpa angka transaksi")
        with self.assertRaises(AccessError):
            record.with_user(self.am_gl).action_efn_retry()  # Accounting Manager PT lain
        clearing = self.acc[self.pb.id]["efn_loyalty_clearing_account_id"]
        self.pb.sudo().write({"efn_loyalty_clearing_account_id": clearing})
        record.with_user(self.am_pb).action_efn_retry()
        self.assertEqual(record.state, "done")
        self.assertEqual(len(self._moves(self.pb)), 1)

    def test_zero_values_need_no_accounts(self):
        self.pb.sudo().write(dict.fromkeys(ACCOUNT_FIELDS, False))
        self.bff_responses = [self.accrual("PB", by_issuer={"PB": 900})]
        with self.assertLogs(RECON_LOGGER, "INFO") as logs, self.assertLogs(ACCRUAL_LOGGER, "WARNING"):
            record = self._run(self.pb)
        self.assertEqual((record.state, record.recon_status), ("no_entry", "not_run"))
        self.assertFalse(self._moves(self.pb))
        expected = {
            "bff_closing_liability_idr": 0,
            "company_code": "PB",
            "date": DAY_STR,
            "diff_idr": 0,
            "odoo_liability_idr": 0,
        }
        self.assertRecon(logs, "INFO", expected)

    # -------------------------------------------------------------- recon
    def test_recon_mismatch_logged_without_pii_and_activity(self):
        self.bff_responses = [self.accrual("GL", earned=10_000, closing=9_000)]
        with self.assertLogs(RECON_LOGGER, "WARNING") as logs:
            record = self._run(self.gl)
        expected = {
            "bff_closing_liability_idr": 9_000,
            "company_code": "GL",
            "date": DAY_STR,
            "diff_idr": 1_000,
            "odoo_liability_idr": 10_000,
        }
        self.assertRecon(logs, "WARNING", expected)
        self.assertEqual((record.recon_status, record.recon_diff_idr), ("mismatch", 1_000))
        self.assertTrue(record.activity_ids)
        self.assertEqual(record.state, "done", "tidak ada koreksi otomatis")

    # ----------------------------------------------------- galat BFF
    def test_bff_errors_and_bad_response_make_no_journal(self):
        cases = (
            (EfnBffError("bff_timeout"), "bff:bff_timeout"),
            (self.ok({"error": {"code": "not_ready"}}, status=409), "bff:not_ready"),
            (self.accrual("GL", earned="10000"), "bad_response"),
            (self.accrual("GL", earned=-5), "bad_response"),
            (self.accrual("GL", earned=True), "bad_response"),
            (self.accrual("PB", earned=1), "bad_response"),  # company_code salah
            (self.accrual("GL", by_issuer={"XX": 1}), "bad_response"),  # PT tidak dikenal
            (self.accrual("GL", day="2026-10-17"), "bad_response"),
        )
        for response, code in cases:
            with self.subTest(code=code):
                self.Accrual.search([("company_id", "=", self.gl.id)]).unlink()
                self.bff_responses = [response]
                with self.assertLogs(ACCRUAL_LOGGER, "WARNING"):
                    record = self._run(self.gl)
                self.assertEqual((record.state, record.last_error), ("error", code))
                self.assertFalse(self._moves(self.gl))

    def test_breakage_prepared_but_disabled_by_default(self):
        self.bff_responses = [self.accrual("GL", expired=300, closing=0)]
        self._run(self.gl)
        self.assertFalse(self._moves(self.gl), "breakage belum dijurnal (W-17, G22)")
        self.env["ir.config_parameter"].sudo().set_param("efn.loyalty.breakage_journal_enabled", "True")
        self.bff_responses = [self.accrual("GL", expired=300, closing=-300, day="2026-10-17")]
        self._run(self.gl, date(2026, 10, 17))
        move = self._moves(self.gl, "2026-10-17")
        self.assertEqual(self._sums(move, "efn_loyalty_breakage_account_id"), (0, 300))
        self.assertEqual(self._sums(move, "efn_loyalty_liability_account_id"), (300, 0))

    # ---------------------------------------------------- akses & cron
    def test_record_rule_and_read_only_acl(self):
        self.bff_responses = [self.accrual("GL", earned=100, closing=100)]
        record = self._run(self.gl)
        Accrual = self.env["efn.loyalty.accrual"]
        self.assertFalse(Accrual.with_user(self.am_pb).search([("id", "=", record.id)]))
        self.assertEqual(Accrual.with_user(self.am_gl).search([("id", "=", record.id)]), record)
        with self.assertRaises(AccessError):
            record.with_user(self.am_gl).write({"state": "pending"})
        with self.assertRaises(AccessError):
            record.with_user(self.am_gl).unlink()

    def test_cron_dates_and_run_all_companies(self):
        Accrual = self.env["efn.loyalty.accrual"]
        self.assertEqual(
            Accrual._efn_accrual_dates(today=date(2026, 10, 19), lookback=3),
            [date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18)],
        )
        self.env["ir.config_parameter"].sudo().set_param("efn.loyalty.accrual_lookback_days", "1")

        def zero(_method, _path, _body, params):
            return self.accrual(params["company_code"], day=params["date"])

        self.bff_responses = [zero]
        coded = self.env["res.company"].sudo().search_count([("efn_brand_code", "!=", False)])
        self.Accrual._efn_run_all(today=date(2026, 10, 19))
        self.assertEqual(len(self.bff_calls), coded)
        self.assertEqual({call["params"]["date"] for call in self.bff_calls}, {DAY_STR})
        states = self.Accrual.search([("date", "=", DAY)]).mapped("state")
        self.assertEqual(set(states), {"no_entry"})
