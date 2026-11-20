"""`efn.insight.sm.kpi` (docs/04 §28.13.8c, L-200/L-201): registri, parity JSON, validasi, pasangan ambang, seed,
record rule multi-company, ACL, audit."""

import json
import os
import pathlib
from contextlib import contextmanager

import psycopg2

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.efn_insight_sm.models import kpi_registry as reg

from .common import setup_sm_fixture

JSON_REL = pathlib.Path("docs") / "contracts" / "insight_sm_kpi_defaults.json"
JSON_ENV = "EFN_KPI_DEFAULTS_JSON"


def _find_defaults_json():
    """File normatif kontrak. Env `EFN_KPI_DEFAULTS_JSON` atau naik dari folder modul (repo checkout)."""
    if os.environ.get(JSON_ENV):
        path = pathlib.Path(os.environ[JSON_ENV])
        return path if path.is_file() else None
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / JSON_REL
        if candidate.is_file():
            return candidate
    return None


@tagged("efn", "post_install", "-at_install")
class TestInsightSmKpi(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        setup_sm_fixture(cls)
        cls.Kpi = cls.env["efn.insight.sm.kpi"]
        # PT SMD baru -> baris default dibuat otomatis (res.company create, models/res_company.py).
        cls.rows = cls.Kpi.search([("company_id", "=", cls.smd.id)])
        cls.admin = cls.make_user("efn_sm_kpi_admin", ["kpi_admin"])
        cls.admin_other = cls.make_user("efn_sm_kpi_admin_pb", ["kpi_admin"], companies=cls.pb)
        cls.persona = cls.make_user("efn_sm_kpi_exec", ["exec"])

    @contextmanager
    def _rejects(self, exception, pattern):
        """assertRaisesRegex + savepoint di DALAM-nya: rollback SQL + bersihkan cache ORM saat galat, agar nilai
        yang ditolak tidak tertinggal di cache untuk langkah berikutnya (BaseCase hanya membungkus assertRaises)."""
        with self.assertRaisesRegex(exception, pattern), self.env.cr.savepoint():
            yield

    def _row(self, key):
        return self.Kpi.with_context(active_test=False).search([("company_id", "=", self.smd.id), ("key", "=", key)])

    # ------------------------------------------------------------------ registri & parity
    def test_registry_shape(self):
        self.assertEqual(len(reg.KPI_REGISTRY), 39)
        for key, spec in reg.KPI_REGISTRY.items():
            with self.subTest(key=key):
                self.assertIn(spec.type, reg.TYPES)
                literal = reg.default_literal(key)
                self.assertEqual(reg.parse(key, literal)[1], literal, "default kanonik & sah")
        for lower, upper in reg.KPI_PAIRS:
            self.assertIn(lower, reg.KPI_REGISTRY)
            self.assertIn(upper, reg.KPI_REGISTRY)
        self.assertFalse(reg.pair_violations({}), "default konsisten")

    def test_parity_with_contract_json(self):
        path = _find_defaults_json()
        if path is None:
            self.skipTest(
                f"{JSON_REL} tidak terjangkau dari container (docs tidak di-mount). Set {JSON_ENV}=<path> untuk "
                "menjalankan parity."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = [entry["key"] for entry in data["keys"]]
        self.assertEqual(keys, list(reg.KPI_REGISTRY), "kunci & urutan sama dengan file normatif")
        for entry in data["keys"]:
            spec = reg.KPI_REGISTRY[entry["key"]]
            with self.subTest(key=entry["key"]):
                self.assertEqual(spec.type, entry["type"])
                if spec.type == reg.T_ENUM:
                    self.assertEqual(list(spec.choices), entry["choices"])
                    self.assertEqual(spec.default, entry["default"])
                else:
                    self.assertEqual(float(spec.minimum), float(entry["min"]))
                    self.assertEqual(float(spec.maximum), float(entry["max"]))
                    self.assertEqual(reg.default_literal(entry["key"]), reg.format_number(entry["default"]))
        pairs = [(p["lower"], p["upper"]) for p in data["pairs"]]
        self.assertEqual(pairs, list(reg.KPI_PAIRS))

    # ------------------------------------------------------------------ seed
    def test_seed_defaults_idempotent(self):
        self.assertEqual(len(self.rows), 39)
        self.assertEqual(set(self.rows.mapped("key")), set(reg.KPI_REGISTRY))
        for row in self.rows:
            self.assertEqual(row.value, reg.default_literal(row.key))
            self.assertTrue(row.is_default)
        self.assertFalse(self.Kpi._efn_seed_defaults(self.smd), "idempoten")
        self.assertFalse(self.Kpi._efn_seed_defaults(self.pb), "PT non-SM tidak di-seed")
        self.assertFalse(self.Kpi.search([("company_id", "=", self.pb.id)]))
        self.assertEqual(self._row("thr.oos.bad_pct").value, "5")
        self.assertEqual(self._row("critical.cover_days").value, "2")
        # Baris yang dihapus dibuat ulang oleh seed berikutnya; nilai yang diubah tidak ditimpa.
        self._row("gmroi.annualize").unlink()
        self._row("gmroi.period").value = "year"
        created = self.Kpi._efn_seed_defaults()
        self.assertEqual(created.mapped("key"), ["gmroi.annualize"])
        self.assertEqual(self._row("gmroi.period").value, "year")

    def test_seed_when_company_becomes_sm(self):
        # PT tanpa kode lalu diberi kode SMD (kode unik: lepas dulu dari PT uji).
        self.smd.efn_brand_code = False
        company = self.env["res.company"].create({"name": "PT Uji Insight SMD 2"})
        self.assertFalse(self.Kpi.search([("company_id", "=", company.id)]))
        company.efn_brand_code = "SMD"
        self.assertEqual(len(self.Kpi.search([("company_id", "=", company.id)])), 39)

    # ------------------------------------------------------------------ validasi
    def test_canonical_values(self):
        cases = {
            "critical.cover_days": ("2.50", "2.5"),
            "oos.threshold_qty": (" 1e1 ", "10"),
            "critical.sales_window_days": ("+014", "14"),
            "gmroi.include_dc": ("0", "0"),
            "fill.basis": (" received ", "received"),
        }
        for key, (raw, expected) in cases.items():
            with self.subTest(key=key):
                row = self._row(key)
                row.value = raw
                self.assertEqual(row.value, expected)
        self.assertFalse(self._row("fill.basis").is_default)
        self._row("fill.basis").value = "shipped"
        self.assertTrue(self._row("fill.basis").is_default)

    def test_invalid_values_rejected(self):
        cases = (
            ("critical.cover_days", "0.4", "kpi_range"),
            ("critical.cover_days", "31", "kpi_range"),
            ("critical.cover_days", "1,5", "kpi_type"),
            ("critical.cover_days", "nan", "kpi_type"),
            ("critical.cover_days", "inf", "kpi_type"),
            ("critical.sales_window_days", "28.0", "kpi_type"),
            ("critical.sales_window_days", "6", "kpi_range"),
            ("gmroi.include_dc", "true", "kpi_type"),
            ("gmroi.include_dc", "2", "kpi_type"),
            ("gmroi.period", "week", "kpi_choice"),
            ("gmroi.period", "", "kpi_choice"),
            ("thr.wape.bad_pct", "501", "kpi_range"),
        )
        for key, raw, code in cases:
            with self.subTest(key=key, raw=raw), self._rejects(ValidationError, f"efn_insight_sm:{code}"):
                self._row(key).value = raw

    def test_pairs_consistent(self):
        with self._rejects(ValidationError, "efn_insight_sm:kpi_pair"):
            self._row("thr.oos.warn_pct").value = "5"  # warn == bad
        with self._rejects(ValidationError, "efn_insight_sm:kpi_pair"):
            self._row("thr.fill.good_pct").value = "89"  # good < warn
        with self._rejects(ValidationError, "efn_insight_sm:kpi_pair"):
            self._row("fresh.daily.stale_min").value = "1560"
        # Urutan yang menjaga lower < upper lolos.
        self._row("thr.oos.bad_pct").value = "10"
        self._row("thr.oos.warn_pct").value = "6"
        # Mengarsipkan baris upper -> kembali ke default 5 < warn 6 -> ditolak.
        with self._rejects(ValidationError, "efn_insight_sm:kpi_pair"):
            self._row("thr.oos.bad_pct").active = False
        # Menghapus baris juga dicek.
        with self._rejects(ValidationError, "efn_insight_sm:kpi_pair"):
            self._row("thr.oos.bad_pct").unlink()
        # Arsip lower dulu -> default 2 < 10: sah; is_default True untuk baris arsip (C-1).
        lower = self._row("thr.oos.warn_pct")
        lower.active = False
        self.assertTrue(lower.is_default)

    def test_key_and_company_readonly(self):
        row = self._row("gmroi.period")
        with self._rejects(UserError, "efn_insight_sm:kpi_readonly"):
            row.key = "gmroi.stock_avg"
        with self._rejects(UserError, "efn_insight_sm:kpi_readonly"):
            row.company_id = self.pb
        row.write({"key": "gmroi.period", "value": "quarter"})  # kunci sama = boleh
        self.assertEqual(row.value, "quarter")

    def test_unique_company_key(self):
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.IntegrityError), self.env.cr.savepoint():
            self.Kpi.create({"company_id": self.smd.id, "key": "gmroi.period", "value": "month"})
            self.env.flush_all()

    def test_unknown_key_and_non_sm_company(self):
        with self._rejects(ValidationError, "efn_insight_sm:kpi_unknown_key"):
            self.Kpi.create({"company_id": self.smd.id, "key": "gmroi.unknown", "value": "1"})
        self._row("gmroi.annualize").unlink()
        with self._rejects(ValidationError, "efn_insight_sm:kpi_company"):
            self.Kpi.create({"company_id": self.pb.id, "key": "gmroi.annualize", "value": "1"})

    def test_note_length(self):
        row = self._row("gmroi.period")
        row.note = "x" * 128
        with self._rejects(ValidationError, "efn_insight_sm:kpi_note"):
            row.note = "x" * 129

    def test_multi_value_write_canonical_per_key(self):
        rows = self._row("critical.sales_window_days") | self._row("thr.price.min_samples")
        rows.write({"value": "07"})
        self.assertEqual(rows.mapped("value"), ["7", "7"])
        with self._rejects(ValidationError, "efn_insight_sm:kpi_range"):
            rows.write({"value": "60"})  # sah untuk window, di luar rentang min_samples (1..50)

    # ------------------------------------------------------------------ hak akses
    def test_kpi_admin_crud_own_company(self):
        Kpi = self.Kpi.with_user(self.admin)
        rows = Kpi.search([("company_id", "=", self.smd.id)])
        self.assertEqual(len(rows), 39)
        row = rows.filtered(lambda r: r.key == "fill.basis")
        row.value = "received"
        row.unlink()
        Kpi.create({"company_id": self.smd.id, "key": "fill.basis", "value": "received"})

    def test_record_rule_multi_company(self):
        Kpi = self.Kpi.with_user(self.admin_other)
        self.assertFalse(Kpi.search([("company_id", "=", self.smd.id)]))
        row = self._row("gmroi.period")
        with self.assertRaises(AccessError):
            row.with_user(self.admin_other).read(["value"])
        with self.assertRaises(AccessError):
            row.with_user(self.admin_other).write({"value": "year"})

    def test_persona_cannot_read_or_write(self):
        row = self._row("gmroi.period")
        with self.assertRaises(AccessError):
            row.with_user(self.persona).read(["value"])
        with self.assertRaises(AccessError):
            self.Kpi.with_user(self.persona).create(
                {"company_id": self.smd.id, "key": "gmroi.period", "value": "month"}
            )

    # ------------------------------------------------------------------ audit
    def test_change_audited(self):
        self.env.registry.clear_cache()
        row = self._row("thr.price.min_samples")
        row.with_user(self.admin).write({"value": "5", "note": "uji audit"})
        logs = (
            self.env["efn.audit.log"]
            .sudo()
            .search([("model_name", "=", "efn.insight.sm.kpi"), ("res_id", "=", row.id), ("operation", "=", "write")])
        )
        self.assertEqual(len(logs), 1)
        line = logs.line_ids.filtered(lambda r: r.field_name == "value")
        self.assertEqual((line.old_value, line.new_value), ("3", "5"))
        self.assertEqual(logs.user_id, self.admin)
        self.assertEqual(logs.company_id, self.smd)
        rule = self.env.ref("efn_insight_sm.audit_rule_efn_insight_sm_kpi")
        self.assertTrue(rule.capture_unlink_values and rule.log_create and rule.log_write and rule.log_unlink)
