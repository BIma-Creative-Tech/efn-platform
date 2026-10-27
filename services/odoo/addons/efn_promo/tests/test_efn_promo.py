"""efn_promo (docs/04 §18.1). Tag efn. Semua data = data uji."""

import base64
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

PNG_1PX = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    )
)


@tagged("post_install", "-at_install", "efn")
class TestEfnPromo(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        cls.other = env["res.company"].create({"name": "PT Uji Lain"})
        now = fields.Datetime.now()
        cls.now = now
        base = {"image": PNG_1PX, "date_start": now - timedelta(hours=1), "date_end": now + timedelta(days=3)}
        cls.banner = env["efn.promo.banner"].create(
            dict(base, name="B1", title="Promo roti", link_type="brand", link_brand="PB")
        )
        cls.ended = env["efn.promo.banner"].create(
            dict(base, name="B2", title="Lama", date_start=now - timedelta(days=5), date_end=now - timedelta(days=1))
        )
        cls.foreign = env["efn.promo.banner"].create(dict(base, name="B3", title="PT lain", company_id=cls.other.id))
        cls.popup = env["efn.promo.popup"].create(
            dict(
                base,
                name="P1",
                title="Diskon kopi",
                frequency="daily",
                push_enabled=True,
                push_title="Promo",
                push_body="Cek aplikasi",
            )
        )
        cls.bff = new_test_user(
            env,
            login="efn_promo_bff_uji",
            groups="base.group_user,efn_promo.group_promo_bff",
            company_id=cls.company.id,
        )
        cls.manager = new_test_user(
            env, login="efn_promo_mgr_uji", groups="efn_promo.group_promo_manager", company_id=cls.company.id
        )

    def feed(self, user=None):
        return self.env["efn.promo.banner"].with_user(user or self.bff).efn_promo_feed()

    def test_feed_whitelist_period_company(self):
        res = self.feed()
        ids = [b["id"] for b in res["banners"]]
        self.assertIn(self.banner.id, ids)
        self.assertNotIn(self.ended.id, ids, "promo berakhir tidak dikirim")
        self.assertNotIn(self.foreign.id, ids, "promo PT lain tidak dikirim")
        b = next(x for x in res["banners"] if x["id"] == self.banner.id)
        self.assertEqual(b["link"], {"type": "brand", "brand": "PB"})
        self.assertEqual(len(b["image_sha"]), 64)
        self.assertEqual(
            set(b),
            {
                "id",
                "title",
                "subtitle",
                "cta_label",
                "link",
                "web",
                "app",
                "date_start",
                "date_end",
                "sequence",
                "image_sha",
                "write_date",
            },
        )
        self.assertEqual(res["popups"][0]["frequency"], "daily")
        self.assertNotIn("push_body", res["popups"][0])

    def test_caller_checks(self):
        with self.assertRaises(AccessError):
            self.feed(self.manager)
        self.bff.sudo().write({"group_ids": [(4, self.env.ref("base.group_system").id)]})
        with self.assertRaises(AccessError):
            self.feed()

    def test_image_rpc(self):
        res = self.env["efn.promo.banner"].with_user(self.bff).efn_promo_image("banner", self.banner.id)
        self.assertEqual(res["sha"], self.banner.image_sha)
        self.assertEqual(res["mimetype"], "image/png")
        with self.assertRaisesRegex(UserError, "efn_promo:not_found"):
            self.env["efn.promo.banner"].with_user(self.bff).efn_promo_image("banner", self.foreign.id)
        with self.assertRaisesRegex(UserError, "efn_promo:invalid_input"):
            self.env["efn.promo.banner"].with_user(self.bff).efn_promo_image("x", self.banner.id)

    def test_push_queue_and_claim_once(self):
        claim = self.env["efn.promo.popup"].with_user(self.bff).efn_promo_push_claim
        self.assertEqual(claim(5), {"items": []}, "draft tidak diklaim")
        self.popup.with_user(self.manager).action_efn_queue_push()
        self.assertEqual(self.popup.push_state, "queued")
        items = claim(5)["items"]
        self.assertEqual([i["id"] for i in items], [self.popup.id])
        self.assertEqual(items[0]["push_title"], "Promo")
        self.assertEqual(self.popup.push_state, "sent")
        self.assertEqual(claim(5), {"items": []}, "sekali kirim")
        with self.assertRaises(UserError):
            self.popup.with_user(self.manager).action_efn_queue_push()

    def test_validation(self):
        Banner = self.env["efn.promo.banner"]
        base = {
            "name": "X",
            "title": "X",
            "image": PNG_1PX,
            "date_start": self.now,
            "date_end": self.now + timedelta(days=1),
        }
        with self.assertRaises(ValidationError):
            Banner.create(dict(base, date_end=self.now - timedelta(days=1)))
        with self.assertRaises(ValidationError):
            Banner.create(dict(base, link_type="url", link_url="http://x.test"))
        with self.assertRaises(ValidationError):
            Banner.create(dict(base, link_type="brand", link_brand="pb"))
        with self.assertRaises(ValidationError):
            Banner.create(dict(base, channel_web=False, channel_app=False))
        ok = Banner.create(dict(base, link_type="url", link_url="https://parisbaguette.co.id/promo"))
        self.assertEqual(ok._efn_promo_link(), {"type": "url", "url": "https://parisbaguette.co.id/promo"})

    def test_manager_company_rule(self):
        names = self.env["efn.promo.banner"].with_user(self.manager).search([]).mapped("name")
        self.assertIn("B1", names)
        self.assertNotIn("B3", names)
