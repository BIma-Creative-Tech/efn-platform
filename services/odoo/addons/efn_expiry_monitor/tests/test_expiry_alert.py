"""efn.expiry.alert: tahap per ambang kategori (diwariskan), idempoten per hari, notifikasi, dedupe CE."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import EfnExpiryCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnExpiryAlert(EfnExpiryCommon):
    def alerts(self):
        return self.env["efn.expiry.alert"].search([("company_id", "=", self.company.id)])

    def test_category_inheritance_and_constraint(self):
        self.assertEqual(
            self.categ._efn_expiry_thresholds(), {"near": 10, "markdown": 3, "min_receipt": 5}, "daun mewarisi induk"
        )
        self.categ.efn_markdown_days = 2
        self.assertEqual(self.categ._efn_expiry_thresholds()["markdown"], 2, "nilai sendiri menang")
        self.assertIn(self.categ.id, self.env["product.category"]._efn_managed_categ_ids())
        self.assertNotIn(self.categ_plain.id, self.env["product.category"]._efn_managed_categ_ids())
        # Nilai sama valid (1/1/1); markdown > near ditolak.
        self.categ_plain.write({"efn_near_expiry_days": 1, "efn_markdown_days": 1, "efn_min_receipt_days": 1})
        with self.assertRaises(ValidationError):
            self.categ_plain.write({"efn_near_expiry_days": 2, "efn_markdown_days": 5})
        with self.assertRaises(ValidationError):
            self.categ_plain.write({"efn_min_receipt_days": -1})

    def test_scan_stages_idempotent_and_close(self):
        lot_near = self.make_lot("EXP-NEAR", 8)
        lot_md = self.make_lot("EXP-MD", 2)
        lot_exp = self.make_lot("EXP-OLD", -1)
        lot_far = self.make_lot("EXP-FAR", 30)
        lot_dc = self.make_lot("EXP-DC", 2)
        for lot in (lot_near, lot_md, lot_exp, lot_far):
            self.put(lot, self.outlet_a, 5)
        self.put(lot_dc, self.dc, 5)
        self.put(self.make_lot("EXP-PLAIN", 1, product=self.product_plain), self.outlet_a, 5)
        Alert = self.env["efn.expiry.alert"]
        created = Alert._cron_efn_expiry_scan()
        self.assertEqual(created, 3)
        stages = {a.lot_id.name: a.stage for a in self.alerts()}
        self.assertEqual(
            stages,
            {"EXP-NEAR": "near", "EXP-MD": "markdown", "EXP-OLD": "expired"},
            "DC, lot jauh, dan kategori tanpa ambang tidak di-alert",
        )
        near = self.alerts().filtered(lambda a: a.lot_id == lot_near)
        self.assertEqual((near.qty, near.warehouse_id, near.state), (5, self.outlet_a, "open"))
        # Idempoten: run kedua di hari yang sama tidak membuat alert/aktivitas baru.
        activities = self.env["mail.activity"].search_count([("res_model", "=", "efn.expiry.alert")])
        self.assertEqual(Alert._cron_efn_expiry_scan(), 0)
        self.assertEqual(len(self.alerts()), 3)
        self.assertEqual(self.env["mail.activity"].search_count([("res_model", "=", "efn.expiry.alert")]), activities)
        # Tahap naik (near -> markdown) membuka ulang + notifikasi baru.
        near.sudo().write({"state": "actioned"})
        lot_near.write(
            {
                "expiration_date": fields.Datetime.now() + timedelta(days=1),
                "removal_date": fields.Datetime.now() + timedelta(days=1),
            }
        )
        self.assertEqual(Alert._cron_efn_expiry_scan(), 1)
        self.assertEqual((near.stage, near.state), ("markdown", "open"))
        # Stok habis -> alert ditutup.
        self.put(lot_md, self.outlet_a, -5)
        Alert._cron_efn_expiry_scan()
        self.assertEqual(self.alerts().filtered(lambda a: a.lot_id == lot_md).state, "closed")

    def test_next_day_run_no_renotify_and_new_alert_after_close(self):
        """QA G45: idempotensi lintas hari. Alert terbuka kemarin hanya diperbarui (tanpa alert/To-Do baru); setelah
        ditutup (stok habis) lalu stok muncul lagi hari berikutnya -> alert baru bertanggal hari itu."""
        Alert = self.env["efn.expiry.alert"]
        lot = self.make_lot("EXP-DAY", 2)
        self.put(lot, self.outlet_a, 4)
        self.assertEqual(Alert._cron_efn_expiry_scan(), 1)
        today = fields.Date.context_today(Alert)
        yesterday = today - timedelta(days=1)

        def mine():
            return self.alerts().filtered(lambda a: a.lot_id == lot)

        mine().sudo().write({"date": yesterday, "last_run_date": yesterday})
        activities = self.env["mail.activity"].search_count([("res_model", "=", "efn.expiry.alert")])
        self.assertEqual(Alert._cron_efn_expiry_scan(), 0, "alert lama diperbarui, tidak diumumkan ulang")
        self.assertEqual((len(mine()), mine().last_run_date, mine().date), (1, today, yesterday))
        self.assertEqual(self.env["mail.activity"].search_count([("res_model", "=", "efn.expiry.alert")]), activities)
        # Stok habis -> closed; hari berikutnya stok kembali -> alert baru (kunci unik memakai tanggal deteksi).
        self.put(lot, self.outlet_a, -4)
        Alert._cron_efn_expiry_scan()
        self.assertEqual(mine().state, "closed")
        self.put(lot, self.outlet_a, 4)
        self.assertEqual(Alert._cron_efn_expiry_scan(), 1)
        self.assertEqual(sorted(mine().mapped("state")), ["closed", "open"])
        self.assertEqual(Alert._cron_efn_expiry_scan(), 0, "run ulang di hari yang sama tetap idempoten")
        self.assertEqual(len(mine()), 2)

    def test_notifications_store_head_and_buyer(self):
        self.put(self.make_lot("EXP-NOTIF", 2), self.outlet_a, 3)
        self.put(self.make_lot("EXP-NOTIF-B", 2), self.outlet_b, 3)
        self.env["efn.expiry.alert"]._cron_efn_expiry_scan()
        Activity = self.env["mail.activity"]
        head = Activity.search([("res_model", "=", "efn.expiry.alert"), ("user_id", "=", self.store_head.id)])
        self.assertEqual(len(head), 1, "satu To-Do per outlet yang dikelola kepala toko (outlet B bukan miliknya)")
        self.assertIn("EOA1", head.summary)
        buyer = Activity.search([("res_model", "=", "efn.expiry.alert"), ("user_id", "=", self.manager.id)])
        self.assertEqual(len(buyer), 1, "satu ringkasan per PT untuk Markdown Manager")
        # Cakupan record rule: kepala toko hanya melihat alert outletnya; manager semua outlet PT.
        self.assertEqual(self.env["efn.expiry.alert"].with_user(self.store_head).search([]).warehouse_id, self.outlet_a)
        self.assertEqual(
            set(self.env["efn.expiry.alert"].with_user(self.manager).search([]).warehouse_id.ids),
            {self.outlet_a.id, self.outlet_b.id},
        )

    def test_sec219_alert_fields_server_only(self):
        self.put(self.make_lot("EXP-GUARD", 2), self.outlet_a, 3)
        self.env["efn.expiry.alert"]._cron_efn_expiry_scan()
        alert = self.alerts().with_user(self.store_head)
        for vals in ({"stage": "expired"}, {"state": "actioned"}, {"warehouse_id": self.outlet_b.id}, {"qty": 0}):
            with self.subTest(vals=vals), self.assertRaisesRegex(AccessError, "server_field"):
                alert.write(vals)
        # Aksi resmi tetap jalan (sudo setelah cek); chatter/aktivitas boleh.
        alert.message_post(body="catatan kepala toko")
        alert.action_efn_close()
        self.assertEqual(alert.state, "closed")

    def test_flag_off_no_alert(self):
        self.env["ir.config_parameter"].sudo().set_param("efn_expiry_monitor.enabled", "false")
        self.put(self.make_lot("EXP-OFF", 2), self.outlet_a, 3)
        self.assertEqual(self.env["efn.expiry.alert"]._cron_efn_expiry_scan(), 0)
        self.assertFalse(self.alerts())

    def test_ce_product_expiry_activity_not_duplicated(self):
        past = fields.Datetime.now() - timedelta(days=1)
        managed = self.make_lot("EXP-CE-MANAGED", 5, alert_date=past)
        plain = self.make_lot("EXP-CE-PLAIN", 5, product=self.product_plain, alert_date=past)
        self.put(managed, self.outlet_a, 2)
        self.put(plain, self.outlet_a, 2)
        self.env["stock.lot"]._alert_date_exceeded()
        self.assertTrue(managed.product_expiry_reminded)
        self.assertFalse(managed.activity_ids, "lot terkelola EFN: tanpa To-Do CE (digantikan efn.expiry.alert)")
        self.assertTrue(plain.product_expiry_reminded)
        self.assertTrue(plain.activity_ids, "kategori tanpa ambang EFN tetap memakai perilaku CE")
