"""Field yang memenuhi syarat audit "semua field" (G22 / L-81): field mail.activity.mixin dikecualikan per NAMA,
field bisnis berprefiks ``activity_`` (mis. ``efn.loyalty.policy.activity_epoch``, L-75) tetap teraudit."""

from types import SimpleNamespace

from odoo.tests import TransactionCase, tagged

from odoo.addons.efn_audit.models.efn_audit_rule import NON_ELIGIBLE_NAMES, eligible_fields, is_eligible


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditEligible(TransactionCase):
    def test_business_field_with_activity_prefix_is_eligible(self):
        stored_date = SimpleNamespace(store=True, type="date")
        self.assertTrue(is_eligible("activity_epoch", stored_date))
        self.assertTrue(is_eligible("activity_anything_business", stored_date))

    def test_mail_activity_mixin_fields_excluded_by_name(self):
        stored_char = SimpleNamespace(store=True, type="char")
        for name in NON_ELIGIBLE_NAMES:
            with self.subTest(field=name):
                self.assertFalse(is_eligible(name, stored_char))
        self.assertFalse(is_eligible("message_ids", SimpleNamespace(store=True, type="many2many")))
        self.assertFalse(is_eligible("activity_epoch", SimpleNamespace(store=False, type="date")), "non-stored")

    def test_real_mixin_model_has_no_activity_fields_eligible(self):
        """Semua field mixin pada model nyata (res.partner mewarisi mail.activity.mixin) tidak lolos."""
        Partner = self.env["res.partner"]
        eligible = eligible_fields(Partner)
        mixin = {name for name in Partner._fields if name.startswith("activity_")}
        self.assertTrue(mixin, "res.partner harus punya field activity_* dari mixin")
        self.assertFalse(mixin & eligible, f"field mixin bocor ke audit: {sorted(mixin & eligible)}")
