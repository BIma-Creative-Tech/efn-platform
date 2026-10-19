"""AC-9 (ADR-036 §0.5): token nama — normalisasi, awalan >= 3, AND multi-kata, tanpa infiks, padding
kelipatan 16, erasure/ganti nama mengosongkan token lama, rotasi kunci menghitung ulang, indeks GIN."""

import os
import re

from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL

from odoo.addons.efn_pii.models import keyring as kr
from odoo.addons.efn_pii.models.res_partner import GIN_INDEX
from odoo.addons.efn_pii.tools import name_tokens as nt

from .common import PiiKeyMixin

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnNameTokensPure(TransactionCase):
    def test_normalize(self):
        for raw, expected in (
            ("Budi Santoso", "budi santoso"),
            ("  Hj. Siti   Nurhaliza ", "hj siti nurhaliza"),
            ("Al-Fatih", "al fatih"),
            ("José Müller", "jose muller"),
            ("ＢＵＤＩ", "budi"),
            ("Straße", "strasse"),
            ("%budi%", "budi"),
            ("*sant*", "sant"),
            ("李小龙", ""),
            ("", ""),
            (None, ""),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(nt.normalize(raw), expected)

    def test_record_inputs_and_queries(self):
        inputs = nt.record_inputs("Budi Santoso", "PT Sumber Rejeki, Budi Santoso")
        self.assertEqual(inputs["f"], {"budi santoso", "pt sumber rejeki budi santoso"})
        self.assertEqual(inputs["w"], {"pt", "sumber", "rejeki", "budi", "santoso"})
        self.assertTrue({"bud", "budi", "san", "sant", "santo", "santos", "santoso", "sum", "rej"} <= inputs["p"])
        self.assertNotIn("bu", inputs["p"])
        self.assertNotIn("pt", inputs["p"])  # awalan minimal 3
        self.assertNotIn("anto", inputs["p"])  # tanpa infiks
        long = nt.record_inputs("Abcdefghijklmnopqrstu", "")
        self.assertIn("abcdefghijklmno", long["p"])
        self.assertNotIn("abcdefghijklmnop", long["p"])  # maks 15
        self.assertEqual(nt.prefix_query_inputs("budi sant"), ["budi", "sant"])
        self.assertEqual(nt.prefix_query_inputs("Sant budi"), ["budi", "sant"])
        self.assertEqual(nt.prefix_query_inputs("bu sant"), ["sant"])  # kata < 3 diabaikan bila ada lainnya
        self.assertIsNone(nt.prefix_query_inputs("bu"))
        self.assertEqual(nt.prefix_query_inputs("%%"), [])
        self.assertEqual(nt.prefix_query_inputs("abcdefghijklmnopqrstu"), ["abcdefghijklmno"])

    def test_padding_multiple_of_16_and_unique(self):
        def fake_pad(index):
            return f"pad{index:019d}"

        self.assertEqual(nt.pad_tokens([], fake_pad), [])
        padded = nt.pad_tokens(["b", "a", "a"], fake_pad)
        self.assertEqual(len(padded), 16)
        self.assertEqual(padded, sorted(padded))
        self.assertEqual(len(set(padded)), 16)
        self.assertTrue({"a", "b"} <= set(padded))
        self.assertEqual(padded, nt.pad_tokens(["a", "b"], fake_pad))  # deterministik
        self.assertEqual(len(nt.pad_tokens([f"t{i}" for i in range(16)], fake_pad)), 16)
        self.assertEqual(len(nt.pad_tokens([f"t{i}" for i in range(17)], fake_pad)), 32)
        self.assertEqual(len(nt.pad_tokens(["a"], fake_pad, extra_blocks=1)), 32)
        # kandidat padding yang kebetulan sama dengan token asli dilewati
        clash = nt.pad_tokens(["pad0000000000000000000"], fake_pad)
        self.assertEqual(len(clash), 16)
        self.assertEqual(len(set(clash)), 16)
        token = kr.random_name_token()
        self.assertRegex(token, TOKEN_RE)

    def test_padding_seed_per_record_and_name(self):
        """SEC-149: padding = f(kunci nama, tabel, id, token asli) -> stabil untuk nama sama, berganti total bila
        nama/record berbeda."""
        ring = kr.KeyRing({1: os.urandom(32)}, 1, os.urandom(32), name_key=os.urandom(32))
        real = {ring.name_token("p", "budi"), ring.name_token("f", "budi")}
        pad_a, extra_a = ring.name_padder("res_partner", 7, real)
        pad_b, extra_b = ring.name_padder("res_partner", 7, set(real))
        self.assertEqual([pad_a(i) for i in range(20)], [pad_b(i) for i in range(20)])
        self.assertEqual(extra_a, extra_b)
        self.assertIn(extra_a, (0, 1))
        pad_other_id, _extra = ring.name_padder("res_partner", 8, real)
        pad_other_name, _extra = ring.name_padder("res_partner", 7, real | {ring.name_token("p", "san")})
        first = {pad_a(i) for i in range(32)}
        self.assertFalse(first & {pad_other_id(i) for i in range(32)})
        self.assertFalse(first & {pad_other_name(i) for i in range(32)})
        for i in range(5):
            self.assertRegex(pad_a(i), TOKEN_RE)
        # tanpa kunci token nama: tidak ada padding (fail-closed)
        with self.assertRaises(kr.PiiError):
            kr.KeyRing({1: os.urandom(32)}, 1, os.urandom(32)).name_padder("res_partner", 7, real)

    def test_token_derivation_per_kind(self):
        ring = kr.KeyRing({1: os.urandom(32)}, 1, os.urandom(32), name_key=os.urandom(32))
        f_tok, p_tok = ring.name_token("f", "budi"), ring.name_token("p", "budi")
        self.assertRegex(f_tok, TOKEN_RE)
        self.assertNotEqual(f_tok, p_tok)  # subkunci per jenis
        self.assertEqual(ring.name_token("p", "budi"), p_tok)  # deterministik
        other = kr.KeyRing({1: os.urandom(32)}, 1, os.urandom(32), name_key=os.urandom(32))
        self.assertNotEqual(other.name_token("p", "budi"), p_tok)
        with self.assertRaises(ValueError):
            ring.name_token("x", "budi")
        no_name = kr.KeyRing({1: os.urandom(32)}, 1, os.urandom(32))
        self.assertFalse(no_name.can_encrypt)
        with self.assertRaises(kr.PiiError):
            no_name.name_token("p", "budi")


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnNameTokenSearch(PiiKeyMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        _folder, self.keys, self.bidx_key = self._pii_env()
        self.Partner = self.env["res.partner"]
        self.budi = self.Partner.create({"name": "Budi Santoso", "phone": "0812 1000 0001"})
        self.jose = self.Partner.create({"name": "José Müller"})
        self.company = self.Partner.create({"name": "PT Sumber Rejeki", "is_company": True})
        self.contact = self.Partner.create({"name": "Agus Salim", "parent_id": self.company.id})
        self.antonius = self.Partner.create({"name": "Antonius Wijaya"})
        self.plain = self.Partner.create({"name": "CV Santoso Jaya", "is_company": True})
        self.all = self.budi | self.jose | self.company | self.contact | self.antonius | self.plain

    def _find(self, domain):
        return self.Partner.search([*domain, ("id", "in", self.all.ids)])

    def test_tokens_column_shape(self):
        self.env.flush_all()
        raw = self._raw(self.budi.id, "efn_name_tokens")["efn_name_tokens"]
        tokens = raw.split(" ")
        self.assertEqual(len(tokens) % 16, 0)
        self.assertEqual(tokens, sorted(tokens))
        self.assertEqual(len(set(tokens)), len(tokens))
        for token in tokens:
            self.assertRegex(token, TOKEN_RE)
        for plain in ("budi", "Budi", "santoso", "sant"):
            self.assertNotIn(plain, raw)
        self.assertIsNone(self._raw(self.company.id, "efn_name_tokens")["efn_name_tokens"])
        self.assertIsNone(self._raw(self.plain.id, "efn_name_tokens")["efn_name_tokens"])

    def test_prefix_and_multiword_and(self):
        cases = {
            "budi": self.budi,
            "BUDI": self.budi,
            "sant": self.budi | self.plain,
            "budi sant": self.budi,
            "santoso budi": self.budi,
            "%budi%": self.budi,
            # `*` = pemisah kata untuk partner terlindungi (awalan "sant"); untuk partner tidak terlindungi
            # Odoo standar memperlakukan `*` sebagai karakter literal, jadi "CV Santoso Jaya" TIDAK cocok
            "*sant*": self.budi,
            "jose": self.jose,
            "müller": self.jose,
            "muller": self.jose,
            "sumber": self.company | self.contact,  # kata perusahaan ikut di complete_name kontak
            "sumber agus": self.contact,
            "anton": self.antonius,
        }
        for term, expected in cases.items():
            with self.subTest(term=term):
                self.assertEqual(self._find([("name", "ilike", term)]) & expected, expected)
                self.assertEqual(self._find([("complete_name", "ilike", term)]) - expected - self.plain, self.Partner)
        # infiks TIDAK cocok untuk terlindungi (plain tetap substring standar)
        self.assertEqual(self._find([("name", "ilike", "anto")]), self.antonius | self.plain)
        self.assertNotIn(self.budi, self._find([("name", "ilike", "antoso")]))
        self.assertNotIn(self.budi, self._find([("name", "ilike", "bu")]))
        self.assertNotIn(self.budi, self._find([("name", "ilike", "budi xyz")]))
        self.assertIn(self.plain, self._find([("name", "ilike", "jaya")]))
        # negasi semantik
        self.assertNotIn(self.budi, self._find([("name", "not ilike", "budi")]))
        self.assertIn(self.jose, self._find([("name", "not ilike", "budi")]))

    def test_exact_name(self):
        self.assertEqual(self._find([("name", "=", "Budi Santoso")]), self.budi)
        self.assertEqual(self._find([("name", "=", "budi  santoso")]), self.budi)
        self.assertEqual(self._find([("name", "=ilike", "BUDI SANTOSO")]), self.budi)
        self.assertEqual(self._find([("name", "in", ["Budi Santoso", "Antonius Wijaya"])]), self.budi | self.antonius)
        self.assertFalse(self._find([("name", "=", "Budi")]))
        self.assertEqual(self._find([("complete_name", "=", "PT Sumber Rejeki, Agus Salim")]), self.contact)
        self.assertEqual(self._find([("name", "=", "Agus Salim")]), self.contact)
        self.assertEqual(self._find([("name", "!=", "Budi Santoso")]), self.all - self.budi)

    def test_name_search_display_name_and_other_models(self):
        ids = [row[0] for row in self.Partner.name_search("budi sant", limit=50)]
        self.assertIn(self.budi.id, ids)
        self.assertIn(self.budi, self._find([("display_name", "ilike", "budi")]))
        # traversal many2one dari model lain (partner_id.name)
        users_domain = [("partner_id.name", "ilike", "budi")]
        self.assertFalse(self.env["res.users"].search(users_domain))
        child = self.Partner.create({"name": "Rumah Budi", "parent_id": self.budi.id, "type": "delivery"})
        self.assertIn(child, self.Partner.search([("parent_id.name", "ilike", "santoso")]))

    def test_rename_erasure_drops_old_tokens(self):
        self.budi.write({"name": "Pelanggan dihapus"})
        self.assertNotIn(self.budi, self._find([("name", "ilike", "budi")]))
        self.assertIn(self.budi, self._find([("name", "ilike", "pelanggan")]))

    def test_name_key_rotation_recomputes(self):
        partner = self.Partner.create({"name": "Rina Kartika"})
        self.assertIn(partner, self.Partner.search([("name", "ilike", "rina")]))
        # rotasi HANYA odoo_pii_name_key (kunci enkripsi & blind index tetap)
        self._pii_env(keys=self.keys, bidx_key=self.bidx_key, name_key=os.urandom(32))
        self.env.invalidate_all()
        self.assertNotIn(partner, self.Partner.search([("name", "ilike", "rina")]))
        totals = self.Partner.sudo()._efn_pii_migrate(ids=(partner | self.budi).ids)
        self.assertEqual(totals["partners"], 2)
        self.assertEqual(totals["failed"], 0)
        self.assertIn(partner, self.Partner.search([("name", "ilike", "rina")]))
        self.assertIn(self.budi, self.Partner.search([("name", "ilike", "budi sant")]))
        self.assertEqual(partner.name, "Rina Kartika")
        # blind index tidak berubah (kunci bidx sama)
        self.assertIn(self.budi, self.Partner.search([("phone", "=", "081210000001")]))

    def test_gin_index_exists_and_usable(self):
        self.env.cr.execute(SQL("SELECT indexdef FROM pg_indexes WHERE indexname = %s", GIN_INDEX))
        row = self.env.cr.fetchone()
        self.assertTrue(row)
        self.assertIn("gin", row[0].lower())
        self.assertIn("string_to_array", row[0])
        self.env.flush_all()
        ring = kr.get_keyring(self.env.cr.dbname)
        token = ring.name_token("p", "budi")
        self.env.cr.execute("SET LOCAL enable_seqscan = off")
        try:
            self.env.cr.execute(
                SQL(
                    "EXPLAIN SELECT id FROM res_partner WHERE string_to_array(efn_name_tokens, ' ') @> %s::text[]",
                    [token],
                )
            )
            plan = "\n".join(row[0] for row in self.env.cr.fetchall())
        finally:
            self.env.cr.execute("SET LOCAL enable_seqscan = on")
        self.assertIn(GIN_INDEX, plan)
