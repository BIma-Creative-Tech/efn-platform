"""G21 E-39 (docs/04 §13.5, L-63): ``efn_online_set_invoice_party`` — faktur atas nama pembeli.

Dua kelas:
- ``TestEfnOnlineInvoicePartyGuards``: selalu jalan (validasi input, not_found, PT F&B, tanpa efn_pii ->
  ``invoice_not_available`` dan TIDAK ada partner/profil yang tersimpan).
- ``TestEfnOnlineInvoicePartyPii``: hanya bila efn_pii terpasang dan field partner terenkripsi (run gabungan AC-4,
  kunci uji global); selain itu di-skip dengan alasan.

Profil di bawah = data uji fiktif (bukan orang nyata). NPWP 16 digit pola test CE l10n_id_efaktur_coretax.
"""

import uuid
from unittest.mock import PropertyMock, patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import EfnOnlineCommon

PROFILE = {
    "buyer_type": "npwp",
    "tax_id": "1234567890123456",
    "name": "Pembeli Uji Faktur",
    "address": "Jl. Contoh Faktur No. 1, Jakarta",
    "email": "pembeli.uji@example.com",
}
LOGGER = "odoo.addons.efn_online_order.models.efn_online_order"


class _InvoicePartyMixin:
    @classmethod
    def _efn_set_company_code(cls, company, code):
        """Kode company unik (efn_core): lepas dari company lain dulu (rollback di akhir kelas)."""
        holder = cls.env["res.company"].search([("efn_brand_code", "=", code), ("id", "!=", company.id)])
        holder.write({"efn_brand_code": False})
        # Flush dulu: tanpa ini ORM 19 menggabungkan kedua write ke satu UPDATE dan UNIQUE langsung dilanggar.
        holder.flush_recordset(["efn_brand_code"])
        company.write({"efn_brand_code": code})

    def set_party(self, part, profile=None, key=None, user=None):
        return self.order_api(user).efn_online_set_invoice_party(
            "TST", part, dict(profile or PROFILE), key or f"online:invoice_party:{part}"
        )

    def assertOnlineError(self, code, func, *args, **kwargs):
        with self.assertRaises(UserError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(str(ctx.exception), f"efn_online:{code}")

    @classmethod
    def _pii_ready(cls):
        order_model = cls.env["efn.online.order"]
        partner_model = cls.env["res.partner"]
        return "efn_pii_protected" in partner_model._fields and all(
            order_model._efn_pii_encrypted_field(partner_model, fname) for fname in ("name", "street", "email", "vat")
        )


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineInvoicePartyGuards(_InvoicePartyMixin, EfnOnlineCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._efn_set_company_code(cls.company, "GL")  # PT PPN (ritel)

    def test_invalid_profile_rejected(self):
        part, _payload, _result = self.place()
        bad_profiles = [
            {**PROFILE, "buyer_type": "ktp"},
            {**PROFILE, "tax_id": "12345"},
            {**PROFILE, "tax_id": "12345678901234567"},
            {**PROFILE, "buyer_type": "nik", "tax_id": "ABCDEFGHIJKLMNOP"},
            {**PROFILE, "buyer_type": "passport", "tax_id": "A1"},
            {**PROFILE, "name": "A"},
            {**PROFILE, "name": "x" * 101},
            {**PROFILE, "address": "Jl."},
            {**PROFILE, "address": "Jl. Uji\x00 1"},
            {**PROFILE, "email": "bukan-email"},
            {**PROFILE, "extra": "tidak boleh"},
            {k: v for k, v in PROFILE.items() if k != "name"},
        ]
        for profile in bad_profiles:
            with self.subTest(profile=sorted(profile)):
                self.assertOnlineError("invalid_input", self.set_party, part, profile)
        self.assertOnlineError("invalid_input", self.set_party, part, PROFILE, key="pendek")
        self.assertOnlineError(
            "invalid_input",
            self.order_api().efn_online_set_invoice_party,
            "TST",
            part,
            "bukan-dict",
            f"online:invoice_party:{part}",
        )

    def test_unknown_order_not_found(self):
        self.assertOnlineError("not_found", self.set_party, str(uuid.uuid4()))

    def test_other_channel_user_denied(self):
        part, _payload, _result = self.place()
        with self.assertRaises(AccessError):
            self.set_party(part, user=self.tech_user_2)

    def test_fnb_company_not_available(self):
        """PT PB1 (F&B) tidak menerbitkan faktur pajak PPN -> invoice_not_available (dengan/tanpa efn_pii)."""
        self._efn_set_company_code(self.company, "PB")
        part, _payload, _result = self.place()
        self.assertOnlineError("invoice_not_available", self.set_party, part)
        self.assertEqual(self.record(part).sale_order_id.partner_invoice_id, self.channel.partner_id)

    def test_without_pii_not_available(self):
        """L-63: tanpa efn_pii profil pembeli tidak pernah masuk Odoo."""
        if "efn_pii_protected" in self.env["res.partner"]._fields:
            self.skipTest("efn_pii terpasang: jalur tanpa enkripsi tidak dapat diuji di DB ini")
        part, _payload, _result = self.place()
        before = self.env["res.partner"].with_context(active_test=False).search_count([])
        self.assertOnlineError("invoice_not_available", self.set_party, part)
        self.assertOnlineError("invoice_not_available", self.set_party, part, {**PROFILE, "buyer_type": "nik"})
        self.assertEqual(self.env["res.partner"].with_context(active_test=False).search_count([]), before)
        order = self.record(part)
        self.assertEqual(order.sale_order_id.partner_invoice_id, self.channel.partner_id)
        self.assertFalse(order.invoice_party_idempotency_key)
        self.assertFalse(
            self.env["res.partner"].with_context(active_test=False).search_count([("name", "=", PROFILE["name"])])
        )


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineInvoicePartyPii(_InvoicePartyMixin, EfnOnlineCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pii_ready = cls._pii_ready()
        if cls.pii_ready:
            cls._efn_set_company_code(cls.company, "GL")

    def setUp(self):
        super().setUp()
        if not self.pii_ready:
            self.skipTest("efn_pii (+ efn_pii_base_vat bila base_vat terpasang) tidak terpasang: run gabungan AC-4")

    def _raw_partner(self, partner, column):
        self.env.flush_all()
        self.env.cr.execute(f"SELECT {column} FROM res_partner WHERE id = %s", (partner.id,))  # noqa: S608 (kolom tetap)
        return self.env.cr.fetchone()[0]

    def test_set_party_creates_protected_buyer(self):
        part, _payload, _result = self.place()
        with self.assertLogs(LOGGER, "INFO") as logs:
            result = self.set_party(part)
        self.assertEqual(result, {"state": "set"})
        output = "\n".join(logs.output)
        for value in PROFILE.values():
            if value != "npwp":
                self.assertNotIn(value, output)
        order = self.record(part)
        sale_order = order.sale_order_id
        buyer = sale_order.partner_invoice_id
        self.assertNotEqual(buyer, self.channel.partner_id)
        self.assertEqual(sale_order.partner_id, self.channel.partner_id)
        self.assertTrue(buyer.efn_pii_protected)
        self.assertTrue(buyer.efn_online_invoice_buyer)
        self.assertEqual(buyer.company_id, self.company)
        self.assertFalse(buyer.parent_id)
        self.assertEqual(buyer.name, PROFILE["name"])
        self.assertEqual("".join(c for c in buyer.vat if c.isdigit()), PROFILE["tax_id"])
        for column in ("name", "street", "email", "vat"):
            raw = self._raw_partner(buyer, column)
            self.assertTrue(raw and raw.startswith("efnpii"), column)
            self.assertNotIn(PROFILE["tax_id"], raw)
        # Tidak ada event baru (profil tidak pernah masuk outbox) dan state pesanan tidak berubah.
        self.assertEqual(order.state, "placed")
        self.assertEqual(set(order.event_ids.mapped("event")), {"placed"})

    def test_replay_and_conflict(self):
        part, _payload, _result = self.place()
        self.assertEqual(self.set_party(part), {"state": "set"})
        buyer = self.record(part).sale_order_id.partner_invoice_id
        self.assertEqual(self.set_party(part), {"state": "set"})
        self.assertEqual(self.record(part).sale_order_id.partner_invoice_id, buyer)
        self.assertOnlineError("idempotency_conflict", self.set_party, part, {**PROFILE, "name": "Nama Lain Uji"})
        self.assertOnlineError(
            "idempotency_conflict", self.set_party, part, PROFILE, key=f"online:invoice_party2:{part}"
        )

    def test_buyer_reused_per_company(self):
        part_1, _p1, _r1 = self.place()
        part_2, _p2, _r2 = self.place()
        self.set_party(part_1)
        self.set_party(part_2)
        buyer_1 = self.record(part_1).sale_order_id.partner_invoice_id
        self.assertEqual(self.record(part_2).sale_order_id.partner_invoice_id, buyer_1)
        part_3, _p3, _r3 = self.place()
        self.set_party(part_3, {**PROFILE, "address": "Jl. Alamat Lain No. 2, Bandung"})
        self.assertNotEqual(self.record(part_3).sale_order_id.partner_invoice_id, buyer_1)

    def test_invoice_uses_buyer_and_paid_order_is_final(self):
        part, _payload, _result = self.place()
        self.set_party(part)
        buyer = self.record(part).sale_order_id.partner_invoice_id
        self.assertEqual(self.pay(part)["order_state"], "confirmed")
        order = self.record(part)
        self.assertEqual(order.invoice_id.partner_id, buyer)
        self.assertEqual(order.invoice_id.commercial_partner_id, buyer)
        # Replay kunci yang sama tetap aman setelah bayar; kunci baru / order dibayar tanpa party -> ditolak.
        self.assertEqual(self.set_party(part), {"state": "set"})
        part_2, _p2, _r2 = self.place()
        self.pay(part_2)
        self.assertOnlineError("invalid_state", self.set_party, part_2)

    def test_locked_keyring_not_available(self):
        """Mode terkunci efn_pii (kunci tidak terbaca) -> invoice_not_available, tanpa partner tersimpan."""
        from odoo.addons.efn_pii.models import keyring as kr  # noqa: PLC0415 (opsional, hanya bila terpasang)

        part, _payload, _result = self.place()
        ring_cls = type(kr.get_keyring(self.env.cr.dbname, log=False))
        before = self.env["res.partner"].sudo().search_count([("efn_online_invoice_buyer", "=", True)])
        with (
            mute_logger(kr._logger.name),
            self.assertLogs(LOGGER, "WARNING"),
            patch.object(ring_cls, "can_encrypt", new_callable=PropertyMock, return_value=False),
        ):
            self.assertOnlineError("invoice_not_available", self.set_party, part)
        self.assertEqual(self.record(part).sale_order_id.partner_invoice_id, self.channel.partner_id)
        self.assertFalse(self.record(part).invoice_party_idempotency_key)
        self.assertEqual(self.env["res.partner"].sudo().search_count([("efn_online_invoice_buyer", "=", True)]), before)
