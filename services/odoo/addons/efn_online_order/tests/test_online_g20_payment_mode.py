"""G20 E-45 (docs/04 §12.2, L-56): metode bayar kanal + `payment_mode` di place — bagian yang tidak butuh POS.

Alur bayar di kasir lengkap (kode bayar, lookup POS, sync, kliring, event) diuji di
`efn_online_pos_counter/tests/test_online_pos_counter.py` (modul jembatan yang menyediakan jurnal kliring).
"""

import uuid

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.efn_online_order.models.efn_online_common import (
    counter_code_digest,
    counter_hash_rounds,
    parse_payment_modes,
)

from .common import EfnOnlineCommon

COUNTER_CODE = "WXYZ6789"


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlinePaymentMode(EfnOnlineCommon):
    def _payload(self, **extra):
        lines = [{"product_id": self.p_rice.id, "qty": 1}]
        part = str(uuid.uuid4())
        payload = {
            "idempotency_key": f"online:place:{part}",
            "platform_order_ref": part,
            "checkout_ref": str(uuid.uuid4()),
            "outlet_code": "TOKO-T1",
            "fulfilment": "pickup",
            "lines": lines,
            "quote_hash": self.quote(lines)["quote_hash"],
            "pickup_code": "ABCD2345",
        }
        payload.update(extra)
        return part, payload

    def test_parse_payment_modes(self):
        self.assertEqual(parse_payment_modes("counter, qris"), ["qris", "counter"])
        self.assertEqual(parse_payment_modes("counter"), ["counter"])
        for bad in ("", "qris,", "qris,qris", "va", "QRIS", None):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_payment_modes(bad))

    def test_default_qris_and_constraint(self):
        self.assertEqual(self.channel.payment_modes, "qris")
        self.assertEqual(self.channel._efn_online_payment_modes(), ["qris"])
        for bad in ("va", "qris,,counter", "qris,qris", " "):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.channel.write({"payment_modes": bad})
        with self.assertRaises(ValidationError):
            self.channel.write({"payment_modes": "counter", "invoice_on_paid": False})

    def test_catalog_block_payment_modes_array(self):
        block = self.channel_api().efn_online_catalog("TST")["channel"]
        self.assertEqual(block["payment_modes"], ["qris"])

    def test_place_qris_payload_unchanged(self):
        """`payment_mode: "qris"` eksplisit = tanpa kunci (payload_sha256 sama)."""
        part, payload = self._payload()
        result = self.order_api().efn_online_place("TST", payload)
        self.assertNotIn("counter_code", result)
        order = self.record(part)
        self.assertEqual(order.payment_mode, "qris")
        self.assertFalse(order.counter_code_hash)
        replay = dict(payload, payment_mode="qris")
        self.assertEqual(self.order_api().efn_online_place("TST", replay)["sale_order_name"], order.name)

    def test_place_counter_rejected_when_channel_disallows(self):
        _part, payload = self._payload(payment_mode="counter", counter_code=COUNTER_CODE)
        with self.assertRaisesRegex(Exception, "efn_online:invalid_input"):
            self.order_api().efn_online_place("TST", payload)
        self.assertFalse(self.record(payload["platform_order_ref"]))

    def test_place_input_validation(self):
        for extra in (
            {"payment_mode": "va"},
            {"payment_mode": "qris", "counter_code": COUNTER_CODE},  # kode bayar tanpa mode counter
            {"counter_code": COUNTER_CODE},
        ):
            with self.subTest(extra=extra):
                _part, payload = self._payload(**extra)
                with self.assertRaisesRegex(Exception, "efn_online:invalid_input"):
                    self.order_api().efn_online_place("TST", payload)

    def test_counter_digest_deterministic_per_company(self):
        one = counter_code_digest("s", self.company.id, COUNTER_CODE, 1000)
        self.assertEqual(one, counter_code_digest("s", self.company.id, COUNTER_CODE, 1000))
        self.assertNotEqual(one, counter_code_digest("s", self.company_2.id, COUNTER_CODE, 1000))
        self.assertNotEqual(one, counter_code_digest("t", self.company.id, COUNTER_CODE, 1000))
        self.assertNotIn(COUNTER_CODE, one)
        self.assertEqual(counter_hash_rounds(one), 1000)
        self.assertTrue(one.startswith("pbkdf2-sha512$1000$"))
