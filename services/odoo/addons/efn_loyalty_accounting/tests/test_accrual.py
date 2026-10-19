"""Akrual harian Poin EFN per PT + rekonsiliasi (docs/04 §13.1 no. 6-7, L-61). BFF di-mock; angka = data uji."""

import json
from datetime import date

from odoo.exceptions import AccessError, UserError
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
        forfeited=None,
        restored_by_issuer=None,
    ):
        """Respons akrual BFF. Field L-66/L-67/L-68 hanya disertakan bila diberikan (BFF lama tanpa field = map
        kosong / 0)."""
        extra = {}
        if as_issuer_redeemed is not None:
            extra["redeemed_value_idr_as_issuer_by_redeemer"] = as_issuer_redeemed
        if as_issuer_restored is not None:
            extra["restored_value_idr_as_issuer_by_redeemer"] = as_issuer_restored
        if forfeited is not None:
            extra["forfeited_value_idr"] = forfeited
        if restored_by_issuer is not None:
            extra["restored_value_idr_by_issuer"] = restored_by_issuer
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
        # SEC-156: jurnal tertaut sistem; akrual berjurnal tidak bisa dihapus (termasuk sudo).
        self.assertEqual(move.efn_loyalty_accrual_id, record)
        with self.assertRaises(UserError):
            record.unlink()

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
    def _pos_redeem(self, company, value, day=DAY, restore=False):
        """Simulasi jurnal sesi POS penukaran Poin EFN di PT penebus (§9.2): Dr Liabilitas (outstanding metode Poin
        EFN) / Cr lawan. ``restore=True`` = refund POS (pembayaran Poin EFN negatif): Cr Liabilitas / Dr lawan.
        Angka uji."""
        data = {self.gl.id: self.company_data, self.bcr.id: self.company_data_3}[company.id]
        liability_side, other_side = ("credit", "debit") if restore else ("debit", "credit")
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
                    "ref": "POS uji refund Poin EFN" if restore else "POS uji tukar Poin EFN",
                    "line_ids": [
                        Command.create(
                            {
                                "name": "Poin EFN",
                                "account_id": self.acc[company.id]["efn_loyalty_liability_account_id"],
                                liability_side: value,
                            }
                        ),
                        Command.create(
                            {"name": "Lawan POS", "account_id": data["default_account_revenue"].id, other_side: value}
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

    def test_cross_company_restore_at_redeemer_both_reconcile_zero(self):
        """L-68: earn 10.000 di GL, 3.000 ditukar di BCR, refund POS di BCR mengembalikan 500.
        BCR: POS Dr Liabilitas 3.000 & Cr 500; akrual Dr Kliring 3.000 / Cr Liabilitas 3.000 dan Dr Liabilitas 500 /
        Cr Kliring 500 -> liabilitas BCR 0 = closing BCR. GL: 10.000 - 3.000 + 500 = 7.500. Keduanya RECON 0."""
        self._pos_redeem(self.bcr, 3_000)
        self._pos_redeem(self.bcr, 500, restore=True)
        self.bff_responses = [
            self.accrual("BCR", by_issuer={"GL": 3_000}, restored=500, restored_by_issuer={"GL": 500}, closing=0)
        ]
        with self.assertLogs(RECON_LOGGER, "INFO") as logs_bcr:
            bcr_record = self._run(self.bcr)
        bcr_move = self._moves(self.bcr)
        self.assertEqual(self._sums(bcr_move, "efn_loyalty_clearing_account_id"), (3_000, 500))
        self.assertEqual(self._sums(bcr_move, "efn_loyalty_liability_account_id"), (500, 3_000))
        self.assertIn("Restore", " ".join(bcr_move.line_ids.mapped("name")))
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
        self.assertEqual(
            (bcr_record.restored_cross_value_idr, json.loads(bcr_record.restored_by_issuer), bcr_record.recon_status),
            (500, {"GL": 500}, "ok"),
        )
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
        self.assertEqual(gl_record.recon_status, "ok")
        # Piutang BCR dari GL (kliring 2.500) = utang GL ke BCR (2.500) -> tereliminasi di konsolidasi (W-15).
        gl_payable = self._sums(self._moves(self.gl), "efn_loyalty_interco_payable_account_id")
        bcr_clearing = self._sums(bcr_move, "efn_loyalty_clearing_account_id")
        self.assertEqual(gl_payable[1] - gl_payable[0], bcr_clearing[0] - bcr_clearing[1])

    def test_as_issuer_fields_optional_and_validated(self):
        # BFF lama tanpa field L-66 -> map kosong (test lain juga tanpa field).
        self.bff_responses = [self.accrual("GL", earned=100, closing=100, day="2026-10-17")]
        record = self._run(self.gl, date(2026, 10, 17))
        self.assertEqual((record.state, record.issuer_redeemed_value_idr), ("done", 0))
        for bad in ({"GL": 1}, {"XX": 1}, {"BCR": -1}, {"BCR": "1"}, ["BCR"]):
            with self.subTest(bad=bad):
                self.Accrual.search([("company_id", "=", self.gl.id), ("date", "=", DAY)]).unlink()
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

    # ------------------------------------------- L-67 kedaluwarsa & forfeit
    def test_expired_and_forfeited_journaled_recon_zero(self):
        """L-67 (SEC-160): earn 10.000 (17 Okt); 18 Okt 300 kedaluwarsa + 200 hangus erasure DSAR -> Dr Liabilitas /
        Cr Pendapatan Poin Kedaluwarsa 500; saldo Odoo 9.500 = closing BFF -> RECON 0."""
        self.bff_responses = [self.accrual("GL", earned=10_000, closing=10_000, day="2026-10-17")]
        with self.assertLogs(RECON_LOGGER, "INFO"):
            self._run(self.gl, date(2026, 10, 17))
        # Parameter lama G21 (breakage_journal_enabled=False) tidak lagi mematikan jurnal.
        self.env["ir.config_parameter"].sudo().set_param("efn.loyalty.breakage_journal_enabled", "False")
        self.bff_responses = [self.accrual("GL", expired=300, forfeited=200, closing=9_500)]
        with self.assertLogs(RECON_LOGGER, "INFO") as logs:
            record = self._run(self.gl)
        move = self._moves(self.gl)
        self.assertEqual(move.state, "posted")
        self.assertEqual(self._sums(move, "efn_loyalty_breakage_account_id"), (0, 500))
        self.assertEqual(self._sums(move, "efn_loyalty_liability_account_id"), (500, 0))
        breakage_id = self.acc[self.gl.id]["efn_loyalty_breakage_account_id"]
        credits = sorted(move.line_ids.filtered(lambda line: line.account_id.id == breakage_id).mapped("credit"))
        self.assertEqual(credits, [200, 300], "kedaluwarsa & forfeit baris terpisah")
        self.assertEqual(
            (record.expired_value_idr, record.forfeited_value_idr, record.recon_status, record.recon_diff_idr),
            (300, 200, "ok", 0),
        )
        expected = {
            "bff_closing_liability_idr": 9_500,
            "company_code": "GL",
            "date": DAY_STR,
            "diff_idr": 0,
            "odoo_liability_idr": 9_500,
        }
        self.assertRecon(logs, "INFO", expected)

    def test_inactivity_expire_breakage_per_issuer_recon_zero(self):
        """G22 W-17 (§14.D.5): entri ``inactivity_expire`` masuk ``expired_value_idr`` PT PENERBIT (bentuk respons
        tidak berubah, tanpa flag). Member punya lot terbitan GL Rp2.500 dan PB Rp4.000 (17 Okt), tidak aktif, semua
        sisa hangus 18 Okt -> masing-masing PT penerbit Dr Liabilitas / Cr Pendapatan Poin Kedaluwarsa, saldo Odoo =
        closing BFF (0) -> RECON 0 di kedua PT."""
        self.bff_responses = [self.accrual("GL", earned=2_500, closing=2_500, day="2026-10-17")]
        with self.assertLogs(RECON_LOGGER, "INFO"):
            self._run(self.gl, date(2026, 10, 17))
        self.bff_responses = [self.accrual("PB", earned=4_000, closing=4_000, day="2026-10-17")]
        with self.assertLogs(RECON_LOGGER, "INFO"):
            self._run(self.pb, date(2026, 10, 17))
        for company, value in ((self.gl, 2_500), (self.pb, 4_000)):
            with self.subTest(company=company.efn_brand_code):
                self.bff_responses = [self.accrual(company.efn_brand_code, expired=value, closing=0)]
                with self.assertLogs(RECON_LOGGER, "INFO") as logs:
                    record = self._run(company)
                move = self._moves(company)
                self.assertEqual(move.state, "posted")
                self.assertEqual(move.company_id, company)
                self.assertEqual(self._sums(move, "efn_loyalty_breakage_account_id"), (0, value))
                self.assertEqual(self._sums(move, "efn_loyalty_liability_account_id"), (value, 0))
                self.assertEqual((record.state, record.recon_status, record.recon_diff_idr), ("done", "ok", 0))
                self.assertRecon(
                    logs,
                    "INFO",
                    {
                        "bff_closing_liability_idr": 0,
                        "company_code": company.efn_brand_code,
                        "date": DAY_STR,
                        "diff_idr": 0,
                        "odoo_liability_idr": 0,
                    },
                )

    def test_forfeit_without_breakage_account_is_missing_config(self):
        self.gl.sudo().write({"efn_loyalty_breakage_account_id": False})
        self.bff_responses = [self.accrual("GL", forfeited=200, closing=-200)]
        with self.assertLogs(ACCRUAL_LOGGER, "ERROR"):
            record = self._run(self.gl)
        self.assertEqual((record.state, record.last_error), ("missing_config", "missing_accounts:breakage"))
        self.assertFalse(self._moves(self.gl))
        self.assertTrue(record.activity_ids)

    def test_forfeit_and_restore_by_issuer_validated(self):
        cases = (
            self.accrual("GL", forfeited=-1),
            self.accrual("GL", forfeited="1"),
            self.accrual("GL", forfeited=True),
            self.accrual("GL", restored=500, restored_by_issuer={"XX": 500}),
            self.accrual("GL", restored=500, restored_by_issuer={"BCR": 400}),  # Σ != restored_value_idr
            self.accrual("GL", restored=500, restored_by_issuer={"BCR": -500}),
            self.accrual("GL", restored=500, restored_by_issuer=["BCR"]),
        )
        for index, response in enumerate(cases):
            with self.subTest(case=index):
                self.Accrual.search([("company_id", "=", self.gl.id), ("date", "=", DAY)]).unlink()
                self.bff_responses = [response]
                with self.assertLogs(ACCRUAL_LOGGER, "WARNING"):
                    record = self._run(self.gl)
                self.assertEqual((record.state, record.last_error), ("error", "bad_response"))
                self.assertFalse(self._moves(self.gl))
        # Bagian restore PT sendiri (GL 200) tidak dijurnal (refund POS); hanya terbitan PT lain (BCR 500).
        self.Accrual.search([("company_id", "=", self.gl.id), ("date", "=", DAY)]).unlink()
        restored_map = {"GL": 200, "BCR": 500}
        self.bff_responses = [self.accrual("GL", restored=700, restored_by_issuer=restored_map, closing=-500)]
        with self.assertLogs(RECON_LOGGER, "INFO"):
            record = self._run(self.gl)
        move = self._moves(self.gl)
        self.assertEqual(self._sums(move, "efn_loyalty_liability_account_id"), (500, 0))
        self.assertEqual(self._sums(move, "efn_loyalty_clearing_account_id"), (0, 500))
        self.assertEqual((record.restored_cross_value_idr, record.recon_status), (500, "ok"))

    # ------------------------------------------------ SEC-156 konflik ref
    def _manual_move(self, ref, state="draft", day=DAY, extra=None):
        """Jurnal manual akuntan di GL ber-ref akrual (skenario SEC-156). Angka uji."""
        move = (
            self.env["account.move"]
            .sudo()
            .with_company(self.gl)
            .create(
                {
                    **(extra or {}),
                    "move_type": "entry",
                    "company_id": self.gl.id,
                    "journal_id": self.company_data["default_journal_misc"].id,
                    "date": day,
                    "ref": ref,
                    "line_ids": [
                        Command.create(
                            {
                                "name": "manual",
                                "account_id": self.acc[self.gl.id]["efn_loyalty_liability_account_id"],
                                "debit": 100,
                            }
                        ),
                        Command.create(
                            {
                                "name": "manual",
                                "account_id": self.company_data["default_account_revenue"].id,
                                "credit": 100,
                            }
                        ),
                    ],
                }
            )
        )
        if state == "posted":
            move.action_post()
        elif state == "cancel":
            move.button_cancel()
        self.assertEqual(move.state, state)
        return move

    def test_ref_conflict_manual_moves_never_double_journal(self):
        """Jurnal manual ber-ref sama (draft/batal/posted) -> conflict + ERROR + aktivitas, tanpa jurnal akrual."""
        cases = ((date(2026, 10, 18), "draft"), (date(2026, 10, 17), "cancel"), (date(2026, 10, 16), "posted"))
        for day, state in cases:
            day_str = day.isoformat()
            with self.subTest(state=state):
                manual = self._manual_move(f"EFN-LOY-GL-{day_str}", state=state, day=day)
                self.bff_responses = [self.accrual("GL", earned=1_000, closing=1_000, day=day_str)]
                with self.assertLogs(ACCRUAL_LOGGER, "ERROR") as logs, self.assertLogs(RECON_LOGGER, "INFO"):
                    record = self._run(self.gl, day)
                self.assertTrue(any(item.levelname == "ERROR" for item in logs.records))
                self.assertEqual((record.state, record.last_error), ("conflict", "ref_conflict:foreign_move"))
                self.assertEqual(self._moves(self.gl, day_str), manual, "tidak ada jurnal ganda")
                self.assertFalse(record.move_id)
                self.assertTrue(record.activity_ids, "aktivitas Finance")

    def test_ref_conflict_not_silent_and_resolved_by_retry(self):
        manual = self._manual_move("EFN-LOY-GL-2026-10-18")
        self.bff_responses = [self.accrual("GL", earned=1_000, closing=1_000)]
        with self.assertLogs(ACCRUAL_LOGGER, "ERROR"), self.assertLogs(RECON_LOGGER, "INFO"):
            record = self._run(self.gl)
        activities = len(record.activity_ids)
        # Run berikutnya: tetap conflict, ERROR lagi (tidak diam), tanpa jurnal ganda, aktivitas tidak berulang.
        with self.assertLogs(ACCRUAL_LOGGER, "ERROR"), self.assertLogs(RECON_LOGGER, "INFO"):
            self._run(self.gl)
        self.assertEqual(record.state, "conflict")
        self.assertEqual(self._moves(self.gl), manual)
        self.assertEqual(len(record.activity_ids), activities)
        # Finance mengganti ref jurnal manual lalu Proses ulang -> jurnal akrual dibuat & ditautkan sistem.
        manual.write({"ref": "Koreksi manual Finance"})
        with self.assertLogs(RECON_LOGGER, "INFO"):
            record.with_user(self.am_gl).action_efn_retry()
        move = self._moves(self.gl)
        self.assertEqual((record.state, record.move_id, move.efn_loyalty_accrual_id), ("done", move, record))
        self.assertEqual(record.recon_status, "ok")

    def test_own_move_reset_to_draft_is_conflict_until_reposted(self):
        self.bff_responses = [self.accrual("GL", earned=1_000, closing=1_000)]
        with self.assertLogs(RECON_LOGGER, "INFO"):
            record = self._run(self.gl)
        move = record.move_id
        move.button_draft()
        with self.assertLogs(ACCRUAL_LOGGER, "ERROR"), self.assertLogs(RECON_LOGGER, "INFO"):
            self._run(self.gl)
        self.assertEqual((record.state, record.last_error), ("conflict", "ref_conflict:move_not_posted"))
        self.assertEqual(len(self.bff_calls), 1)
        # Masih draft: Proses ulang tetap conflict, tidak membuat jurnal kedua.
        with self.assertLogs(ACCRUAL_LOGGER, "ERROR"), self.assertLogs(RECON_LOGGER, "INFO"):
            record.with_user(self.am_gl).action_efn_retry()
        self.assertEqual((record.state, record.move_id), ("conflict", move))
        self.assertEqual(self._moves(self.gl), move)
        move.action_post()
        with self.assertLogs(RECON_LOGGER, "INFO"):
            record.with_user(self.am_gl).action_efn_retry()
        self.assertEqual((record.state, record.move_id, record.recon_status), ("done", move, "ok"))
        self.assertEqual(self._moves(self.gl), move)

    def test_accrual_link_on_move_is_system_only(self):
        self.bff_responses = [self.accrual("GL", earned=1_000, closing=1_000)]
        with self.assertLogs(RECON_LOGGER, "INFO"):
            record = self._run(self.gl)
        move = record.move_id
        for env_move in (
            move.with_user(self.am_gl),
            move.sudo(),
            move.with_user(self.am_gl).with_context(efn_loyalty_accrual_link=True),
        ):
            with self.assertRaises(AccessError):
                env_move.write({"efn_loyalty_accrual_id": False})
        forged = {"efn_loyalty_accrual_id": record.id}
        with self.assertRaises(AccessError):
            self._manual_move("EFN-LOY-GL-2026-10-17", day=date(2026, 10, 17), extra=forged)
        self.assertEqual(move.efn_loyalty_accrual_id, record)

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
