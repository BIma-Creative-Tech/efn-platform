import json
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

MODEL = "odoo.addons.efn_data_ask.models.efn_data_ask"


@tagged("efn", "post_install", "-at_install")
class TestDataAsk(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.c1 = cls.env.company
        cls.c2 = cls.env["res.company"].create({"name": "PT Uji Tanya Data 2"})
        cls.c3 = cls.env["res.company"].create({"name": "PT Uji Tanya Data 3"})
        cls.user = new_test_user(
            cls.env,
            login="efn_t2s_user",
            groups="base.group_user,efn_data_ask.group_data_ask",
            company_id=cls.c1.id,
            company_ids=[(6, 0, [cls.c1.id, cls.c2.id])],
        )
        cls.other = new_test_user(
            cls.env,
            login="efn_t2s_other",
            groups="base.group_user,efn_data_ask.group_data_ask",
            company_id=cls.c1.id,
        )
        cls.plain = new_test_user(cls.env, login="efn_t2s_plain", groups="base.group_user", company_id=cls.c1.id)
        cls.auditor = new_test_user(
            cls.env,
            login="efn_t2s_auditor",
            groups="base.group_user,efn_data_ask.group_data_ask_auditor",
            company_id=cls.c1.id,
        )

    def _ask(self, user=None, companies=None, question="Penjualan per outlet bulan ini", mode=None):
        user = user or self.user
        allowed = [user.company_id.id] + [c for c in user.company_ids.ids if c != user.company_id.id]
        env = self.env(user=user, context={"allowed_company_ids": allowed})
        vals = {"question": question}
        if mode:
            vals["mode"] = mode
        if companies is not None:
            vals["company_ids"] = [(6, 0, companies.ids)]
        return env["efn.data.ask"].create(vals)

    def _run(self, rec, status=200, body=None, enabled="true"):
        body = (
            body
            if body is not None
            else {
                "status": "ok",
                "sql": "SELECT 1",
                "columns": ["brand", "net"],
                "rows": [["PB", 1500.5], ["GL", 200]],
                "row_count": 2,
                "truncated": False,
                "engine": "ninerouter:cc/x",
                "tables": ["efn_t2s.v_sales_daily"],
            }
        )
        with (
            patch.dict("os.environ", {"TEXT2SQL_ENABLED": enabled}),
            patch(f"{MODEL}.EfnDataAsk._efn_text2sql_post", return_value=(status, body)) as post,
        ):
            rec.action_ask()
        return post

    def test_ok_stores_result_and_sends_scope_intersection(self):
        rec = self._ask(companies=self.c1 | self.c2)
        post = self._run(rec)
        payload = post.call_args.args[0]
        self.assertEqual(payload["company_ids"], sorted((self.c1 | self.c2).ids))
        self.assertEqual(payload["question"], "Penjualan per outlet bulan ini")
        self.assertTrue(payload["request_id"].startswith(f"odoo-{rec.id}-"))
        self.assertEqual(rec.state, "done")
        self.assertEqual(rec.row_count, 2)
        self.assertEqual(rec.engine, "ninerouter:cc/x")
        self.assertIn("<td>PB</td>", rec.result_html)
        self.assertIn("1,500.5", rec.result_html)
        self.assertIn("AI", rec.message)

    def test_company_outside_user_access_rejected(self):
        rec = (
            self.env["efn.data.ask"]
            .with_user(self.user)
            .sudo()
            .create(
                {"question": "berapa penjualan", "user_id": self.user.id, "company_ids": [(6, 0, [self.c3.id])]},
            )
        )
        with self.assertRaises(AccessError):
            self._run(rec.with_user(self.user).sudo(False))

    def test_default_companies_are_active_switcher_companies(self):
        env = self.env(user=self.user, context={"allowed_company_ids": [self.c1.id, self.c2.id]})
        rec = env["efn.data.ask"].create({"question": "berapa penjualan"})
        self.assertEqual(rec.company_ids, self.c1 | self.c2)

    def test_kill_switch(self):
        rec = self._ask()
        with self.assertRaises(UserError):
            self._run(rec, enabled="false")
        self.assertEqual(rec.state, "draft")

    def test_group_required(self):
        with self.assertRaises(AccessError):
            self.env["efn.data.ask"].with_user(self.plain).create({"question": "halo dunia"})

    def test_only_owner_can_run_and_see(self):
        rec = self._ask()
        other_env = self.env(user=self.other)
        self.assertFalse(other_env["efn.data.ask"].search([("id", "=", rec.id)]))
        with self.assertRaises(AccessError):
            rec.with_user(self.other).action_ask()

    def test_auditor_reads_company_records(self):
        rec = self._ask()
        self.assertEqual(rec.company_id, self.c1)
        found = self.env["efn.data.ask"].with_user(self.auditor).search([("id", "=", rec.id)])
        self.assertEqual(found, rec)

    def test_user_cannot_forge_result(self):
        rec = self._ask()
        with self.assertRaises(AccessError):
            rec.write({"state": "done", "result_json": json.dumps({"columns": ["x"], "rows": [["palsu"]]})})
        with self.assertRaises(AccessError):
            self.env["efn.data.ask"].with_user(self.user).create({"question": "abc", "sql": "SELECT 1"})

    def test_rejected_and_error_states(self):
        rec = self._ask()
        self._run(rec, body={"status": "rejected", "code": "table_not_allowed"})
        self.assertEqual((rec.state, rec.error_code), ("rejected", "table_not_allowed"))
        self.assertIn("table_not_allowed", rec.message)
        self.assertFalse(rec.result_html)
        rec2 = self._ask()
        self._run(rec2, status=503, body={"status": "unavailable", "code": "llm_unavailable"})
        self.assertEqual((rec2.state, rec2.error_code), ("error", "llm_unavailable"))
        rec3 = self._ask()
        self._run(rec3, status=0, body={"code": "connection"})
        self.assertEqual(rec3.state, "error")

    def test_cannot_rerun_or_edit_answered(self):
        rec = self._ask()
        self._run(rec)
        with self.assertRaises(UserError):
            self._run(rec)
        with self.assertRaises(UserError):
            rec.write({"question": "pertanyaan lain"})

    def test_question_length(self):
        rec = self._ask(question="ab")
        with self.assertRaises(UserError):
            self._run(rec)

    def test_html_is_escaped(self):
        rec = self._ask()
        self._run(rec, body={"status": "ok", "sql": "x", "columns": ["<b>c</b>"], "rows": [["<script>x</script>"]]})
        self.assertNotIn("<script>", rec.result_html)
        self.assertIn("&lt;script&gt;", rec.result_html)

    def test_new_question_copies_scope(self):
        rec = self._ask(companies=self.c1 | self.c2)
        self._run(rec)
        action = rec.action_new_question()
        new = self.env["efn.data.ask"].browse(action["res_id"])
        self.assertEqual(new.state, "draft")
        self.assertEqual(new.company_ids, self.c1 | self.c2)
        self.assertEqual(new.user_id, self.user)

    def test_post_requires_https_and_ca(self):
        env = {"EFN_TEXT2SQL_URL": "http://text2sql:8443", "APP_ENV": "production"}
        with patch.dict("os.environ", env), self.assertLogs(MODEL, "ERROR"):
            status, body = self.env["efn.data.ask"]._efn_text2sql_post({})
        self.assertEqual((status, body["code"]), (0, "connection"))

    # ------------------------------------------------------------------ G39 analisis bertahap (docs/04 §24)
    ANALYSIS = {
        "status": "ok",
        "engine": "ninerouter:cc/x",
        "steps": [
            {
                "title": "Penjualan <b>bulan ini</b>",
                "status": "ok",
                "sql": "SELECT a",
                "columns": ["brand", "net"],
                "rows": [["PB", 1500.5], ["<script>x</script>", 2]],
                "row_count": 2,
            },
            {"title": "Data GL", "status": "rejected", "code": "table_not_allowed"},
            {"title": "Bulan lalu", "status": "ok", "sql": "SELECT b", "columns": ["n"], "rows": [[1]], "row_count": 1},
            {"title": "kelebihan", "status": "ok", "sql": "SELECT c", "columns": ["n"], "rows": [[9]]},
        ],
        "summary": "- Penjualan PB naik <img src=x onerror=alert(1)>",
        "summary_code": "-",
        "summary_engine": "ninerouter:cc/sum",
        "row_count": 3,
    }

    def _run_analyze(self, rec, status=200, body=None):
        with (
            patch.dict("os.environ", {"TEXT2SQL_ENABLED": "true"}),
            patch(f"{MODEL}.EfnDataAsk._efn_text2sql_post", return_value=(status, body or self.ANALYSIS)) as post,
        ):
            rec.action_ask()
        return post

    def test_analyze_posts_to_analyze_path_with_longer_timeout(self):
        rec = self._ask(mode="analyze", companies=self.c1 | self.c2)
        post = self._run_analyze(rec)
        self.assertEqual(post.call_args.kwargs["path"], "/v1/analyze")
        self.assertGreater(post.call_args.kwargs["timeout"], 60)
        self.assertEqual(post.call_args.args[0]["company_ids"], sorted((self.c1 | self.c2).ids))

    def test_analyze_stores_steps_summary_and_escapes(self):
        rec = self._ask(mode="analyze")
        self._run_analyze(rec)
        self.assertEqual(rec.state, "done")
        self.assertEqual(rec.row_count, 3)  # langkah ke-4 dibuang (maks 3)
        self.assertEqual(rec.summary_engine, "ninerouter:cc/sum")
        html = rec.result_html
        self.assertIn("Penjualan &lt;b&gt;bulan ini&lt;/b&gt;", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("table_not_allowed", html)
        self.assertNotIn("SELECT c", html)
        self.assertIn("-- 1\nSELECT a", rec.sql)
        self.assertIn("-- 2\nSELECT b", rec.sql)
        # ringkasan = field Text (plain, di-escape web client), bukan HTML
        self.assertEqual(type(rec)._fields["summary"].type, "text")
        self.assertIn("AI", rec.message)
        self.assertFalse(rec.summary_note)

    def test_analyze_without_summary_shows_note(self):
        rec = self._ask(mode="analyze")
        body = dict(self.ANALYSIS, summary="", summary_code="results_not_shared", summary_engine="-")
        self._run_analyze(rec, body=body)
        self.assertFalse(rec.summary)
        self.assertFalse(rec.summary_engine)
        self.assertIn("tidak dikirim ke AI", rec.summary_note)

    def test_analyze_disabled_agent_is_error_with_message(self):
        rec = self._ask(mode="analyze")
        self._run_analyze(rec, status=503, body={"status": "unavailable", "code": "agent_disabled"})
        self.assertEqual((rec.state, rec.error_code), ("error", "agent_disabled"))
        self.assertIn("Tanya cepat", rec.message)

    def test_analyze_cannot_forge_summary_or_switch_mode_after_answer(self):
        rec = self._ask(mode="analyze")
        with self.assertRaises(AccessError):
            rec.write({"summary": "palsu"})
        self._run_analyze(rec)
        with self.assertRaises(UserError):
            rec.write({"mode": "ask"})
        new = self.env["efn.data.ask"].browse(rec.action_new_question()["res_id"])
        self.assertEqual(new.mode, "analyze")

    def test_post_rejects_unknown_path(self):
        with self.assertRaises(ValueError):
            self.env["efn.data.ask"]._efn_text2sql_post({}, path="/v1/admin")
