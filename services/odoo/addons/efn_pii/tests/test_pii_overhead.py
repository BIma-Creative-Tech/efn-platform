"""Kriteria 5 (ADR-036 §8.2): overhead baca 1.000 partner terlindungi vs tidak terlindungi.

Tanpa assert ketat (mesin CI/dev berbeda-beda). Angka dicatat ke log dengan penanda
`EFN_PII_POC overhead` agar bisa diambil dari keluaran test:

    grep "EFN_PII_POC overhead" <log test>

Pola baca meniru loader POS (`search_read` domain id + daftar field partner POS yang relevan).
`_load_pos_data` asli belum diukur karena PoC tidak bergantung pada point_of_sale.
"""

import logging
import time

from odoo.tests import TransactionCase, tagged

from .common import PiiKeyMixin

_logger = logging.getLogger(__name__)

N_PARTNERS = 1000
ROUNDS = 5
READ_FIELDS = ["name", "phone", "email", "vat", "street", "city", "zip"]


@tagged("post_install", "-at_install", "efn", "efn_pii_poc")
class TestEfnPiiOverhead(PiiKeyMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self._pii_env()
        self.Partner = self.env["res.partner"].with_context(tracking_disable=True)

    def _vals(self, protected):
        tag = "P" if protected else "U"
        return [
            {
                "name": f"Overhead {tag} {i:04d}",
                "phone": f"0812{i:08d}",
                "email": f"overhead.{tag.lower()}.{i}@example.com",
                "vat": f"01{i:013d}",
                "street": "Jl. Contoh 1",
                "city": "Jakarta",
                "zip": "10110",
                "efn_pii_protected": protected,
            }
            for i in range(N_PARTNERS)
        ]

    def _time_create(self, protected):
        start = time.perf_counter()
        records = self.Partner.create(self._vals(protected))
        self.env.flush_all()
        return records, time.perf_counter() - start

    def _time_read(self, records):
        domain = [("id", "in", records.ids)]
        best = None
        for _round in range(ROUNDS):
            self.env.invalidate_all()
            start = time.perf_counter()
            rows = self.Partner.search_read(domain, READ_FIELDS)
            elapsed = time.perf_counter() - start
            self.assertEqual(len(rows), N_PARTNERS)
            best = elapsed if best is None else min(best, elapsed)
        return best, rows

    def test_overhead_1000_partners(self):
        plain, t_create_plain = self._time_create(False)
        protected, t_create_prot = self._time_create(True)
        t_read_plain, _rows = self._time_read(plain)
        t_read_prot, rows = self._time_read(protected)
        # kebenaran: semua plaintext setelah dekripsi
        self.assertTrue(all(r["phone"].startswith("0812") and "@example.com" in r["email"] for r in rows))
        start = time.perf_counter()
        found = self.Partner.search([("phone", "in", [f"0812{i:08d}" for i in range(0, N_PARTNERS, 10)])])
        t_search = time.perf_counter() - start
        self.assertEqual(len(found & protected), N_PARTNERS // 10)
        ratio = (t_read_prot / t_read_plain - 1.0) * 100.0 if t_read_plain else float("nan")
        _logger.info(
            "EFN_PII_POC overhead n=%d rounds=%d read_plain=%.4fs read_protected=%.4fs overhead_read=%.1f%% "
            "create_plain=%.3fs create_protected=%.3fs search_in_100=%.4fs",
            N_PARTNERS,
            ROUNDS,
            t_read_plain,
            t_read_prot,
            ratio,
            t_create_plain,
            t_create_prot,
            t_search,
        )
